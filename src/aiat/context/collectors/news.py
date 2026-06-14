"""RSS news collector (PRD §7.2, §6.3, M3-T04; closes D5 via ADR-0011)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

import httpx
import structlog

from aiat.context.collectors.base import BaseCollector, CollectorSourceError, CollectorTimeoutError
from aiat.domain.schemas import NewsItem

logger = structlog.get_logger(__name__)

# D5 decision (ADR-0011): 2 RSS sources, 10 items/tick max.
_RSS_SOURCES: Final[dict[str, str]] = {
    "cryptopanic": "https://cryptopanic.com/news/rss/",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

MAX_ITEMS_PER_TICK: Final[int] = 10


def _parse_rss(xml_text: str, source_name: str) -> list[NewsItem]:
    """Parse RSS 2.0 XML into a list of NewsItem.

    Args:
        xml_text: raw XML string from the feed.
        source_name: human-readable source label stored in NewsItem.source.

    Raises:
        CollectorSourceError: if the XML is malformed.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CollectorSourceError(f"Invalid XML from {source_name}: {exc}") from exc

    items: list[NewsItem] = []
    for item_el in root.findall(".//item"):
        title_el = item_el.find("title")
        desc_el = item_el.find("description")
        pub_el = item_el.find("pubDate")

        title = (title_el.text or "").strip() if title_el is not None else ""
        summary = (desc_el.text or "").strip() if desc_el is not None else ""
        raw_pub = (pub_el.text or "").strip() if pub_el is not None else ""

        if not title:
            continue

        try:
            pub_dt = parsedate_to_datetime(raw_pub)
            # Normalize to UTC so lexicographic AND datetime ordering are chronological
            # even when feeds emit mixed timezone offsets (avoids wrong recency sort).
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=UTC)
            published_at = pub_dt.astimezone(UTC).isoformat()
        except Exception:
            published_at = datetime.now(tz=UTC).isoformat()

        items.append(
            NewsItem(
                title=title[:300],
                summary=summary[:600],
                source=source_name,
                published_at=published_at,
                sentiment_polarity=None,
            )
        )

    return items


class NewsCollector(BaseCollector[list[NewsItem]]):
    """Fetches crypto news from RSS feeds (CryptoPanic + CoinDesk).

    D5 decision (ADR-0011): 10 items/tick, 2 sources, sorted by recency.
    At least one source must succeed; partial failures are tolerated.
    """

    timeout_seconds: int = 8
    cache_ttl_seconds: int = 90

    def __init__(
        self,
        client: httpx.AsyncClient,
        timeout_seconds: int = 8,
        max_items: int = MAX_ITEMS_PER_TICK,
    ) -> None:
        self._client = client
        self.timeout_seconds = timeout_seconds
        self._max_items = max_items

    async def _fetch_source(self, source_name: str, url: str) -> list[NewsItem]:
        """Fetch and parse a single RSS source.

        Raises:
            CollectorTimeoutError: on HTTP timeout.
            CollectorSourceError: on HTTP error or invalid response.
        """
        try:
            resp = await self._client.get(url, timeout=float(self.timeout_seconds))
        except httpx.TimeoutException as exc:
            raise CollectorTimeoutError(
                f"NewsCollector timed out fetching {source_name} after {self.timeout_seconds}s"
            ) from exc
        except httpx.RequestError as exc:
            raise CollectorSourceError(f"HTTP error fetching {source_name}: {exc}") from exc

        if resp.status_code != 200:
            raise CollectorSourceError(
                f"{source_name} RSS returned {resp.status_code}: {resp.text[:200]}"
            )

        return _parse_rss(resp.text, source_name)

    async def collect(self) -> list[NewsItem]:
        """Fetch news from all RSS sources and return top max_items sorted by recency.

        Tolerates partial source failures: if at least one source succeeds, its
        items are returned. Raises only when ALL sources fail.

        Returns:
            list[NewsItem] sorted by published_at descending, len ≤ max_items.

        Raises:
            CollectorTimeoutError: if every source timed out.
            CollectorSourceError: if every source failed (non-timeout).
        """
        all_items: list[NewsItem] = []
        errors: list[Exception] = []
        all_timed_out = True

        for source_name, url in _RSS_SOURCES.items():
            try:
                items = await self._fetch_source(source_name, url)
                all_items.extend(items)
                all_timed_out = False
                logger.info("news_source_fetched", source=source_name, count=len(items))
            except CollectorTimeoutError as exc:
                logger.warning("news_source_timeout", source=source_name)
                errors.append(exc)
            except CollectorSourceError as exc:
                logger.warning("news_source_error", source=source_name, error=str(exc))
                errors.append(exc)
                all_timed_out = False

        if not all_items:
            if all_timed_out and errors:
                raise CollectorTimeoutError("All RSS news sources timed out") from errors[0]
            raise CollectorSourceError(f"All RSS news sources failed: {[str(e) for e in errors]}")

        all_items.sort(key=lambda x: datetime.fromisoformat(x.published_at), reverse=True)
        result = all_items[: self._max_items]
        logger.info("news_collected", total=len(result))
        return result

    async def check_sources_reachability(self) -> dict[str, bool]:
        """Check reachability of each configured RSS source via HEAD request.

        Returns:
            dict mapping source_name → is_reachable (True if HTTP status < 500).
        """
        reachability: dict[str, bool] = {}
        for source_name, url in _RSS_SOURCES.items():
            try:
                resp = await self._client.head(url, timeout=float(self.timeout_seconds))
                reachability[source_name] = resp.status_code < 500
            except (httpx.TimeoutException, httpx.RequestError):
                reachability[source_name] = False
        return reachability
