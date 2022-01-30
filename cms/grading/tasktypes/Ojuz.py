
import logging
import os

from cms.db import Executable
from cms.grading.ParameterTypes import ParameterTypeCollection, \
    ParameterTypeChoice, ParameterTypeString
from cms.grading.languagemanager import LANGUAGES, get_language
from cms.grading.steps import compilation_step, evaluation_step, \
    human_evaluation_message
from . import TaskType, \
    check_executables_number, check_files_number, check_manager_present, \
    create_sandbox, delete_sandbox, eval_output, is_manager_for_compilation

from typing import Union, Tuple
import re, requests, time
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# returns (outcome, submission-link)
def submit(problem: str, source: str) -> Tuple[Union[int, None], str]:
	with requests.Session() as s:
		def post_csrf(url, data):
			# find token
			page = BeautifulSoup(s.get(url).text, "html.parser")
			data["csrf_token"] = page.find(id="csrf_token").get("value")
			# post request
			return s.post(url, data=data, headers={"Referer": url})

		# login
		post_csrf("https://oj.uz/login?next=%2F%3F", {
			"email": "c28dnv9q3", # or username
			"password": "dgAMDpzL2xb34qj4", # TODO
			"next": "/?"
		})

		# submit and fetch outcome
		submission = post_csrf("https://oj.uz/problem/submit/" + problem, {
			"code_1": source,
			"language": "9" # c++17
		}).url
		def wait_for_evaluation() -> Union[int, None]:
			submission_id = submission.rsplit('/', 1)[-1]
			poll_delay = 2.5 # seconds
			while True:
				time.sleep(poll_delay)
				page = BeautifulSoup(s.get(submission).text, "html.parser")
				progress = page.find(id="progressbar_text_" + submission_id).string
				if progress == "Compilation error":
					return None
				if re.match("\d+ / \d+$", progress) != None:
					return int(progress.split('/')[0])

		outcome = wait_for_evaluation()

		# logout :)
		s.get("https://oj.uz/logout")
	return (outcome, submission)



# Dummy function to mark translatable string.
def N_(message):
    return message


class Ojuz(TaskType):
    # Codename of the checker, if it is used.
    CHECKER_CODENAME = "checker"
    # Basename of the grader, used in the manager filename and as the main
    # class in languages that require us to specify it.
    GRADER_BASENAME = "grader"
    # Default input and output filenames when not provided as parameters.
    DEFAULT_INPUT_FILENAME = "input.txt"
    DEFAULT_OUTPUT_FILENAME = "output.txt"

    # Constants used in the parameter definition.
    OUTPUT_EVAL_DIFF = "diff"
    OUTPUT_EVAL_CHECKER = "comparator"
    COMPILATION_ALONE = "alone"
    COMPILATION_GRADER = "grader"

    # Other constants to specify the task type behaviour and parameters.
    ALLOW_PARTIAL_SUBMISSION = False

    _COMPILATION = ParameterTypeChoice(
        "Compilation",
        "compilation",
        "",
        {COMPILATION_ALONE: "Submissions are self-sufficient",
         COMPILATION_GRADER: "Submissions are compiled with a grader"})

    _USE_FILE = ParameterTypeCollection(
        "I/O (blank for stdin/stdout)",
        "io",
        "",
        [
            ParameterTypeString("Input file", "inputfile", ""),
            ParameterTypeString("Output file", "outputfile", ""),
        ])

    _EVALUATION = ParameterTypeChoice(
        "Output evaluation",
        "output_eval",
        "",
        {OUTPUT_EVAL_DIFF: "Outputs compared with white diff",
         OUTPUT_EVAL_CHECKER: "Outputs are compared by a comparator"})

    _OJUZ_KEY = ParameterTypeString(
        "Ojuz Task Name",
        "ojuz_task_name",
        "The task name in oj.zu, like IOI21_KEYS")

    ACCEPTED_PARAMETERS = [_COMPILATION, _USE_FILE, _EVALUATION, _OJUZ_KEY]

    @property
    def name(self):
        """See TaskType.name."""
        # TODO add some details if a grader/comparator is used, etc...
        return "Ojuz"

    def __init__(self, parameters):
        super().__init__(parameters)

        # Data in the parameters.
        self.compilation = self.parameters[0]
        self.input_filename, self.output_filename = self.parameters[1]
        self.output_eval = self.parameters[2]
        self.ojuz_key = self.parameters[3]

        # Actual input and output are the files used to store input and
        # where the output is checked, regardless of using redirects or not.
        self._actual_input = self.input_filename
        self._actual_output = self.output_filename
        if len(self.input_filename) == 0:
            self._actual_input = self.DEFAULT_INPUT_FILENAME
        if len(self.output_filename) == 0:
            self._actual_output = self.DEFAULT_OUTPUT_FILENAME

    def get_compilation_commands(self, submission_format):
        """See TaskType.get_compilation_commands."""
        codenames_to_compile = []
        if self._uses_grader():
            codenames_to_compile.append(self.GRADER_BASENAME + ".%l")
        codenames_to_compile.extend(submission_format)
        res = dict()
        for language in LANGUAGES:
            source_ext = language.source_extension
            executable_filename = self._executable_filename(submission_format,
                                                            language)
            res[language.name] = language.get_compilation_commands(
                [codename.replace(".%l", source_ext)
                 for codename in codenames_to_compile],
                executable_filename)
        return res

    def get_user_managers(self):
        """See TaskType.get_user_managers."""
        # In case the task uses a grader, we let the user provide their own
        # grader (which is usually a simplified grader provided by the admins).
        if self._uses_grader():
            return [self.GRADER_BASENAME + ".%l"]
        else:
            return []

    def get_auto_managers(self):
        """See TaskType.get_auto_managers."""
        return []

    def _uses_grader(self):
        return self.compilation == self.COMPILATION_GRADER

    def _uses_checker(self):
        return self.output_eval == self.OUTPUT_EVAL_CHECKER

    @staticmethod
    def _executable_filename(codenames, language):
        """Return the chosen executable name computed from the codenames.

        codenames ([str]): submission format or codename of submitted files,
            may contain %l.
        language (Language): the programming language of the submission.

        return (str): a deterministic executable name.

        """
        name =  "_".join(sorted(codename.replace(".%l", "")
                                for codename in codenames))
        return name + language.executable_extension

    def compile(self, job, file_cacher):
        """See TaskType.compile."""
        for codename, file_ in job.files.items():
            filename = codename.replace(".%l", "cpp")
            job.executables[filename] = Executable(filename, file_.digest)

        # Retrieve the compiled executables.
        job.success = True
        job.compilation_success = True
        job.text = ["N/A"]
        job.plus = {
            "stdout": "this is compile stdout",
            "stderr": "this is compile stderr",
        }


    def evaluate(self, job, file_cacher):
        """See TaskType.evaluate."""
        # Prepare the execution
        executable_filename = next(iter(job.executables.keys()))
        digest = job.executables[executable_filename].digest
        submission_content = file_cacher.get_file_content(digest)
        # print(submission_content)
        outcome, submission_link = submit(self.ojuz_key, submission_content)

        job.success = True
        if outcome is None:
            job.outcome = str(0.0)
            text_base = "Execution failed"
        else:
            job.outcome = str(outcome/100)
            text_base = "Execution completed successfully"

        job.text = [f"{text_base}. URL: {submission_link}"]
        job.plus = {
            "stdout": "this is evaluate stdout",
            "stderr": "this is evaluate stderr",
            "execution_time": 1.0,
            "execution_wall_clock_time": 1.1,
        }
