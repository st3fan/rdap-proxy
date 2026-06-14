import asyncio
import ipaddress
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx

from .exceptions import RDAPBootstrapError, RDAPNotFoundError
from .service import RDAPService

logger = logging.getLogger(__name__)

IANA_BOOTSTRAP_URLS = {
    "domain": "https://data.iana.org/rdap/dns.json",
    "ipv4": "https://data.iana.org/rdap/ipv4.json",
    "ipv6": "https://data.iana.org/rdap/ipv6.json",
    "asn": "https://data.iana.org/rdap/asn.json",
}


@dataclass
class _BootstrapSource:
    """A single IANA bootstrap document plus its HTTP cache validators."""

    url: str
    etag: str | None = None
    last_modified: str | None = None
    services: list | None = None  # raw "services" array, kept to rebuild the index


IndexT = TypeVar("IndexT")


class RDAPBootstrap(ABC, Generic[IndexT]):
    bootstrap_urls: tuple[str, ...]  # defined by subclass

    # Fallback freshness when IANA sends no Cache-Control max-age.
    _DEFAULT_TTL = 3600.0
    # How far to push expiry out when a refresh fails but we have stale data.
    _STALE_TTL = 300.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sources = [_BootstrapSource(url) for url in self.bootstrap_urls]
        self._expiry = 0.0  # monotonic deadline; 0 == never fetched / expired
        self._loaded = False
        self._refresh_task: asyncio.Task | None = None

    async def fetch(self, *, force: bool = False) -> None:
        """Refresh bootstrap data if expired, using conditional HTTP requests.

        Cheap when fresh (no network). On expiry, issues If-None-Match /
        If-Modified-Since requests; a 304 just renews the expiry, a 200
        re-indexes. If the network fails but we already have data, the existing
        index is kept and expiry is extended (serve stale).
        """
        async with self._lock:
            now = time.monotonic()
            if self._loaded and now < self._expiry and not force:
                return

            # Phase 1: network only. Bail out atomically on any failure.
            responses: list[tuple[_BootstrapSource, httpx.Response]] = []
            for source in self._sources:
                try:
                    response = await self._conditional_get(source)
                    response.raise_for_status()
                except httpx.HTTPError as e:
                    if self._loaded:
                        logger.warning(
                            "Bootstrap refresh failed for %s; serving stale (%s)",
                            source.url,
                            e,
                        )
                        self._expiry = now + self._STALE_TTL
                        return
                    raise RDAPBootstrapError(
                        f"Failed to fetch bootstrap data from {source.url}: {e}"
                    ) from e
                responses.append((source, response))

            # Phase 2: commit. Only 200s change data and force a re-index.
            max_ages: list[float] = []
            dirty = False
            for source, response in responses:
                max_ages.append(self._max_age(response))
                if response.status_code == 200:
                    source.etag = response.headers.get("ETag")
                    source.last_modified = response.headers.get("Last-Modified")
                    source.services = response.json()["services"]
                    dirty = True

            self._expiry = now + min(max_ages, default=self._DEFAULT_TTL)

            if dirty or not self._loaded:
                start = time.perf_counter()
                combined = [
                    entry for source in self._sources for entry in (source.services or [])
                ]
                # Build off the event loop, then swap the reference in a single
                # assignment. Readers (which don't take the lock) always see a
                # complete index -- the old one or the new one, never a partial.
                new_index = await asyncio.to_thread(self._build_index, combined)
                self._index = new_index
                logger.debug(
                    "Built %s index: %d entries in %.1f ms",
                    type(self).__name__,
                    len(new_index),
                    (time.perf_counter() - start) * 1000,
                )
            self._loaded = True

    async def _conditional_get(self, source: _BootstrapSource) -> httpx.Response:
        """GET a bootstrap document, sending cache validators when known."""
        headers = {}
        if source.etag:
            headers["If-None-Match"] = source.etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified
        async with httpx.AsyncClient() as client:
            return await client.get(source.url, headers=headers, timeout=10.0)

    def _max_age(self, response: httpx.Response) -> float:
        """Seconds of freshness from the Cache-Control max-age, else the default."""
        cache_control = response.headers.get("Cache-Control", "")
        for part in cache_control.split(","):
            part = part.strip().lower()
            if part.startswith("max-age="):
                try:
                    return float(part.removeprefix("max-age="))
                except ValueError:
                    break
        return self._DEFAULT_TTL

    async def _ensure_fetched(self) -> None:
        """Stale-while-revalidate gate for lookups.

        Fresh data returns immediately (no lock). With no data at all we must
        block on the first fetch. Once we have data but it has expired, we serve
        the stale index right away and refresh in the background, so readers are
        never blocked on a slow IANA round-trip.
        """
        if self._loaded and time.monotonic() < self._expiry:
            return
        if not self._loaded:
            await self.fetch()  # cold start: nothing to serve, must block
            return
        self._schedule_refresh()  # stale: serve old index, refresh in background

    def _schedule_refresh(self) -> None:
        """Start a single background refresh, coalescing concurrent stale readers.

        Runs entirely on the event loop with no await, so the check-and-create is
        atomic against other coroutines: at most one refresh task is ever in
        flight. The reference is kept so the task is not garbage-collected mid-run.
        """
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_in_background())

    async def _refresh_in_background(self) -> None:
        # fetch() already serves stale and extends expiry on network failure when
        # loaded; this guard is defensive against unexpected errors and stops a
        # background-task exception from being swallowed silently.
        try:
            await self.fetch()
        except Exception:
            logger.warning(
                "Background bootstrap refresh failed for %s",
                type(self).__name__,
                exc_info=True,
            )

    @abstractmethod
    def _build_index(self, services: list) -> IndexT:
        """Build and return a fresh index structure for fast lookup.

        Must be pure with respect to instance state -- it reads only its
        argument (and the read-only _pick_url helper) and returns a brand-new
        structure. fetch() swaps it into self._index atomically. This keeps the
        method safe to run off the event loop via asyncio.to_thread.
        """
        ...

    @abstractmethod
    async def lookup_service(self, query: Any) -> RDAPService | None:
        """Return the authoritative RDAPService for the given query."""
        ...

    def _pick_url(self, urls: list[str]) -> str:
        """
        Pick the best URL from the list returned by IANA.
        Prefer HTTPS, take the first otherwise.
        """
        https = [u for u in urls if u.startswith("https://")]
        return https[0] if https else urls[0]


