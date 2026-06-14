"""Technical indicators collector (PRD §7.2, §6.3, inv #12)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pandas as pd
import pandas_ta  # noqa: F401  # registers df.ta accessor
import structlog

from aiat.context.collectors.base import BaseCollector, CollectorSourceError, CollectorTimeoutError
from aiat.domain.schemas import TechnicalIndicators

logger = structlog.get_logger(__name__)

_HL_BASE_URL = "https://api.hyperliquid.xyz"
_CANDLES_NEEDED = 200
_INTERVAL = "15m"
_INTERVAL_MS = 15 * 60 * 1000
_CANDLES_PER_24H = 96  # 96 × 15 min = 24 h
_SUPPORTED_SYMBOLS = frozenset({"BTC", "ETH", "SOL"})


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))


class TechnicalCollector(BaseCollector[TechnicalIndicators]):
    """Fetches 15m OHLCV candles from Hyperliquid and computes technical indicators."""

    timeout_seconds: int = 30
    cache_ttl_seconds: int = 60

    def __init__(
        self,
        symbol: str,
        client: httpx.AsyncClient,
        base_url: str = _HL_BASE_URL,
    ) -> None:
        self._symbol = symbol.upper()
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def collect(self) -> TechnicalIndicators:
        """Fetch OHLCV and compute technical indicators.

        Returns:
            TechnicalIndicators with all fields as Decimal.

        Raises:
            CollectorTimeoutError: if the HTTP call exceeds timeout_seconds.
            CollectorSourceError: if the server returns an error or invalid data.
        """
        import time

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - _CANDLES_NEEDED * _INTERVAL_MS

        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": self._symbol,
                "interval": _INTERVAL,
                "startTime": start_ms,
                "endTime": now_ms,
            },
        }

        try:
            resp = await self._client.post(
                f"{self._base_url}/info",
                json=payload,
                timeout=float(self.timeout_seconds),
            )
        except httpx.TimeoutException as exc:
            raise CollectorTimeoutError(
                f"TechnicalCollector timed out after {self.timeout_seconds}s for {self._symbol}"
            ) from exc
        except httpx.RequestError as exc:
            raise CollectorSourceError(
                f"HTTP error fetching candles for {self._symbol}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise CollectorSourceError(
                f"Candles endpoint returned {resp.status_code} for {self._symbol}: "
                f"{resp.text[:200]}"
            )

        try:
            candles: list[dict[str, object]] = resp.json()
        except Exception as exc:
            raise CollectorSourceError(
                f"Invalid JSON from candles endpoint for {self._symbol}"
            ) from exc

        if not candles:
            raise CollectorSourceError(f"Empty candles response for {self._symbol}")

        return self._compute_indicators(candles)

    def _compute_indicators(self, candles: list[dict[str, object]]) -> TechnicalIndicators:
        if self._symbol not in _SUPPORTED_SYMBOLS:
            raise CollectorSourceError(f"Unsupported symbol: {self._symbol}")

        try:
            df = pd.DataFrame(
                {
                    "open": [float(str(c["o"])) for c in candles],
                    "high": [float(str(c["h"])) for c in candles],
                    "low": [float(str(c["l"])) for c in candles],
                    "close": [float(str(c["c"])) for c in candles],
                    "volume": [float(str(c["v"])) for c in candles],
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise CollectorSourceError(f"Malformed candle data: {exc}") from exc

        if len(df) < 50:
            raise CollectorSourceError(
                f"Insufficient candles: got {len(df)}, need ≥50 for indicators"
            )

        try:
            rsi_s = df.ta.rsi(length=14)
            macd_df = df.ta.macd(fast=12, slow=26, signal=9)
            ema20_s = df.ta.ema(length=20)
            ema50_s = df.ta.ema(length=50)
            bb_df = df.ta.bbands(length=20)
            atr_s = df.ta.atr(length=14)
        except Exception as exc:
            raise CollectorSourceError(
                f"Indicator computation failed for {self._symbol}: {exc}"
            ) from exc

        try:
            rsi_val: float = float(rsi_s.dropna().iloc[-1])
            macd_h_col = next(c for c in macd_df.columns if c.startswith("MACDh_"))
            macd_h_val: float = float(macd_df[macd_h_col].dropna().iloc[-1])
            ema20_val: float = float(ema20_s.dropna().iloc[-1])
            ema50_val: float = float(ema50_s.dropna().iloc[-1])
            bbu_col = next(c for c in bb_df.columns if c.startswith("BBU_"))
            bbl_col = next(c for c in bb_df.columns if c.startswith("BBL_"))
            bb_upper_val: float = float(bb_df[bbu_col].dropna().iloc[-1])
            bb_lower_val: float = float(bb_df[bbl_col].dropna().iloc[-1])
            atr_val: float = float(atr_s.dropna().iloc[-1])
        except (IndexError, StopIteration) as exc:
            raise CollectorSourceError(
                f"Not enough data for indicators after NaN drop for {self._symbol}: {exc}"
            ) from exc

        window = df.tail(_CANDLES_PER_24H)
        volume_24h: float = float((window["close"] * window["volume"]).sum())

        return TechnicalIndicators(
            symbol=self._symbol,  # type: ignore[arg-type]
            price_usd=_to_decimal(df["close"].iloc[-1]),
            rsi_14=_to_decimal(rsi_val),
            macd_signal_diff=_to_decimal(macd_h_val),
            ema_20=_to_decimal(ema20_val),
            ema_50=_to_decimal(ema50_val),
            bollinger_upper=_to_decimal(bb_upper_val),
            bollinger_lower=_to_decimal(bb_lower_val),
            atr_14=_to_decimal(atr_val),
            volume_24h_usd=_to_decimal(volume_24h),
        )
