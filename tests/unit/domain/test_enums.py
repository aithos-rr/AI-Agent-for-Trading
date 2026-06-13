"""Tests for domain enums (§6.1)."""

from aiat.domain.enums import (
    CloseReason,
    EntryType,
    ExecutionStatus,
    Geography,
    OrderKind,
    RunStatus,
    Side,
    Tier,
)


class TestSide:
    def test_values(self) -> None:
        assert Side.LONG == "LONG"
        assert Side.SHORT == "SHORT"
        assert Side.FLAT == "FLAT"
        assert Side.HOLD == "HOLD"

    def test_is_str(self) -> None:
        assert isinstance(Side.LONG, str)

    def test_all_members(self) -> None:
        assert set(Side) == {Side.LONG, Side.SHORT, Side.FLAT, Side.HOLD}


class TestEntryType:
    def test_values(self) -> None:
        assert EntryType.MARKET == "market"
        assert EntryType.LIMIT == "limit"
        assert EntryType.NONE == "none"


class TestTier:
    def test_values(self) -> None:
        assert Tier.PREMIUM == "premium"
        assert Tier.CHEAP_ALT == "cheap_alt"


class TestGeography:
    def test_values(self) -> None:
        assert Geography.USA == "USA"
        assert Geography.CN == "CN"


class TestRunStatus:
    def test_all_7_values(self) -> None:
        expected = {
            "running",
            "success",
            "partial",
            "failed",
            "timeout",
            "missed",
            "skipped",
        }
        assert {s.value for s in RunStatus} == expected


class TestExecutionStatus:
    def test_all_6_values(self) -> None:
        expected = {
            "not_applicable",
            "pending",
            "filled",
            "partial",
            "failed",
            "cancelled",
        }
        assert {s.value for s in ExecutionStatus} == expected


class TestOrderKind:
    def test_values(self) -> None:
        assert OrderKind.ENTRY == "entry"
        assert OrderKind.STOP_LOSS == "stop_loss"
        assert OrderKind.TAKE_PROFIT == "take_profit"
        assert OrderKind.CLOSE == "close"


class TestCloseReason:
    def test_values(self) -> None:
        assert CloseReason.MANUAL == "manual"
        assert CloseReason.STOP_LOSS == "stop_loss"
        assert CloseReason.TAKE_PROFIT == "take_profit"
        assert CloseReason.LIQUIDATED == "liquidated"
        assert CloseReason.MODEL_CLOSE == "model_close"


def test_8_enums_exported() -> None:
    """All 8 enums from §6.1 are importable."""
    from aiat.domain.enums import (  # noqa: F401
        CloseReason,
        EntryType,
        ExecutionStatus,
        Geography,
        OrderKind,
        RunStatus,
        Side,
        Tier,
    )
