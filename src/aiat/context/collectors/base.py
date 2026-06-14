"""BaseCollector ABC and collector-level exceptions (PRD §7.2)."""

from abc import ABC, abstractmethod


class CollectorTimeoutError(Exception):
    """Raised when a collector exceeds its timeout_seconds."""


class CollectorSourceError(Exception):
    """Raised when a remote source is unreachable or returns invalid data."""


class BaseCollector[T](ABC):
    """Base for collectors: technical, sentiment, news, onchain.

    T is unconstrained to support both single Pydantic models (TechnicalIndicators,
    SentimentSnapshot) and collections (list[NewsItem], list[OnChainSnapshot]).
    """

    timeout_seconds: int
    cache_ttl_seconds: int

    @abstractmethod
    async def collect(self) -> T:
        """Collect data from a remote source.

        Returns:
            Collected data (Pydantic model or list thereof).

        Raises:
            CollectorTimeoutError: if the operation exceeds self.timeout_seconds.
            CollectorSourceError: if the remote source fails or returns invalid data.
        """
        ...
