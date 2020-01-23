from cmscommon.constants import SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX

FEEDBACK_LEVELS = ['RESTRICTED', 'FULL']
SCORE_MODES = {
    'SUM_SUBTASK_BEST': SCORE_MODE_MAX_SUBTASK,
    'MAX': SCORE_MODE_MAX,
}
SCORE_TYPES = {
    'GROUP_MIN': 'GroupMin',
    'GROUP_MUL': 'GroupMul',
    'GROUP_THRESHOLD': 'GroupThreshold',
    'SUM': 'Sum',
}
TASK_TYPES = {
    'BATCH': 'Batch',
    'OUTPUT_ONLY': 'OutputOnly',
}


CONF_NAME = 'name'
CONF_LONG_NAME = 'long_name'
CONF_ATTRIBUTION = 'attribution'
CONF_AUTHOR = 'author'
CONF_USES = 'uses'
CONF_STATEMENTS = 'statements'
CONF_ATTACHMENTS = 'attachments'
CONF_FEEDBACK_LEVEL = 'feedback_level'
CONF_SCORE_OPTIONS = 'score_options'
CONF_DECIMAL_PLACES = 'decimal_places'
CONF_MODE = 'mode'
CONF_TYPE = 'type'
CONF_TIME_LIMIT = 'time_limit'
CONF_MEMORY_LIMIT = 'memory_limit'
CONF_SAMPLE_SOLUTION = 'sample_solution'
CONF_GRADER = 'grader'
CONF_TASK_TYPE = 'task_type'
CONF_SUBTASKS = 'subtasks'
CONF_POINTS = 'points'
CONF_TESTCASES = 'testcases'
CONF_INPUT = 'input'
CONF_OUTPUT = 'output'
CONF_CHECKER = 'checker'
CONF_TEST_SUBMISSIONS = 'test_submissions'
CONF_EXTENDS = 'extends'
CONF_GCC_ARGS = 'gcc_args'
CONF_LATEX_CONFIG = 'latex_config'
CONF_LATEXMK_ARGS = 'latexmk_args'
CONF_ADDITIONAL_FILES = 'additional_files'
