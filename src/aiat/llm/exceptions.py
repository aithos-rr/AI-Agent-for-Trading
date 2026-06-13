"""LLM exception hierarchy (PRD §8.2)."""


class LLMError(Exception):
    """Base exception for all LLM errors."""


class LLMTimeoutError(LLMError):
    """Timeout exceeded. Does NOT trigger freetext fallback.

    Timeout indicates an infrastructure problem (slow provider, network issue,
    prompt too long). A second freetext attempt would likely fail again and burn
    API quota. Propagated as infrastructure error; run closes with status='timeout'.
    """


class LLMRateLimitError(LLMError):
    """Provider rate limit hit. Does NOT trigger freetext fallback.

    A second attempt would worsen the rate limit. Propagated.
    """


class LLMAuthError(LLMError):
    """Auth failed (invalid key, expired, insufficient permissions).
    Does NOT trigger freetext fallback. Fatal: run ends in 'failed' with
    failure_stage='llm_auth'.
    """


class LLMParsingError(LLMError):
    """LLM output not parseable as a valid TradeDecision.

    This is the ONLY case that triggers freetext fallback: the model responded
    but with malformed JSON or values that fail Pydantic validation.
    """


class LLMUnrecoverableError(LLMError):
    """Both primary structured output AND freetext fallback failed parsing.
    Run ends in 'failed' with failure_stage='llm_parse'.
    """

    def __init__(self, primary_error: Exception, fallback_error: Exception) -> None:
        self.primary_error = primary_error
        self.fallback_error = fallback_error
        super().__init__(f"primary={primary_error!r}; fallback={fallback_error!r}")
