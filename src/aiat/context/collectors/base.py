"""BaseCollector ABC and collector-level exceptions (PRD §7.2)."""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class CollectorTimeoutError(Exception):
    """Raised when a collector exceeds its timeout_seconds."""


class CollectorSourceError(Exception):
    """Raised when a remote source is unreachable or returns invalid data."""


class BaseCollector[T: BaseModel](ABC):
    """Base for collectors: technical, sentiment, news, onchain."""

    timeout_seconds: int
    cache_ttl_seconds: int

    @abstractmethod
    async def collect(self) -> T:
        """Collect data from a remote source.

        Returns:
            Pydantic model with the collected data.

        Raises:
            CollectorTimeoutError: if the operation exceeds self.timeout_seconds.
            CollectorSourceError: if the remote source fails or returns invalid data.
        """
        ...
