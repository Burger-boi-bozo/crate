class JobError(ValueError):
    """A user-facing conversion failure with a stable machine-readable code."""

    def __init__(self, message: str, code: str = "source_error"):
        super().__init__(message)
        self.code = code
