import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union, Dict

import gevent
from voluptuous.humanize import humanize_error
import voluptuous as vol
import yaml
import yaml.constructor

from cms.db import test_db_connection
import cms.log
from cmscontrib.aoi.const import CONF_EXTENDS, CONF_GCC_ARGS, CONF_LATEX_CONFIG, CONF_LATEXMK_ARGS, \
    CONF_ADDITIONAL_FILES, CONF_NAME, CONF_TEST_SUBMISSIONS, CONF_SAMPLE_SOLUTION, CONF_SUBTASKS, CONF_TESTCASES, \
    CONF_OUTPUT, CONF_INPUT, CONF_SCORE_OPTIONS, CONF_STATEMENTS, CONF_ATTACHMENTS, CONF_FEEDBACK_LEVEL, CONF_LONG_NAME, \
    CONF_DECIMAL_PLACES, SCORE_MODES, CONF_MODE, CONF_GRADER, CONF_CHECKER, CONF_TYPE, CONF_POINTS, CONF_TASK_TYPE, \
    CONF_TIME_LIMIT, CONF_MEMORY_LIMIT, SCORE_TYPES, TASK_TYPES, CONF_CPP_CONFIG, CONF_PUBLIC, \
    CONF_TOKENS, CONF_INITIAL, CONF_GEN_NUMBER, TOKEN_MODES, CONF_MANAGER, CONF_NUM_PROCESSES, \
    CONF_CODENAME
from cmscontrib.aoi.core import core, CMSAOIError
from cmscontrib.aoi.rule import Rule, ShellRule, EmptyRule
from cmscontrib.aoi.util import copytree, copy_if_necessary
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
            if not isinstance(extends, list):
                return base
            return [visit(x, y) for x, y in zip(base, extends)]
        elif isinstance(base, dict):
            if not isinstance(extends, dict):
                return base
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
    parser.add_argument('--only-build', help="Only build files, do not upload them.",
                        action='store_true')
    parser.add_argument('TASK_DIR', help="The directory of task to upload.")
    args = parser.parse_args()

    os.chdir(str(args.TASK_DIR))
    core.task_dir = Path.cwd()
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

    if not task_file.is_file():
        raise CMSAOIError(f"task.yaml file {task_file} does not exist!")

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

    latex_config = config[CONF_LATEX_CONFIG]
    core.latexmk_args = latex_config[CONF_LATEXMK_ARGS]
    core.latex_additional_files = [Path(x) for x in latex_config[CONF_ADDITIONAL_FILES]]
    core.gcc_args = config[CONF_CPP_CONFIG][CONF_GCC_ARGS]
    core.config = config
    try:
        from cms.db import SessionGen, Contest
        if args.contest is not None:
            with SessionGen() as session:
                contest = session.query(Contest).filter(Contest.id == args.contest).one()
                core.contest_name = contest.description
    except Exception as e:
        _LOGGER.warning("Could not determine contest name from ID - latex won't automatically set in latex header (%s)",
                        e, exc_info=0)

    copytree(core.task_dir, core.internal_build_dir, ignore={core.internal_dir})

    # Find rules (stuff to be executed) in config and replace them by the resulting filename
    all_rules, config = find_rules(config)
    core.config = config

    # Execute all rules synchronously
    for rule in all_rules.values():
        rule.ensure()

    # Copy results to new directory (so that testcases can be searched more easily)
    core.result_dir.mkdir(exist_ok=True)
    for i, subtask in enumerate(config[CONF_SUBTASKS], start=1):
        for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
            copy_if_necessary(Path(testcase[CONF_INPUT]),
                              core.result_dir / f'{i:02d}_{j:02d}.in')
            copy_if_necessary(Path(testcase[CONF_OUTPUT]),
                              core.result_dir / f'{i:02d}_{j:02d}.out')
    for lang, statement in config[CONF_STATEMENTS].items():
        copy_if_necessary(Path(statement), core.result_dir / f'{lang}.pdf')
    for fname, attachment in config[CONF_ATTACHMENTS].items():
        copy_if_necessary(Path(attachment), core.result_dir / fname)
    if CONF_CHECKER in config:
        copy_if_necessary(Path(config[CONF_CHECKER]), core.result_dir / 'checker')
    for grader in config[CONF_GRADER]:
        copy_if_necessary(Path(grader), core.result_dir / Path(grader).name)
    if CONF_SAMPLE_SOLUTION in config:
        copy_if_necessary(Path(config[CONF_SAMPLE_SOLUTION]), core.result_dir / 'samplesol')

    if args.only_build:
        return 0

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

    task = construct_task(config, all_rules, put_file)

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

    if CONF_TEST_SUBMISSIONS not in config:
        return True

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
            comment = f"Test {Path(path).name} for {points}P"
            submission = Submission(timestamp=datetime.utcnow(), language=filename_to_language(path).name,
                                    participation=participation, task=task, comment=comment)
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
                if task.num is None:
                    task.num = len(contest.tasks)
        # Commit changes
        session.commit()


