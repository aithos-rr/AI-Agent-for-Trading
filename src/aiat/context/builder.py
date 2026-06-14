"""ContextBuilder — composes 4 collectors into a ContextBundle (PRD §4.1 CO.1-CO.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from aiat.context.collectors.base import BaseCollector
from aiat.domain.exceptions import ContextBuildError
from aiat.domain.schemas import (
    ContextBundle,
    NewsItem,
    OnChainSnapshot,
    SentimentSnapshot,
    TechnicalIndicators,
)

logger = structlog.get_logger(__name__)

_SOURCE_LABELS = (
    "technical_btc",
    "technical_eth",
    "technical_sol",
    "sentiment",
    "news",
    "onchain",
)


class ContextBuilder:
    """Composes TechnicalCollector×3, SentimentCollector, NewsCollector, and
    OnchainCollector into a single ContextBundle via parallel fetch (§4.1 CO.1).

    Each collector enforces its own per-source timeout (technical/onchain: 10s,
    sentiment: 5s, news: 8s). A hard overall timeout is applied by the
    ContextOrchestrator (§4.1 CO hard timeout: 30s).
    """

    def __init__(
        self,
        technical_btc: BaseCollector[TechnicalIndicators],
        technical_eth: BaseCollector[TechnicalIndicators],
        technical_sol: BaseCollector[TechnicalIndicators],
        sentiment: BaseCollector[SentimentSnapshot],
        news: BaseCollector[list[NewsItem]],
        onchain: BaseCollector[list[OnChainSnapshot]],
    ) -> None:
        self._tech_btc = technical_btc
        self._tech_eth = technical_eth
        self._tech_sol = technical_sol
        self._sentiment = sentiment
        self._news = news
        self._onchain = onchain

    async def build(self, tick_id: str, tick_at: str) -> ContextBundle:
        """Fetch all sources in parallel and assemble a ContextBundle.

        Args:
            tick_id: Unique tick identifier (ISO timestamp, e.g. "2026-06-14T14:30:00Z").
            tick_at: ISO 8601 timestamp of the scheduled tick.

        Returns:
            A fully populated ContextBundle with market context byte-identical
            cross-model (invariant #13).

        Raises:
            ContextBuildError: if any collector fails (timeout or source error).
        """
        source_timestamps: dict[str, str] = {}

        async def _stamped(coro: Any, key: str) -> Any:
            result = await coro
            source_timestamps[key] = datetime.now(tz=UTC).isoformat()
            return result

        raw: list[Any] = list(
            await asyncio.gather(
                _stamped(self._tech_btc.collect(), "technical_btc"),
                _stamped(self._tech_eth.collect(), "technical_eth"),
                _stamped(self._tech_sol.collect(), "technical_sol"),
                _stamped(self._sentiment.collect(), "sentiment"),
                _stamped(self._news.collect(), "news"),
                _stamped(self._onchain.collect(), "onchain"),
                return_exceptions=True,
            )
        )

        errors: list[str] = [
            f"{label}: {value}"
            for label, value in zip(_SOURCE_LABELS, raw, strict=True)
            if isinstance(value, BaseException)
        ]
        if errors:
            raise ContextBuildError(
                f"ContextBuilder: {len(errors)} collector(s) failed — " + "; ".join(errors)
            )

        technical: list[TechnicalIndicators] = [raw[0], raw[1], raw[2]]
        sentiment: SentimentSnapshot = raw[3]
        news: list[NewsItem] = raw[4]
        onchain: list[OnChainSnapshot] = raw[5]

        logger.info(
            "context_bundle_built",
            tick_id=tick_id,
            n_technical=len(technical),
            n_news=len(news),
            n_onchain=len(onchain),
        )

        return ContextBundle(
            tick_id=tick_id,
            tick_at=tick_at,
            technical=technical,
            sentiment=sentiment,
            news=news,
            onchain=onchain,
            source_timestamps=source_timestamps,
        )
