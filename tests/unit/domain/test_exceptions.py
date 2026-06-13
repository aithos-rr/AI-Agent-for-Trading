"""Tests for domain exception hierarchy (§7.1, §7.5)."""

from aiat.domain.exceptions import (
    AIATError,
    ContextBuildError,
    ExecutionRejectedError,
    ExecutionTimeoutError,
)


def test_aiat_error_is_exception() -> None:
    err = AIATError("base error")
    assert isinstance(err, Exception)
    assert str(err) == "base error"


def test_context_build_error_is_aiat_error() -> None:
    err = ContextBuildError("build failed")
    assert isinstance(err, AIATError)
    assert isinstance(err, Exception)


def test_execution_rejected_error_is_aiat_error() -> None:
    err = ExecutionRejectedError("guardrail blocked order")
    assert isinstance(err, AIATError)


def test_execution_timeout_error_is_aiat_error() -> None:
    err = ExecutionTimeoutError("order timed out")
    assert isinstance(err, AIATError)


def test_exceptions_can_be_raised_and_caught() -> None:
    import pytest

    with pytest.raises(AIATError):
        raise ContextBuildError("source unavailable")

    with pytest.raises(AIATError):
        raise ExecutionRejectedError("rejected")

    with pytest.raises(AIATError):
        raise ExecutionTimeoutError("timeout")
