"""Fear & Greed sentiment collector (PRD §7.2, §6.3, M3-T03)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog

from aiat.context.collectors.base import BaseCollector, CollectorSourceError, CollectorTimeoutError
from aiat.domain.schemas import SentimentSnapshot

logger = structlog.get_logger(__name__)

_FNG_URL = "https://api.alternative.me/fng/"

_LABEL_MAP: dict[str, Literal["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]] = {
    "extreme fear": "extreme_fear",
    "fear": "fear",
    "neutral": "neutral",
    "greed": "greed",
    "extreme greed": "extreme_greed",
}


class SentimentCollector(BaseCollector[SentimentSnapshot]):
    """Fetches the Fear & Greed index from alternative.me (public, no key required)."""

    timeout_seconds: int = 5
    cache_ttl_seconds: int = 60

    def __init__(
        self,
        client: httpx.AsyncClient,
        timeout_seconds: int = 5,
    ) -> None:
        self._client = client
        self.timeout_seconds = timeout_seconds

    async def collect(self) -> SentimentSnapshot:
        """Fetch the current Fear & Greed index.

        Returns:
            SentimentSnapshot with index (0-100), label, and fetched_at ISO timestamp.

        Raises:
            CollectorTimeoutError: if the HTTP call exceeds timeout_seconds.
            CollectorSourceError: if the API is unreachable or returns invalid data.
        """
        try:
            resp = await self._client.get(
                _FNG_URL,
                params={"limit": 1},
                timeout=float(self.timeout_seconds),
            )
        except httpx.TimeoutException as exc:
            raise CollectorTimeoutError(
                f"SentimentCollector timed out after {self.timeout_seconds}s"
            ) from exc
        except httpx.RequestError as exc:
            raise CollectorSourceError(f"HTTP error fetching Fear & Greed: {exc}") from exc

        if resp.status_code != 200:
            raise CollectorSourceError(
                f"Fear & Greed API returned {resp.status_code}: {resp.text[:200]}"
            )

        try:
            payload: dict[str, object] = resp.json()
        except Exception as exc:
            raise CollectorSourceError("Invalid JSON from Fear & Greed API") from exc

        data = payload.get("data")
        if not isinstance(data, list) or len(data) == 0:
            raise CollectorSourceError("Fear & Greed API returned empty or missing 'data'")

        entry = data[0]
        if not isinstance(entry, dict):
            raise CollectorSourceError("Fear & Greed API 'data[0]' is not a dict")

        try:
            index_val = int(str(entry["value"]))
            raw_label = str(entry["value_classification"]).strip().lower()
        except (KeyError, ValueError, TypeError) as exc:
            raise CollectorSourceError(f"Malformed Fear & Greed entry: {exc}") from exc

        label = _LABEL_MAP.get(raw_label)
        if label is None:
            raise CollectorSourceError(f"Unknown Fear & Greed classification: '{raw_label}'")

        fetched_at = datetime.now(tz=UTC).isoformat()

        logger.info("sentiment_collected", fear_greed_index=index_val, label=label)

        return SentimentSnapshot(
            fear_greed_index=index_val,
            fear_greed_label=label,
            fetched_at=fetched_at,
        )