def lookup_friendly_filename(all_rules, fname):
    path = Path(fname).absolute()
    if path in all_rules:
        return all_rules[path].name
    return fname


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
            name = f'{value.tag} {value.value}'.replace('\n', ' ')[:32]

            rule = value.rule_type(value.value, run_entropy=f'{len(all_rules)}',
                                   base_directory=value.base_directory,
                                   name=name)
            return str(register_rule(rule))
        return value

    # Recursively visit all parts of config to find all rules to be evaluated
    config = recursive_visit(config, visit_item)
    # Compile output for each testcase with sample solution
    if CONF_SAMPLE_SOLUTION in config:
        sample_solution = config[CONF_SAMPLE_SOLUTION]
        sample_sol_file = Path(sample_solution).absolute()
        sample_sol_rule = all_rules[sample_sol_file]
        for i, subtask in enumerate(config[CONF_SUBTASKS], start=1):
            for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
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
                                 dependencies=deps, base_directory=core.task_dir,
                                 name=f'samplesol {i:02d}_{j:02d}')
                testcase[CONF_OUTPUT] = register_rule(rule)
    else:
        for i, subtask in enumerate(config[CONF_SUBTASKS], start=1):
            for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
                if CONF_OUTPUT in testcase:
                    # Output already exists
                    continue
                rule = EmptyRule(name=f"empty {i:02d}_{j:02d}")
                testcase[CONF_OUTPUT] = register_rule(rule)
    return all_rules, config


