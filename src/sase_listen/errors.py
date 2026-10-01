"""Exit codes and error types. Owner: scaffold phase."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes shared by every command."""

    OK = 0
    UNEXPECTED = 1
    USAGE = 2
    CONFIG = 3
    SYNTHESIS_FAILED = 4
    QUALITY_GATE_FAILED = 5
    SCRIPT_STRUCTURAL = 6


class SaseListenError(Exception):
    """Base error carrying an exit code and an optional hint."""

    def __init__(
        self, message: str, code: ExitCode = ExitCode.UNEXPECTED, hint: str = ""
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
