"""Entrypoint dispatcher — reads AIAT_SERVICE_ROLE and logs the active role."""

import os

import structlog

logger: structlog.BoundLogger = structlog.get_logger()


def _load_settings_stub() -> str:
    """Return the service role from env; full implementation deferred to M5-T07."""
    return os.environ.get("AIAT_SERVICE_ROLE", "")


def main() -> None:
    role = _load_settings_stub()
    logger.info("startup", service_role=role)


if __name__ == "__main__":
    main()