class RDAPDomainBootstrap(RDAPBootstrap[dict[str, str]]):
    bootstrap_urls = (IANA_BOOTSTRAP_URLS["domain"],)

    def __init__(self) -> None:
        super().__init__()
        self._index: dict[str, str] = {}  # tld -> base_url

    def _build_index(self, services: list) -> dict[str, str]:
        index: dict[str, str] = {}
        for tlds, urls in services:
            url = self._pick_url(urls)
            for tld in tlds:
                index[tld.lower()] = url
        return index

    async def lookup_service(self, query: str) -> RDAPService | None:
        await self._ensure_fetched()

        # Walk labels right-to-left to find the longest matching TLD.
        # Handles multi-label TLDs like .co.uk if IANA ever adds them.
        labels = query.lower().rstrip(".").split(".")
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            if candidate in self._index:
                return RDAPService(self._index[candidate])


class RDAPIPBootstrap(
    RDAPBootstrap[list[tuple["ipaddress.IPv4Network | ipaddress.IPv6Network", str]]]
):
    # IANA splits IP space across two bootstrap files; the base loads both.
    bootstrap_urls = (IANA_BOOTSTRAP_URLS["ipv4"], IANA_BOOTSTRAP_URLS["ipv6"])

    def __init__(self) -> None:
        super().__init__()
        # (network, base_url), searched by longest-prefix match.
        self._index: list[
            tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]
        ] = []

    def _build_index(
        self, services: list
    ) -> list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]]:
        index: list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]] = []
        for cidrs, urls in services:
            url = self._pick_url(urls)
            for cidr in cidrs:
                index.append((ipaddress.ip_network(cidr), url))
        return index

    async def lookup_service(self, query: str) -> RDAPService | None:
        await self._ensure_fetched()
        try:
            addr = ipaddress.ip_address(query)
        except ValueError as e:
            raise RDAPNotFoundError(f"Not a valid IP address: {query!r}") from e

        best: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str] | None = None
        for network, url in self._index:
            if addr in network and (best is None or network.prefixlen > best[0].prefixlen):
                best = (network, url)
        return RDAPService(best[1]) if best else None


class RDAPASNBootstrap(RDAPBootstrap[list[tuple[int, int, str]]]):
    bootstrap_urls = (IANA_BOOTSTRAP_URLS["asn"],)

    def __init__(self) -> None:
        super().__init__()
        # (start, end, base_url) inclusive AS-number ranges.
        self._index: list[tuple[int, int, str]] = []

    def _build_index(self, services: list) -> list[tuple[int, int, str]]:
        index: list[tuple[int, int, str]] = []
        for ranges, urls in services:
            url = self._pick_url(urls)
            for asn_range in ranges:
                start, _, end = asn_range.partition("-")
                low = int(start)
                index.append((low, int(end) if end else low, url))
        return index

    async def lookup_service(self, query: int | str) -> RDAPService | None:
        await self._ensure_fetched()
        try:
            asn = int(str(query).upper().removeprefix("AS"))
        except ValueError as e:
            raise RDAPNotFoundError(f"Not a valid AS number: {query!r}") from e

        for low, high, url in self._index:
            if low <= asn <= high:
                return RDAPService(url)
        return None
