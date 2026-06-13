"""Tests for isinstance()-based exception classification (PRD §8.2 fix B.18, ADR D3)."""

from unittest.mock import MagicMock

import anthropic
import openai

from aiat.llm.structured import _is_auth_error, _is_rate_limit_error

# ---------------------------------------------------------------------------
# isinstance() primary checks — OpenAI SDK classes
# ---------------------------------------------------------------------------


def test_openai_rate_limit_isinstance() -> None:
    """openai.RateLimitError is detected via isinstance() before string matching."""
    err = openai.RateLimitError(
        message="rate limit",
        response=MagicMock(headers={}),
        body={"error": {"message": "rate limit"}},
    )
    assert _is_rate_limit_error(err)


def test_openai_auth_error_isinstance() -> None:
    err = openai.AuthenticationError(
        message="invalid api key",
        response=MagicMock(headers={}),
        body={"error": {"message": "invalid api key"}},
    )
    assert _is_auth_error(err)


def test_openai_permission_denied_isinstance() -> None:
    err = openai.PermissionDeniedError(
        message="forbidden",
        response=MagicMock(headers={}),
        body={"error": {"message": "forbidden"}},
    )
    assert _is_auth_error(err)


# ---------------------------------------------------------------------------
# isinstance() primary checks — Anthropic SDK classes
# ---------------------------------------------------------------------------


def test_anthropic_rate_limit_isinstance() -> None:
    err = anthropic.RateLimitError(
        message="rate limit",
        response=MagicMock(headers={}),
        body={"error": {"message": "rate limit"}},
    )
    assert _is_rate_limit_error(err)


def test_anthropic_auth_error_isinstance() -> None:
    err = anthropic.AuthenticationError(
        message="auth failure",
        response=MagicMock(headers={}),
        body={"error": {"message": "auth failure"}},
    )
    assert _is_auth_error(err)


def test_anthropic_permission_denied_isinstance() -> None:
    err = anthropic.PermissionDeniedError(
        message="permission denied",
        response=MagicMock(headers={}),
        body={"error": {"message": "permission denied"}},
    )
    assert _is_auth_error(err)


# ---------------------------------------------------------------------------
# String-match fallback for OpenAI-compatible providers (DeepSeek / Qwen)
# that may not always raise SDK exception classes
# ---------------------------------------------------------------------------


def test_string_match_fallback_rate_limit() -> None:
    assert _is_rate_limit_error(Exception("HTTP 429 quota exceeded"))
    assert _is_rate_limit_error(Exception("too many requests"))
    assert not _is_rate_limit_error(Exception("connection refused"))


def test_string_match_fallback_auth() -> None:
    assert _is_auth_error(Exception("401 unauthorized"))
    assert _is_auth_error(Exception("invalid api key"))
    assert not _is_auth_error(Exception("network timeout"))


# ---------------------------------------------------------------------------
# SDK errors do NOT bleed into each other's classification
# ---------------------------------------------------------------------------


def test_openai_rate_limit_not_auth() -> None:
    err = openai.RateLimitError(
        message="rate limit",
        response=MagicMock(headers={}),
        body={},
    )
    assert _is_rate_limit_error(err)
    assert not _is_auth_error(err)


def test_anthropic_auth_not_rate_limit() -> None:
    err = anthropic.AuthenticationError(
        message="bad key",
        response=MagicMock(headers={}),
        body={},
    )
    assert _is_auth_error(err)
    assert not _is_rate_limit_error(err)
