"""Unit tests for NewsCollector (PRD §7.2, §6.3, M3-T04; D5 ADR-0011)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aiat.context.collectors.base import CollectorSourceError, CollectorTimeoutError
from aiat.context.collectors.news import MAX_ITEMS_PER_TICK, NewsCollector
from aiat.domain.schemas import NewsItem

# ---------------------------------------------------------------------------
# RSS fixture data
# ---------------------------------------------------------------------------

_RSS_COINTELEGRAPH = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Cointelegraph</title>
    <item>
      <title>Bitcoin surges to new highs</title>
      <description>BTC breaks $100k resistance level after weeks of consolidation</description>
      <pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Ethereum upgrade complete</title>
      <description>Major network upgrade reduces gas fees by 30 percent</description>
      <pubDate>Mon, 10 Jun 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

_RSS_COINDESK = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Solana ecosystem grows</title>
      <description>SOL adoption expands in the DeFi sector this quarter</description>
      <pubDate>Mon, 10 Jun 2026 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

_RSS_EMPTY = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel><title>Empty</title></channel>
</rss>"""

_RSS_INVALID = "THIS IS NOT XML <<<"

# Not well-formed XML (raw '&' + unclosed <br>): strict parser fails -> lenient fallback.
_RSS_MALFORMED = (
    '<?xml version="1.0"?><rss><channel>'
    "<item><title>Broken &amp; raw & ampersand</title>"
    "<description>has <b>bold<br> and a raw & char</description>"
    "<pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate></item>"
    "</channel></rss>"
)

# Mixed timezone offsets: the FIRST item is more recent in absolute UTC terms
# (09:30 -0500 = 14:30 UTC) than the second (12:00 +0000 = 12:00 UTC), but a naive
# lexicographic sort on the raw ISO string would order them WRONG.
_RSS_MIXED_TZ = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>MixedTZ</title>
    <item>
      <title>Most recent in UTC</title>
      <description>09:30 in -0500 equals 14:30 UTC</description>
      <pubDate>Mon, 10 Jun 2026 09:30:00 -0500</pubDate>
    </item>
    <item>
      <title>Older in UTC</title>
      <description>12:00 UTC</description>
      <pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int = 200, text: str = "") -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


def _two_source_client(
    cp_resp: httpx.Response | Exception,
    cd_resp: httpx.Response | Exception,
) -> httpx.AsyncClient:
    """Mock client: first call → cointelegraph, second call → coindesk."""
    client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[cp_resp, cd_resp])
    return client  # type: ignore[return-value]


