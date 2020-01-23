import argparse
import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import sys
from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union, Dict

import gevent
from voluptuous.humanize import humanize_error
import voluptuous as vol
import yaml
import yaml.constructor

from cms.db import test_db_connection
from cmscommon.constants import SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX
import cms.log
from cmscontrib.aoi.const import CONF_EXTENDS, CONF_GCC_ARGS, CONF_LATEX_CONFIG, CONF_LATEXMK_ARGS, \
    CONF_ADDITIONAL_FILES, CONF_NAME, CONF_TEST_SUBMISSIONS, CONF_SAMPLE_SOLUTION, CONF_SUBTASKS, CONF_TESTCASES, \
    CONF_OUTPUT, CONF_INPUT, CONF_SCORE_OPTIONS, CONF_STATEMENTS, CONF_ATTACHMENTS, CONF_FEEDBACK_LEVEL, CONF_LONG_NAME, \
    CONF_DECIMAL_PLACES, SCORE_MODES, CONF_MODE, CONF_GRADER, CONF_CHECKER, CONF_TYPE, CONF_POINTS, CONF_TASK_TYPE, \
    CONF_TIME_LIMIT, CONF_MEMORY_LIMIT, SCORE_TYPES, TASK_TYPES
from cmscontrib.aoi.core import core, CMSAOIError
from cmscontrib.aoi.rule import Rule, ShellRule
from cmscontrib.aoi.validation import CONFIG_SCHEMA
from cmscontrib.aoi.yaml_loader import load_yaml, AOITag

_LOGGER = logging.getLogger(__name__)


def recursive_visit(config, func):
    def visit(value):
        value = func(value)
        if isinstance(value, list):
            value = [visit(x) for x in value]
        elif isinstance(value, dict):
            value = {visit(k): visit(v) for k, v in value.items()}
        return value

    return visit(config)


def merge_visit(full_base, full_extends):
    def visit(base, extends):
        if extends is None:
            return base
        if isinstance(base, list):
            assert isinstance(extends, list)
            return [visit(x, y) for x, y in zip(base, extends)]
        elif isinstance(base, dict):
            assert isinstance(extends, dict)
            ret = extends.copy()
            for k, v in base.items():
                ret[k] = visit(v, extends.get(k))
            return ret
        elif base is None:
            return extends
        else:
            return base

    return visit(full_base, full_extends)


def load_yaml_with_extends(path: Path):
    config = load_yaml(path)
    if CONF_EXTENDS in config:
        extend_config = load_yaml(path.parent / Path(config[CONF_EXTENDS]))
        config = merge_visit(config, extend_config)
        config.pop(CONF_EXTENDS)
    return config


def main():
    # Try to set a better logging format
    try:
        from colorlog import ColoredFormatter
        root_logger = logging.getLogger()
        root_logger.handlers[0].setFormatter(ColoredFormatter(
                "%(log_color)s%(levelname)s%(reset)s %(message)s",
                datefmt='%H:%M:%S',
                reset=True,
                log_colors={
                    'DEBUG': 'cyan',
                    'INFO': 'green',
                    'WARNING': 'yellow',
                    'ERROR': 'red',
                    'CRITICAL': 'red',
                }
            ))
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Austrian CMS Task Upload System")
    parser.add_argument('-c', '--contest', help="The contest ID to add the task to (integer).",
                        type=int)
    parser.add_argument('-nt', '--no-tests', help="Don't run any submission tests.",
                        action='store_true')
    parser.add_argument('--test-user', help="The user (name) to upload test submissions with", type=str,
                        default='trainer')
    parser.add_argument('--clean', help="Clean the temporary directory before running.",
                        action='store_true')
    parser.add_argument('TASK_DIR', help="The directory of task to upload.")
    args = parser.parse_args()

    os.chdir(str(args.TASK_DIR))
    core.task_dir = Path(args.TASK_DIR).absolute()
    if args.clean and core.internal_dir.is_dir():
        shutil.rmtree(core.internal_dir)
    core.internal_dir.mkdir(exist_ok=True)

    try:
        return main_run(args) or 0
    except CMSAOIError as err:
        _LOGGER.error(str(err))
        return 1


