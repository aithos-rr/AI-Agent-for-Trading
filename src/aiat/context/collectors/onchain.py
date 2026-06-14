"""On-chain data collector using Hyperliquid public info endpoint (PRD §7.2, §6.3, M3-T05)."""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation

import httpx
import structlog

from aiat.context.collectors.base import BaseCollector, CollectorSourceError, CollectorTimeoutError
from aiat.domain.schemas import OnChainSnapshot

logger = structlog.get_logger(__name__)

_HL_MAINNET_URL = "https://api.hyperliquid.xyz"
_HL_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
_SUPPORTED_SYMBOLS = ("BTC", "ETH", "SOL")
_LIQUIDATIONS_COEFF = Decimal("0.001")


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value))


class HLPublicInfoClient:
    """Read-only HTTP client for Hyperliquid public /info endpoint (no credentials needed)."""

    def __init__(
        self,
        network: str = "testnet",
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if base_url is not None:
            self._base_url = base_url
        elif network == "testnet":
            self._base_url = _HL_TESTNET_URL
        else:
            self._base_url = _HL_MAINNET_URL
        self._client = client or httpx.AsyncClient()

    async def fetch_meta(self) -> dict[str, object]:
        """Fetch universe meta from HL /info.

        Returns:
            Dict with 'universe' list of asset descriptors.

        Raises:
            CollectorSourceError: if the endpoint returns non-200 or invalid JSON.
        """
        resp = await self._client.post(
            f"{self._base_url}/info",
            json={"type": "meta"},
        )
        if resp.status_code != 200:
            raise CollectorSourceError(f"HL meta returned {resp.status_code}: {resp.text[:200]}")
        result: dict[str, object] = resp.json()
        return result

    async def fetch_meta_and_asset_ctxs(
        self,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Fetch meta + per-asset contexts (funding, OI, prices) from HL /info.

        Returns:
            Tuple of (meta_dict, list_of_asset_context_dicts).

        Raises:
            CollectorSourceError: if endpoint returns non-200 or unexpected structure.
        """
        resp = await self._client.post(
            f"{self._base_url}/info",
            json={"type": "metaAndAssetCtxs"},
        )
        if resp.status_code != 200:
            raise CollectorSourceError(
                f"HL metaAndAssetCtxs returned {resp.status_code}: {resp.text[:200]}"
            )
        data: list[object] = resp.json()
        if len(data) < 2 or not isinstance(data[0], dict) or not isinstance(data[1], list):
            raise CollectorSourceError("Unexpected metaAndAssetCtxs response structure")
        meta: dict[str, object] = data[0]
        ctxs: list[dict[str, object]] = [c for c in data[1] if isinstance(c, dict)]
        return meta, ctxs


class OnchainCollector(BaseCollector[list[OnChainSnapshot]]):
    """Fetches on-chain data (funding rate 8h, OI, premium, liquidations) from HL.

    Uses the public Hyperliquid /info endpoint — no wallet credentials required.
    Covers BTC, ETH, SOL (§6.3 OnChainSnapshot). Liquidations_24h_usd is approximated
    from dayNtlVlm (HL has no public global liquidations endpoint).
    """

    timeout_seconds: int = 10
    cache_ttl_seconds: int = 60

    def __init__(
        self,
        hl_client: HLPublicInfoClient,
        timeout_seconds: int = 10,
    ) -> None:
        self._hl = hl_client
        self.timeout_seconds = timeout_seconds

    async def collect(self) -> list[OnChainSnapshot]:
        """Fetch on-chain snapshots for BTC, ETH, SOL.

        Returns:
            List[OnChainSnapshot] with one entry per supported symbol.

        Raises:
            CollectorTimeoutError: if the HL request exceeds timeout_seconds.
            CollectorSourceError: if HL returns an error or unexpected data.
        """
        try:
            meta, asset_ctxs = await asyncio.wait_for(
                self._hl.fetch_meta_and_asset_ctxs(),
                timeout=float(self.timeout_seconds),
            )
        except TimeoutError as exc:
            raise CollectorTimeoutError(
                f"OnchainCollector timed out after {self.timeout_seconds}s"
            ) from exc
        except httpx.TimeoutException as exc:
            raise CollectorTimeoutError(
                f"OnchainCollector timed out after {self.timeout_seconds}s"
            ) from exc
        except CollectorSourceError:
            raise
        except Exception as exc:
            raise CollectorSourceError(f"Failed to fetch HL on-chain data: {exc}") from exc

        universe = meta.get("universe")
        if not isinstance(universe, list):
            raise CollectorSourceError("Missing 'universe' in HL meta response")

        snapshots: list[OnChainSnapshot] = []
        for symbol in _SUPPORTED_SYMBOLS:
            idx = next(
                (
                    i
                    for i, coin in enumerate(universe)
                    if isinstance(coin, dict) and coin.get("name") == symbol
                ),
                None,
            )
            if idx is None or idx >= len(asset_ctxs):
                raise CollectorSourceError(f"Symbol {symbol} not found in HL universe")

            ctx = asset_ctxs[idx]
            try:
                # HL `funding` è il rate ORARIO; ×8 → rate equivalente 8h (ADR-0013).
                funding_rate_8h = _to_decimal(ctx["funding"]) * 8
                oi_coins = _to_decimal(ctx["openInterest"])
                mark_px = _to_decimal(ctx["markPx"])
                oi_usd = oi_coins * mark_px

                # HL /info non espone un long/short ratio globale: usiamo `premium`
                # (perp vs oracle), segnale direzionale reale (ADR-0013).
                premium = _to_decimal(ctx["premium"])

                day_vlm = _to_decimal(ctx.get("dayNtlVlm", "0"))
                liquidations_24h_usd = day_vlm * _LIQUIDATIONS_COEFF

            except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                raise CollectorSourceError(f"Malformed asset context for {symbol}: {exc}") from exc

            snapshots.append(
                OnChainSnapshot(
                    symbol=symbol,  # type: ignore[arg-type]
                    funding_rate_8h=funding_rate_8h,
                    open_interest_usd=oi_usd,
                    premium=premium,
                    liquidations_24h_usd=liquidations_24h_usd,
                )
            )

        logger.info("onchain_collected", symbols=list(_SUPPORTED_SYMBOLS), n=len(snapshots))
        return snapshots
