from pathlib import Path


class CoreInfo:
    def __init__(self):
        # Will be set later in main()
        self.task_dir: Path = Path.cwd()
        self.gcc_args = ''
        self.latexmk_args = ''
        self.latex_additional_files = []

    @property
    def internal_dir(self) -> Path:
        return self.task_dir / '.aoi-temp'


# Object to store some global data
core = CoreInfo()


class CMSAOIError(Exception):
    """CMSAOIError is for exceptions that should halt execution without generating a stacktrace (managed errors)."""
    pass