def main_run(args):
    # Load config
    task_file = core.task_dir / 'task.yaml'
    _LOGGER.info("Reading task config %s", task_file)

    try:
        config = load_yaml_with_extends(task_file)
    except yaml.YAMLError as err:
        _LOGGER.error("Invalid YAML syntax:")
        _LOGGER.error(str(err))
        return 1

    # Validate config
    try:
        config = CONFIG_SCHEMA(config)
    except vol.Invalid as err:
        _LOGGER.error("Invalid configuration:")
        _LOGGER.error(humanize_error(config, err))
        raise CMSAOIError() from err

    core.gcc_args = config[CONF_GCC_ARGS]
    latex_config = config[CONF_LATEX_CONFIG]
    core.latexmk_args = latex_config[CONF_LATEXMK_ARGS]
    core.latex_additional_files = [Path(x) for x in latex_config[CONF_ADDITIONAL_FILES]]

    # Find rules (stuff to be executed) in config and replace them by the resulting filename
    all_rules, config = find_rules(config)

    # Execute all rules synchronously
    for rule in all_rules.values():
        rule.ensure()

    try:
        test_db_connection()
    except cms.conf.ConfigError as err:
        raise CMSAOIError(f"Database is offline: {err}") from err

    from cms.db.filecacher import FileCacher

    file_cacher = FileCacher()

    def put_file(path: Union[str, Path], description):
        if isinstance(path, str):
            path = Path(path)
        return file_cacher.put_file_from_path(str(path), description)

    task = construct_task(config, put_file)

    # Commit changes
    commit_task(task, args.contest)

    if not args.no_tests:
        if not run_test_submissions(args, config, put_file):
            return 1
    return 0


def run_test_submissions(args, config, put_file):
    from cms.db import SessionGen, Task, Participation, User, Submission, File, SubmissionResult
    from cms.grading.languagemanager import filename_to_language
    from cms import ServiceCoord
    from cms.io import RemoteServiceClient

    _LOGGER.info("Uploading test submissions:")

    name = config[CONF_NAME]
    with SessionGen() as session:
        # Re-fetch task (cannot use task object after session closed)
        task: Task = session.query(Task).filter(Task.name == name).one()

        query = session.query(Participation).join(Participation.user) \
            .filter(User.username == args.test_user)
        if task.contest is not None:
            query = query.filter(Participation.contest_id == task.contest_id)
        participation = query.first()
        if participation is None:
            raise CMSAOIError(f"Test user {args.test_user} for uploading test submissions does not exist "
                              f"in contest {task.contest_id}")

        if task.contest is None:
            # Set contest of the task to the trainer user contest
            task.contest = participation.contest

        # Upload test submissions
        submissions = []
        for path, points in config[CONF_TEST_SUBMISSIONS].items():
            digest = put_file(path, f"Test submission file {path} for {name}")

            submission = Submission(timestamp=datetime.utcnow(), language=filename_to_language(path).name,
                                    participation=participation, task=task)
            session.add(File(filename=f'{task.name}.%l', digest=digest, submission=submission))
            session.add(submission)
            _LOGGER.info("  - Submission %s for %s points", path, points)
            submissions.append((path, submission, points))
        session.commit()
        # Change submissions array to use submission ID (can't use the object after session closed)
        submissions = [(x, sub.id, z) for x, sub, z in submissions]

    # Connect to Evaluation service and notify of new submission
    _LOGGER.info("Submitting submissions to EvaluationService")
    rs = RemoteServiceClient(ServiceCoord("EvaluationService", 0))
    rs.connect()
    # Wait until connected (and use gevent.sleep to let the greenlet run)
    while not rs.connected:
        gevent.sleep(1)
    for path, subid, points in submissions:
        rs.new_submission(submission_id=subid)
    # Wait a bit to let rs greenlet run (timing issues)
    gevent.sleep(1)
    rs.disconnect()

    _LOGGER.info("Waiting for submissions to be evaluated")

    # Store which submissions have already been scored
    seen = set()
    failed = False
    while True:
        gevent.sleep(1)
        # Recreate session each time (otherwise we'd constantly be seeing the "old" state)
        with SessionGen() as session:
            for path, subid, points in submissions:
                if subid in seen:
                    continue
                # Query database for submission result
                ret: Optional[SubmissionResult] = session.query(SubmissionResult) \
                    .join(SubmissionResult.submission) \
                    .filter(Submission.id == subid) \
                    .join(Submission.task) \
                    .filter(SubmissionResult.filter_scored()).first()
                if ret is None:
                    # Submission has not been scored yet
                    break
                else:
                    if ret.score != points:
                        _LOGGER.warning("%s does not have correct score! Expected %sP but returned %sP",
                                        path, points, ret.score)
                        failed = True
                    else:
                        _LOGGER.info("%s test passed successfully!", path)
                    seen.add(subid)
            else:
                # All submissions scored
                break
    return not failed


