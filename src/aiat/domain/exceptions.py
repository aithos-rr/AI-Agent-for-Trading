"""Domain exception hierarchy (§7.1, §7.5, §12 M1)."""


class AIATError(Exception):
    """Base exception for all AIAT domain errors."""


class ContextBuildError(AIATError):
    """Raised when the context build fails (timeout, source unavailable, persist failure)."""


class ExecutionRejectedError(AIATError):
    """Raised when an execution is rejected by guardrails or the exchange."""


class ExecutionTimeoutError(AIATError):
    """Raised when an execution order exceeds the hard timeout."""