def _single_source_client(resp: httpx.Response | Exception) -> httpx.AsyncClient:
    """Mock client returning the same response for every call."""
    client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[resp, resp])
    return client  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestNewsCollectorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_list_of_news_items(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert isinstance(result, list)
        assert all(isinstance(item, NewsItem) for item in result)

    @pytest.mark.asyncio
    async def test_items_from_both_sources_returned(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        sources = {item.source for item in result}
        assert "cointelegraph" in sources
        assert "coindesk" in sources

    @pytest.mark.asyncio
    async def test_items_sorted_by_recency_descending(self) -> None:
        # cointelegraph has 12:00 and 10:00; coindesk has 11:00
        # sorted descending: 12:00, 11:00, 10:00
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) == 3
        assert result[0].title == "Bitcoin surges to new highs"
        assert result[1].title == "Solana ecosystem grows"
        assert result[2].title == "Ethereum upgrade complete"

    @pytest.mark.asyncio
    async def test_sorted_by_absolute_instant_across_timezones(self) -> None:
        """Recency sort must use the absolute UTC instant, not the raw ISO string.

        Guards against lexicographic ordering bugs with mixed timezone offsets.
        """
        client = _two_source_client(
            _make_response(text=_RSS_MIXED_TZ),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) == 2
        assert result[0].title == "Most recent in UTC"
        assert result[1].title == "Older in UTC"

    @pytest.mark.asyncio
    async def test_get_follows_redirects(self) -> None:
        """Real CoinDesk returns HTTP 308; the collector must follow redirects."""
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        await NewsCollector(client=client).collect()
        assert client.get.call_args_list
        for call in client.get.call_args_list:
            assert call.kwargs.get("follow_redirects") is True

    @pytest.mark.asyncio
    async def test_malformed_feed_recovered_by_lenient_fallback(self) -> None:
        """A not-well-formed feed (real malformed-feed case) is recovered best-effort."""
        client = _two_source_client(
            _make_response(text=_RSS_MALFORMED),
            _make_response(text=_RSS_EMPTY),
        )
        result = await NewsCollector(client=client).collect()
        assert len(result) == 1
        assert result[0].title.startswith("Broken")

    @pytest.mark.asyncio
    async def test_sentiment_polarity_is_none(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert all(item.sentiment_polarity is None for item in result)

    @pytest.mark.asyncio
    async def test_source_name_set_correctly(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        cp_items = [i for i in result if i.source == "cointelegraph"]
        cd_items = [i for i in result if i.source == "coindesk"]
        assert len(cp_items) == 2
        assert len(cd_items) == 1

    @pytest.mark.asyncio
    async def test_max_items_cap_respected(self) -> None:
        # Build RSS with 15 items in cointelegraph
        items_xml = "\n".join(
            f"<item><title>Item {i}</title><description>desc</description>"
            f"<pubDate>Mon, 10 Jun 2026 {i:02d}:00:00 +0000</pubDate></item>"
            for i in range(15)
        )
        rss_big = f"<rss version='2.0'><channel>{items_xml}</channel></rss>"
        client = _two_source_client(
            _make_response(text=rss_big),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client, max_items=10)
        result = await collector.collect()
        assert len(result) <= 10

    @pytest.mark.asyncio
    async def test_custom_max_items(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client, max_items=2)
        result = await collector.collect()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_title_truncated_to_300_chars(self) -> None:
        long_title = "X" * 400
        rss = (
            f"<rss version='2.0'><channel><item>"
            f"<title>{long_title}</title><description>d</description>"
            f"<pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate>"
            f"</item></channel></rss>"
        )
        client = _two_source_client(
            _make_response(text=rss),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result[0].title) <= 300

    @pytest.mark.asyncio
    async def test_summary_truncated_to_600_chars(self) -> None:
        long_summary = "Y" * 700
        rss = (
            f"<rss version='2.0'><channel><item>"
            f"<title>title</title><description>{long_summary}</description>"
            f"<pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate>"
            f"</item></channel></rss>"
        )
        client = _two_source_client(
            _make_response(text=rss),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result[0].summary) <= 600

    @pytest.mark.asyncio
    async def test_published_at_is_iso_string(self) -> None:
        from datetime import datetime

        client = _two_source_client(
            _make_response(text=_RSS_COINTELEGRAPH),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        for item in result:
            dt = datetime.fromisoformat(item.published_at)
            assert dt is not None

    @pytest.mark.asyncio
    async def test_items_with_empty_title_are_skipped(self) -> None:
        # item with no <title> (or empty title) is skipped via `continue` (line 53)
        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>TestFeed</title>
    <item>
      <title></title>
      <description>desc1</description>
      <pubDate>Mon, 10 Jun 2026 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Real Item</title>
      <description>desc2</description>
      <pubDate>Mon, 10 Jun 2026 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""
        client = _two_source_client(
            _make_response(text=rss),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) == 1
        assert result[0].title == "Real Item"

    @pytest.mark.asyncio
    async def test_invalid_pubdate_falls_back_to_now(self) -> None:
        # Invalid pubDate triggers except in parsedate_to_datetime → fallback to now() (lines 58-59)
        from datetime import datetime

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>TestFeed</title>
    <item>
      <title>Item with bad date</title>
      <description>desc</description>
      <pubDate>NOT A VALID DATE</pubDate>
    </item>
  </channel>
</rss>"""
        client = _two_source_client(
            _make_response(text=rss),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) == 1
        # published_at must be a parseable ISO datetime (the fallback)
        dt = datetime.fromisoformat(result[0].published_at)
        assert dt is not None


# ---------------------------------------------------------------------------
# Partial failure: one source fails, other succeeds
# ---------------------------------------------------------------------------


class TestNewsCollectorPartialFailure:
    @pytest.mark.asyncio
    async def test_one_source_http_500_continues(self) -> None:
        client = _two_source_client(
            _make_response(status_code=500, text="error"),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) >= 1
        assert all(i.source == "coindesk" for i in result)

    @pytest.mark.asyncio
    async def test_one_source_timeout_continues(self) -> None:
        timeout_exc = httpx.ReadTimeout("timed out", request=MagicMock())
        client = _two_source_client(
            timeout_exc,
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_one_source_connect_error_continues(self) -> None:
        connect_exc = httpx.ConnectError("refused", request=MagicMock())
        client = _two_source_client(
            connect_exc,
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_one_source_invalid_xml_continues(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_INVALID),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        result = await collector.collect()
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_one_source_empty_feed_continues(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_EMPTY),
            _make_response(text=_RSS_COINDESK),
        )
        collector = NewsCollector(client=client)
        # Empty RSS from cp, but coindesk has items
        result = await collector.collect()
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# All sources fail
# ---------------------------------------------------------------------------


class TestNewsCollectorAllFail:
    @pytest.mark.asyncio
    async def test_both_sources_http_500_raises_source_error(self) -> None:
        client = _two_source_client(
            _make_response(status_code=500, text="err"),
            _make_response(status_code=500, text="err"),
        )
        collector = NewsCollector(client=client)
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_both_sources_timeout_raises_timeout_error(self) -> None:
        exc = httpx.ReadTimeout("timed out", request=MagicMock())
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[exc, exc])
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        with pytest.raises(CollectorTimeoutError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_both_sources_connect_error_raises_source_error(self) -> None:
        exc = httpx.ConnectError("refused", request=MagicMock())
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[exc, exc])
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        with pytest.raises(CollectorSourceError):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_both_empty_and_no_items_raises_source_error(self) -> None:
        client = _two_source_client(
            _make_response(text=_RSS_EMPTY),
            _make_response(text=_RSS_EMPTY),
        )
        collector = NewsCollector(client=client)
        with pytest.raises(CollectorSourceError):
            await collector.collect()


# ---------------------------------------------------------------------------
# check_sources_reachability
# ---------------------------------------------------------------------------


class TestCheckSourcesReachability:
    @pytest.mark.asyncio
    async def test_all_sources_reachable(self) -> None:
        cp_resp = _make_response(status_code=200)
        cd_resp = _make_response(status_code=200)
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(side_effect=[cp_resp, cd_resp])
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        result = await collector.check_sources_reachability()
        assert result["cointelegraph"] is True
        assert result["coindesk"] is True

    @pytest.mark.asyncio
    async def test_one_source_unreachable(self) -> None:
        cp_err = httpx.ConnectError("refused", request=MagicMock())
        cd_resp = _make_response(status_code=200)
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(side_effect=[cp_err, cd_resp])
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        result = await collector.check_sources_reachability()
        assert result["cointelegraph"] is False
        assert result["coindesk"] is True

    @pytest.mark.asyncio
    async def test_500_response_is_unreachable(self) -> None:
        cp_resp = _make_response(status_code=500)
        cd_resp = _make_response(status_code=200)
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(side_effect=[cp_resp, cd_resp])
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        result = await collector.check_sources_reachability()
        assert result["cointelegraph"] is False
        assert result["coindesk"] is True

    @pytest.mark.asyncio
    async def test_returns_dict_with_all_source_keys(self) -> None:
        client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=_make_response(status_code=200))
        collector = NewsCollector(client=client)  # type: ignore[arg-type]
        result = await collector.check_sources_reachability()
        assert "cointelegraph" in result
        assert "coindesk" in result


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestNewsCollectorDefaults:
    def test_default_timeout_is_8(self) -> None:
        collector = NewsCollector(client=AsyncMock(spec=httpx.AsyncClient))  # type: ignore[arg-type]
        assert collector.timeout_seconds == 8

    def test_default_cache_ttl_is_90(self) -> None:
        collector = NewsCollector(client=AsyncMock(spec=httpx.AsyncClient))  # type: ignore[arg-type]
        assert collector.cache_ttl_seconds == 90

    def test_default_max_items_constant(self) -> None:
        assert MAX_ITEMS_PER_TICK == 10
