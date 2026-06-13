"""Domain enums (§6.1)."""

from enum import StrEnum


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"
    HOLD = "HOLD"


class EntryType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    NONE = "none"


class Tier(StrEnum):
    PREMIUM = "premium"
    CHEAP_ALT = "cheap_alt"


class Geography(StrEnum):
    USA = "USA"
    CN = "CN"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    MISSED = "missed"
    SKIPPED = "skipped"


class ExecutionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrderKind(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    CLOSE = "close"


class CloseReason(StrEnum):
    MANUAL = "manual"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    LIQUIDATED = "liquidated"
    MODEL_CLOSE = "model_close"