def construct_task(config, all_rules, put_file):
    from cms.db import Statement, Attachment, Manager, Testcase, Dataset, Task
    from cms import FEEDBACK_LEVEL_FULL, FEEDBACK_LEVEL_RESTRICTED

    _LOGGER.info("Task config:")

    name = config[CONF_NAME]
    _LOGGER.info("  - Name: %s", name)
    long_name = config[CONF_LONG_NAME]
    _LOGGER.info("  - Long Name: %s", long_name)
    _LOGGER.info("")

    score_opt = config[CONF_SCORE_OPTIONS]
    # ================ STATEMENTS ================
    statements = {}
    for lang, pdf in config[CONF_STATEMENTS].items():
        digest = put_file(pdf, f"Statement for task {name} (lang: {lang})")
        statements[lang] = Statement(language=lang, digest=digest)
        _LOGGER.info("  - Statement for language %s: '%s'", lang, lookup_friendly_filename(all_rules, pdf))
    if not statements:
        _LOGGER.info("  - No task statements!")

    args = {}
    # If there's only one statement, mark it as the primary statement
    if len(statements) == 1:
        args['primary_statements'] = [next(iter(statements.keys()))]
        _LOGGER.info("  - Primary statement: %s", args['primary_statements'][0])

    # ================ ATTACHMENTS ================
    attachments = {}
    for fname, attachment in config[CONF_ATTACHMENTS].items():
        digest = put_file(attachment, f"Attachment {fname} for task {name}")
        attachments[attachment] = Attachment(filename=fname, digest=digest)
        _LOGGER.info("  - Attachment %s: '%s'", fname, lookup_friendly_filename(all_rules, attachment))
    if not attachments:
        _LOGGER.info("  - No task attachments!")
    _LOGGER.info("")

    # ================ SUBMISSION FORMAT ================
    # Submission format (what the uploaded files are to be called, .%l is replaced by file suffix)
    submission_format = [f'{name}.%l']
    if config[CONF_TASK_TYPE] == "OUTPUT_ONLY":
        # Output only has file for each testcase
        submission_format.clear()
        for i, subtask in enumerate(subtasks, start=1):
            for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
                codename = testcase.get(CONF_CODENAME, f'{i:02d}_{j:02d}')
                submission_format.append(f'output_{codename}.txt')
    _LOGGER.info("  - Submission format: '%s'", ', '.join(submission_format))

    # ================ FEEDBACK LEVEL / SCORING ================
    feedback_level = {
        'FULL': FEEDBACK_LEVEL_FULL,
        'RESTRICTED': FEEDBACK_LEVEL_RESTRICTED,
    }[config[CONF_FEEDBACK_LEVEL]]
    _LOGGER.info("  - Feedback level: %s", feedback_level)

    score_precision = score_opt[CONF_DECIMAL_PLACES]
    _LOGGER.info("  - Score precision: %s", score_precision)

    score_mode = SCORE_MODES[score_opt[CONF_MODE]]
    _LOGGER.info("  - Score mode: %s", score_mode)

    tokens = config[CONF_TOKENS]

    task = Task(
        name=name, title=long_name, submission_format=submission_format,
        feedback_level=feedback_level, score_precision=score_precision, score_mode=score_mode,
        statements=statements, attachments=attachments,
        token_mode=TOKEN_MODES[tokens[CONF_MODE]], token_gen_initial=tokens[CONF_INITIAL],
        token_gen_number=tokens[CONF_GEN_NUMBER],
        **args
    )

    _LOGGER.info("")

    # ================ DATASET ================
    # Managers = additional files attached to the dataset (checker, grader files)
    managers = []

    # ================ GRADER ================
    # How the submission is compiled (alone or with additional grader files)
    compilation_param = 'alone'
    for grader in config[CONF_GRADER]:
        # Add grader (files that are compiled together with the user's file)
        grader_path = Path(grader)
        suffix = grader_path.suffix
        digest = put_file(grader, f"Grader for task {name} and ext {suffix}")
        fname = grader_path.name
        if grader_path.suffix == '.cpp':
            if config[CONF_TASK_TYPE] == "BATCH":
                fname = 'grader.cpp'
            elif isinstance(config[CONF_TASK_TYPE], dict) and config[CONF_TASK_TYPE].get(CONF_TYPE) == 'COMMUNICATION':
                fname = 'stub.cpp'
            else:
                return
        managers.append(Manager(filename=fname, digest=digest))
        _LOGGER.info("  - Grader: '%s' (as %s)", grader, fname)
        compilation_param = 'grader'
    if not config[CONF_GRADER]:
        _LOGGER.info("  - No graders, submission is compiled directly.")

    # ================ CHECKER ================
    if CONF_CHECKER in config:
        # Check submissions with a checker - a program that is called with parameters:
        #  <INPUT_FILE> <CONTESTANT_OUTPUT> <OUTPUT_FILE>
        # Should print a number from 0.0 (incorrect) to 1.0 (correct)
        digest = put_file(config[CONF_CHECKER], f'Manager for task {name}')
        managers.append(Manager(filename='checker', digest=digest))
        evaluation_param = "comparator"
        _LOGGER.info("  - Testcase output is checked by checker '%s'",
                     lookup_friendly_filename(all_rules, config[CONF_CHECKER]))
    else:
        # No checker, validate output with a simple diff (ignoring whitespace)
        evaluation_param = "diff"
        _LOGGER.info("  - Testcase output is checked with an output diff.")

    subtasks = config[CONF_SUBTASKS]

    # ================ SCORE TYPE ================
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

    # ================ TESTCASES ================
    testcases = []
    for i, subtask in enumerate(subtasks, start=1):
        _LOGGER.info("  - Subtask %s worth %s points:", i, subtask[CONF_POINTS])
        for j, testcase in enumerate(subtask[CONF_TESTCASES], start=1):
            input_digest = put_file(testcase[CONF_INPUT], f"Input {j} for task {name}")
            output_digest = put_file(testcase[CONF_OUTPUT], f"Output {j} for task {name}")
            codename = testcase.get(CONF_CODENAME, f'{i:02d}_{j:02d}')

            tc = Testcase(codename=codename, public=testcase[CONF_PUBLIC],
                          input=input_digest, output=output_digest)
            testcases.append(tc)

            _LOGGER.info("    - Testcase %s: Input '%s', Output '%s'",
                         codename, lookup_friendly_filename(all_rules, testcase[CONF_INPUT]),
                         lookup_friendly_filename(all_rules, testcase[CONF_OUTPUT]))
        _LOGGER.info("")
    _LOGGER.info("")

    # ================ TASK TYPE ================
    if config[CONF_TASK_TYPE] == "BATCH":
        # Batch task type, user program is called and a checker (or whitespace diff) is perfomed on output
        # to determine outcome
        task_type_params = [
            # compiled alone (`alone`) or with grader (`grader`)
            compilation_param,
            # I/O, empty for stdin/stdout. Otherwise filenames for input/output files
            ['', ''],
            # Evaluated by white-diff (`diff`) or with checker (`comparator`)
            evaluation_param
        ]
        task_type = 'Batch'
    elif config[CONF_TASK_TYPE] == "OUTPUT_ONLY":
        task_type_params = [
            # Evaluated by white-diff (`diff`) or with checker (`comparator`)
            evaluation_param
        ]
        task_type = 'OutputOnly'
    elif isinstance(config[CONF_TASK_TYPE], dict):
        conf = config[CONF_TASK_TYPE]
        if conf.get(CONF_TYPE) == 'COMMUNICATION':
            task_type_params = [
                # Number of user processes spawned
                conf[CONF_NUM_PROCESSES],
                # compiled alone (`alone`) or with grader (`stub`)
                'stub' if compilation_param == 'grader' else 'alone',
                # User I/O on stdin/out (std_io) or via fifos (fifo_io)
                'std_io'
            ]
            digest = put_file(conf[CONF_MANAGER], f'Communication manager for task {name}')
            managers.append(Manager(filename='manager', digest=digest))
            task_type = 'Communication'
        else:
            raise NotImplementedError
    else:
        raise NotImplementedError
    _LOGGER.info("  - Task Type: %s", task_type)

    # ================ LIMITS ================
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
        task_type=task_type, score_type=SCORE_TYPES[score_type],
        task_type_parameters=task_type_params, score_type_parameters=score_type_params
    )
    # Set dataset as the active one
    task.active_dataset = dataset
    return task


if __name__ == '__main__':
    sys.exit(main() or 0)
