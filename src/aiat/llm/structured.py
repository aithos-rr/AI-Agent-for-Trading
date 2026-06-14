"""invoke_structured — structured output with selective fallback (PRD §8.2)."""

import asyncio
import json

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from aiat.domain.schemas import TradeDecision
from aiat.llm.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnrecoverableError,
)
from aiat.llm.stats_handler import StatsCallbackHandler

# ---------------------------------------------------------------------------
# Module-level SDK exception type resolution (fix B.18, ADR-0009).
# Try/except here (not in every call) so the defensive ImportError branches
# are tested at import time; functions themselves stay branch-clean.
# ---------------------------------------------------------------------------
try:
    import openai as _openai

    _OPENAI_RATE_LIMIT_TYPES: tuple[type[Exception], ...] = (_openai.RateLimitError,)
    _OPENAI_AUTH_TYPES: tuple[type[Exception], ...] = (
        _openai.AuthenticationError,
        _openai.PermissionDeniedError,
    )
except ImportError:  # pragma: no cover
    _OPENAI_RATE_LIMIT_TYPES = ()
    _OPENAI_AUTH_TYPES = ()

try:
    import anthropic as _anthropic

    _ANTHROPIC_RATE_LIMIT_TYPES: tuple[type[Exception], ...] = (_anthropic.RateLimitError,)
    _ANTHROPIC_AUTH_TYPES: tuple[type[Exception], ...] = (
        _anthropic.AuthenticationError,
        _anthropic.PermissionDeniedError,
    )
except ImportError:  # pragma: no cover
    _ANTHROPIC_RATE_LIMIT_TYPES = ()
    _ANTHROPIC_AUTH_TYPES = ()

_PARSING_EXCEPTION_TYPES: tuple[type[Exception], ...] = (
    ValidationError,
    json.JSONDecodeError,
    OutputParserException,
)

FALLBACK_SUFFIX = """

IMPORTANT: Your previous response could not be parsed. Respond NOW with ONLY a
valid JSON object matching the TradeDecision schema. No markdown fences, no
explanation, just the JSON.
"""


async def invoke_structured(
    llm: BaseChatModel,
    prompt: str,
    *,
    timeout_seconds: int,
    stats_handler: StatsCallbackHandler,
) -> tuple[TradeDecision, bool]:
    """Invoke LLM with structured output; fall back to freetext only on parse failures.

    Returns:
        (TradeDecision validated by Pydantic, fallback_used: bool).

    Fix B.7 review-r2: freetext fallback ONLY for parsing failures (ValidationError,
    JSONDecodeError, malformed output). Timeout, rate limit, auth errors are propagated
    as dedicated exceptions.

    Fix B.8 review-r2: stats_handler accumulates tokens from ALL attempts (primary +
    optional fallback), so the final CostEventData reflects the total cost to produce
    this decision.

    Raises:
        LLMTimeoutError: either attempt exceeded timeout_seconds.
        LLMRateLimitError: provider rate limit hit.
        LLMAuthError: authentication or authorization failure.
        LLMUnrecoverableError: both primary and fallback failed parsing.
    """
    parsing_error: Exception | None = None

    # PATH 1: primary attempt with structured output
    try:
        # method="json_schema" forces native response_format (verified working via OpenRouter
        # for OpenAI-compatible models). The default "function_calling" does not propagate the
        # schema correctly through OpenRouter. NOTE: direct providers (esp. Anthropic, which
        # uses tool-use rather than response_format) may need a provider-aware method — to be
        # verified at M6 when direct-provider access is implemented (ADR-0008 scope boundary).
        structured_llm = llm.with_structured_output(
            TradeDecision, method="json_schema"
        ).with_config({"callbacks": [stats_handler]})
        result = await asyncio.wait_for(
            structured_llm.ainvoke(prompt),
            timeout=timeout_seconds,
        )
        assert isinstance(result, TradeDecision)
        return (result, False)
    except TimeoutError as exc:
        raise LLMTimeoutError(f"primary attempt timed out after {timeout_seconds}s") from exc
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc):
            raise LLMRateLimitError(str(exc)) from exc
        if _is_auth_error(exc):
            raise LLMAuthError(str(exc)) from exc
        if not _is_parsing_error(exc):
            raise LLMError(f"unexpected primary error: {exc!r}") from exc
        parsing_error = exc

    # PATH 2: freetext fallback (only reached after parsing failure in PATH 1)
    assert parsing_error is not None  # invariant: set in except block above
    try:
        raw_llm = llm.with_config({"callbacks": [stats_handler]})
        raw_response = await asyncio.wait_for(
            raw_llm.ainvoke(prompt + FALLBACK_SUFFIX),
            timeout=timeout_seconds,
        )
        content = raw_response.content
        if not isinstance(content, str):
            raise ValueError(f"unexpected non-string response content: {type(content)!r}")
        extracted_json = _extract_json_balanced(content)
        decision = TradeDecision.model_validate(json.loads(extracted_json))
        return (decision, True)
    except TimeoutError as exc:
        raise LLMTimeoutError(f"fallback attempt timed out after {timeout_seconds}s") from exc
    except (ValidationError, json.JSONDecodeError, ValueError) as fallback_err:
        raise LLMUnrecoverableError(
            primary_error=parsing_error,
            fallback_error=fallback_err,
        ) from fallback_err


def _extract_json_balanced(text: str) -> str:
    """Extract the first balanced JSON object {...} from text.

    Handles curly braces INSIDE JSON string literals correctly via a state machine
    (fix B.9 review-r2: avoids counting structural delimiters inside string values).

    States: NORMAL (0), IN_STRING (1), IN_STRING_ESCAPE (2).

    Raises:
        ValueError: no balanced JSON object found or unbalanced braces.
    """
    NORMAL, IN_STRING, IN_STRING_ESCAPE = 0, 1, 2
    state = NORMAL
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if state == NORMAL:
            if ch == '"':
                state = IN_STRING
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : i + 1]
                if depth < 0:
                    raise ValueError(f"Unbalanced '}}' at position {i}")
        elif state == IN_STRING:
            if ch == "\\":
                state = IN_STRING_ESCAPE
            elif ch == '"':
                state = NORMAL
        elif state == IN_STRING_ESCAPE:
            state = IN_STRING  # skip the escaped character
    raise ValueError("No balanced JSON object found in text")


def _is_parsing_error(exc: Exception) -> bool:
    """True if the exception is due to malformed output (NOT timeout/rate/auth)."""
    return isinstance(exc, _PARSING_EXCEPTION_TYPES)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Classify rate-limit errors using isinstance() primary + string-match fallback.

    Primary: SDK exception classes resolved at module import (ADR-0009).
    Fallback: string matching for OpenAI-compatible providers that may not always
    raise SDK exception classes.
    """
    if isinstance(exc, (*_OPENAI_RATE_LIMIT_TYPES, *_ANTHROPIC_RATE_LIMIT_TYPES)):
        return True
    err_str = str(exc).lower()
    return any(
        token in err_str for token in ["rate limit", "429", "too many requests", "quota exceeded"]
    )


def _is_auth_error(exc: Exception) -> bool:
    """Classify auth errors using isinstance() primary + string-match fallback.

    Same strategy as _is_rate_limit_error: isinstance() for known SDK classes,
    string-match fallback for OpenAI-compatible providers (ADR-0009).
    """
    if isinstance(exc, (*_OPENAI_AUTH_TYPES, *_ANTHROPIC_AUTH_TYPES)):
        return True
    err_str = str(exc).lower()
    return any(
        token in err_str
        for token in ["401", "403", "unauthorized", "invalid api key", "authentication"]
    )