def commit_task(task, contest_id):
    from cms.db import SessionGen, Task, Contest
    from cmscontrib.importing import update_task

    with SessionGen() as session:
        # Find existing task (by name)
        old_task = session.query(Task).filter(Task.name == task.name).first()
        if old_task is None:
            # No task with matching name yet, add it as a new one
            _LOGGER.info("Adding task to database")
            session.add(task)
        else:
            # Task already exists, update the object dynamically
            _LOGGER.info("Updating task with ID %s", old_task.id)
            update_task(old_task, task)

        if contest_id is not None:
            contest = session.query(Contest).filter(Contest.id == contest_id).first()
            if contest is None:
                raise CMSAOIError(f"Could not find a contest with ID {contest_id}")
            if contest.id != task.contest_id:
                _LOGGER.info("Adding task to contest %s", contest.id)
                task.contest_id = contest.id
        # Commit changes
        session.commit()


def find_rules(config):
    all_rules: Dict[Path, Rule] = {}

    def register_rule(rule: Rule):
        outfile = rule.output_file.absolute()
        all_rules[outfile] = rule
        for dep in rule.dependencies:
            # Add dependencies to all_rules table too
            register_rule(dep)
        return str(outfile.relative_to(core.task_dir))

    def visit_item(value):
        if isinstance(value, AOITag):
            # Replace with output file path
            rule = value.rule_type(value.value, run_entropy=f'{len(all_rules)}',
                                   base_directory=value.base_directory)
            return str(register_rule(rule))
        return value

    # Recursively visit all parts of config to find all rules to be evaluated
    config = recursive_visit(config, visit_item)
    # Compile output for each testcase with sample solution
    if CONF_SAMPLE_SOLUTION in config:
        sample_solution = config[CONF_SAMPLE_SOLUTION]
        sample_sol_file = Path(sample_solution).absolute()
        sample_sol_rule = all_rules[sample_sol_file]
        for subtask in config[CONF_SUBTASKS]:
            for testcase in subtask[CONF_TESTCASES]:
                if CONF_OUTPUT in testcase:
                    # Output already exists
                    continue
                inp = Path(testcase[CONF_INPUT]).absolute()
                # Add sample solution program as dependency
                deps = [sample_sol_rule]
                if inp in all_rules:
                    # Add input as dependency
                    deps.append(all_rules[inp])
                rule = ShellRule(str(Path(sample_sol_file)), stdin_file=inp,
                                 dependencies=deps, base_directory=core.task_dir)
                testcase[CONF_OUTPUT] = register_rule(rule)
    return all_rules, config


