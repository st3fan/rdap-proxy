import logging

import httpx
from litestar import Litestar, Request, Response
from litestar.datastructures import State
from litestar.di import Provide
from litestar.logging import LoggingConfig
from litestar.status_codes import HTTP_404_NOT_FOUND, HTTP_502_BAD_GATEWAY

from rdap_proxy.cache import build_store
from rdap_proxy.routers import health, rdap
from rdap_proxy.services.rdap.exceptions import (
    RDAPBootstrapError,
    RDAPNotFoundError,
    RDAPServiceError,
)
from rdap_proxy.services.rdap.resolver import RDAPResolver
from rdap_proxy.settings import settings

logger = logging.getLogger(__name__)


async def on_startup(app: Litestar) -> None:
    """Create the shared HTTP client and RDAPResolver, then warm the bootstraps."""
    # One pooled client for the app's lifetime: keep-alive and TLS reuse across
    # all upstream RDAP and IANA bootstrap requests, instead of one client per
    # request. Created on the running loop and closed in on_shutdown.
    client = httpx.AsyncClient()
    resolver = RDAPResolver(client=client)
    try:
        await resolver.warm()
    except RDAPBootstrapError:
        # Best-effort prefetch: don't block startup if IANA is briefly
        # unreachable. The resolver retries lazily on the first request.
        logger.warning("Bootstrap warm-up failed; will fetch lazily", exc_info=True)
    app.state.http_client = client
    app.state.rdap_resolver = resolver


async def on_shutdown(app: Litestar) -> None:
    """Cancel in-flight background refreshes, then close the shared client."""
    resolver: RDAPResolver = app.state.rdap_resolver
    await resolver.aclose()  # stop refreshes before the pool they use goes away
    await app.state.http_client.aclose()


def provide_rdap_resolver(state: State) -> RDAPResolver:
    return state.rdap_resolver


def _rdap_not_found_handler(_: Request, exc: RDAPNotFoundError) -> Response:
    return Response({"error": str(exc)}, status_code=HTTP_404_NOT_FOUND)


def _rdap_upstream_error_handler(
    _: Request, exc: RDAPServiceError | RDAPBootstrapError
) -> Response:
    return Response({"error": str(exc)}, status_code=HTTP_502_BAD_GATEWAY)


def create_app() -> Litestar:
    logging_config = LoggingConfig(
        root={"level": settings.log_level, "handlers": ["console"]},
        formatters={
            "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
        },
        log_exceptions=settings.log_exceptions,
        # Keep the HTTP client libraries quiet even at LOG_LEVEL=DEBUG; their
        # per-request DEBUG output drowns out our own logging. Children
        # (httpcore.http11, httpcore.connection, ...) inherit these levels.
        loggers={
            "httpcore": {"level": "WARNING", "propagate": True},
            "httpx": {"level": "INFO", "propagate": True},
        },
    )

    stores = (
        {"response_cache": build_store(settings.cache_url)}
        if settings.cache_url
        else {}
    )

    return Litestar(
        route_handlers=[health.router, rdap.RDAPController],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        stores=stores,
        dependencies={
            "rdap": Provide(provide_rdap_resolver, sync_to_thread=False),
        },
        exception_handlers={
            RDAPNotFoundError: _rdap_not_found_handler,
            RDAPServiceError: _rdap_upstream_error_handler,
            RDAPBootstrapError: _rdap_upstream_error_handler,
        },
        logging_config=logging_config,
        debug=settings.debug,
    )


app = create_app()


def serve() -> None:
    """Production entrypoint: no auto-reload."""
    import uvicorn

    uvicorn.run(
        "rdap_proxy.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


def dev() -> None:
    """Development entrypoint: auto-reload enabled."""
    import uvicorn

    uvicorn.run(
        "rdap_proxy.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=True,
    )


def test() -> None:
    """Run the test suite."""
    import sys

    import pytest

    raise SystemExit(pytest.main(sys.argv[1:]))
