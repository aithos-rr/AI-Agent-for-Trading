"""Tests for LLM exception hierarchy (PRD §8.2)."""

import pytest

from aiat.llm.exceptions import (
    LLMAuthError,
    LLMError,
    LLMParsingError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnrecoverableError,
)


def test_llm_error_is_base_exception() -> None:
    e = LLMError("base error")
    assert isinstance(e, Exception)
    assert str(e) == "base error"


def test_timeout_error_inherits_llm_error() -> None:
    e = LLMTimeoutError("timed out")
    assert isinstance(e, LLMError)
    assert isinstance(e, Exception)


def test_rate_limit_error_inherits_llm_error() -> None:
    e = LLMRateLimitError("rate limited")
    assert isinstance(e, LLMError)


def test_auth_error_inherits_llm_error() -> None:
    e = LLMAuthError("invalid key")
    assert isinstance(e, LLMError)


def test_parsing_error_inherits_llm_error() -> None:
    e = LLMParsingError("bad JSON")
    assert isinstance(e, LLMError)


def test_unrecoverable_error_carries_both_errors() -> None:
    primary = ValueError("primary fail")
    fallback = ValueError("fallback fail")
    e = LLMUnrecoverableError(primary_error=primary, fallback_error=fallback)
    assert isinstance(e, LLMError)
    assert e.primary_error is primary
    assert e.fallback_error is fallback
    assert "primary" in str(e)
    assert "fallback" in str(e)


def test_hierarchy_all_six_classes() -> None:
    classes = [
        LLMError,
        LLMTimeoutError,
        LLMRateLimitError,
        LLMAuthError,
        LLMParsingError,
        LLMUnrecoverableError,
    ]
    assert len(classes) == 6
    for cls in classes[1:]:
        assert issubclass(cls, LLMError)


def test_non_parsing_errors_not_confused() -> None:
    # Timeout, RateLimit, Auth are NOT LLMParsingError
    for cls in (LLMTimeoutError, LLMRateLimitError, LLMAuthError):
        assert not issubclass(cls, LLMParsingError)


def test_unrecoverable_not_raised_from_parsing() -> None:
    # Ensure LLMUnrecoverableError is NOT a subclass of LLMParsingError
    assert not issubclass(LLMUnrecoverableError, LLMParsingError)


def test_raise_and_catch_unrecoverable() -> None:
    primary = RuntimeError("p")
    fallback = RuntimeError("f")
    with pytest.raises(LLMUnrecoverableError) as exc_info:
        raise LLMUnrecoverableError(primary_error=primary, fallback_error=fallback)
    assert exc_info.value.primary_error is primary