def construct_task(config, put_file):
    from cms.db import Statement, Attachment, Manager, Testcase, Dataset, Task
    from cms import FEEDBACK_LEVEL_FULL, FEEDBACK_LEVEL_RESTRICTED

    _LOGGER.info("Task config:")

    name = config[CONF_NAME]
    _LOGGER.info("  - Name: %s", name)
    long_name = config[CONF_LONG_NAME]
    _LOGGER.info("  - Long Name: %s", long_name)
    _LOGGER.info("")

    score_opt = config[CONF_SCORE_OPTIONS]
    # Upload statements
    statements = {}
    for lang, pdf in config[CONF_STATEMENTS].items():
        digest = put_file(pdf, f"Statement for task {name} (lang: {lang})")
        statements[lang] = Statement(language=lang, digest=digest)
        _LOGGER.info("  - Statement for language %s: %s", lang, pdf)
    if not statements:
        _LOGGER.info("  - No task statements!")

    args = {}
    # If there's only one statement, mark it as the primary statement
    if len(statements) == 1:
        args['primary_statements'] = [next(iter(statements.keys()))]
        _LOGGER.info("  - Primary statement: %s", args['primary_statements'][0])

    # Upload attachments (if any)
    attachments = {}
    for fname, attachment in config[CONF_ATTACHMENTS].items():
        digest = put_file(attachment, f"Attachment {fname} for task {name}")
        attachments[attachment] = Attachment(filename=fname, digest=digest)
        _LOGGER.info("  - Attachment %s: %s", fname, attachment)
    if not attachments:
        _LOGGER.info("  - No task attachments!")

    _LOGGER.info("")

    # Submission format (what the uploaded files are to be called, .%l is replaced by file suffix)
    submission_format = [f'{name}.%l']
    _LOGGER.info("  - Submission format: '%s'", submission_format[0])
    feedback_level = {
        'FULL': FEEDBACK_LEVEL_FULL,
        'RESTRICTED': FEEDBACK_LEVEL_RESTRICTED,
    }[config[CONF_FEEDBACK_LEVEL]]
    _LOGGER.info("  - Feedback level: %s", feedback_level)

    score_precision = score_opt[CONF_DECIMAL_PLACES]
    _LOGGER.info("  - Score precision: %s", score_precision)

    score_mode = SCORE_MODES[score_opt[CONF_MODE]]
    _LOGGER.info("  - Score mode: %s", score_mode)

    task = Task(
        name=name, title=long_name, submission_format=submission_format,
        feedback_level=feedback_level, score_precision=score_precision, score_mode=score_mode,
        statements=statements, attachments=attachments, **args
    )

    _LOGGER.info("")

    # Construct dataset
    # Managers = additional files attached to the dataset (checker, grader files)
    managers = []
    # How the submission is compiled (alone or with additional grader files)
    compilation_param = 'alone'
    for grader in config[CONF_GRADER]:
        # Add grader (files that are compiled together with the user's file)
        suffix = Path(grader).suffix
        digest = put_file(grader, f"Grader for task {name} and ext {suffix}")
        managers.append(Manager(filename=f'grader{suffix}', digest=digest))
        _LOGGER.info("  - Grader: %s", grader)
        compilation_param = 'grader'
    if not config[CONF_GRADER]:
        _LOGGER.info("  - No graders, submission is compiled directly.")

    if CONF_CHECKER in config:
        # Check submissions with a checker - a program that is called with parameters:
        #  <INPUT_FILE> <CONTESTANT_OUTPUT> <OUTPUT_FILE>
        # Should print a number from 0.0 (incorrect) to 1.0 (correct)
        digest = put_file(config[CONF_CHECKER], f'Manager for task {name}')
        managers.append(Manager(filename='checker', digest=digest))
        evaluation_param = "comparator"
        _LOGGER.info("  - Testcase output is checked by checker %s", config[CONF_CHECKER])
    else:
        # No checker, validate output with a simple diff (ignoring whitespace)
        evaluation_param = "diff"
        _LOGGER.info("  - Testcase output is checked with an output diff.")

    subtasks = config[CONF_SUBTASKS]
    # Score type: How scores of the individual testcases are combined to the score of a submission
    score_type = score_opt[CONF_TYPE]
    if score_type == 'SUM':
        # Sum score type, add points of all testcases together
        score_type_params = sum(x[CONF_POINTS] for x in subtasks)
        _LOGGER.info("  - Score is computed by a SUM of all testcases.")
    elif score_type == 'GROUP_MIN':
        # Group min - For each subtask, multiply lowest testcase result with a fixed number of points
        # In practice means a subtask gets points iff all testcases finish successfully
        score_type_params = [(subt[CONF_POINTS], len(subt[CONF_TESTCASES])) for subt in subtasks]
        _LOGGER.info("  - Score is computed by the sum of the minimum score across each subtask.")
    else:
        # Other score types not implemented yet
        raise NotImplementedError

    _LOGGER.info("")

    # Upload testcases
    testcases = []
    for i, subtask in enumerate(subtasks, start=1):
        _LOGGER.info("  - Subtask %s worth %s points:", i, subtask[CONF_POINTS])
        for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
            input_digest = put_file(testcase[CONF_INPUT], f"Input {j} for task {name}")
            output_digest = put_file(testcase[CONF_OUTPUT], f"Output {j} for task {name}")
            testcases.append(Testcase(codename=f'{i:02d}_{j:02d}', public=True,
                                      input=input_digest, output=output_digest))
            _LOGGER.info("    - Testcase %s: Input %s, Output %s",
                         j, testcase[CONF_INPUT], testcase[CONF_OUTPUT])

    _LOGGER.info("")

    if config[CONF_TASK_TYPE] == "BATCH":
        # Batch task type, user program is called and a checker (or whitespace diff) is perfomed on output
        # to determine outcome
        task_type_params = [compilation_param, ['', ''], evaluation_param]
        _LOGGER.info("  - Task Type: Batch")
    else:
        raise NotImplementedError

    time_limit = config[CONF_TIME_LIMIT]
    _LOGGER.info("  - Time limit: %s s", time_limit)
    memory_limit = int(config[CONF_MEMORY_LIMIT])
    _LOGGER.info("  - Memory limit: %s MiB", memory_limit)
    _LOGGER.info("")

    dataset = Dataset(
        task=task, description="Default",
        # managers+testcases are mapped to filename/codename
        managers={m.filename: m for m in managers}, testcases={tc.codename: tc for tc in testcases},
        time_limit=time_limit, memory_limit=memory_limit,
        task_type=TASK_TYPES[config[CONF_TASK_TYPE]], score_type=SCORE_TYPES[score_type],
        task_type_parameters=task_type_params, score_type_parameters=score_type_params
    )
    # Set dataset as the active one
    task.active_dataset = dataset
    return task


if __name__ == '__main__':
    sys.exit(main() or 0)
