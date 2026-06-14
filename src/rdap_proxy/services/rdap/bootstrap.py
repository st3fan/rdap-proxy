import asyncio
import ipaddress
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

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


class RDAPBootstrap(ABC):
    bootstrap_url: str  # defined by subclass

    def __init__(self) -> None:
        self._raw: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def fetch(self, *, force: bool = False) -> None:
        """Fetch bootstrap data from IANA, building the internal index."""
        async with self._lock:
            if self._raw is not None and not force:
                return
            self._raw = await self._fetch_json(self.bootstrap_url)
            start = time.perf_counter()
            self._build_index(self._raw["services"])
            logger.debug(
                "Built %s index: %d entries in %.1f ms",
                type(self).__name__,
                len(self._index),
                (time.perf_counter() - start) * 1000,
            )

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """Fetch and decode a single IANA bootstrap document."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            raise RDAPBootstrapError(
                f"Failed to fetch bootstrap data from {url}: {e}"
            ) from e

    async def _ensure_fetched(self) -> None:
        if self._raw is None:
            await self.fetch()

    @abstractmethod
    def _build_index(self, services: list) -> None:
        """Build whatever internal index structure is needed for fast lookup."""
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


class RDAPDomainBootstrap(RDAPBootstrap):
    bootstrap_url = IANA_BOOTSTRAP_URLS["domain"]

    def __init__(self) -> None:
        super().__init__()
        self._index: dict[str, str] = {}  # tld -> base_url

    def _build_index(self, services: list) -> None:
        self._index.clear()
        for tlds, urls in services:
            url = self._pick_url(urls)
            for tld in tlds:
                self._index[tld.lower()] = url

    async def lookup_service(self, query: str) -> RDAPService | None:
        await self._ensure_fetched()

        # Walk labels right-to-left to find the longest matching TLD.
        # Handles multi-label TLDs like .co.uk if IANA ever adds them.
        labels = query.lower().rstrip(".").split(".")
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            if candidate in self._index:
                return RDAPService(self._index[candidate])


class RDAPIPBootstrap(RDAPBootstrap):
    def __init__(self) -> None:
        super().__init__()
        # (network, base_url), searched by longest-prefix match.
        self._index: list[
            tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]
        ] = []

    async def fetch(self, *, force: bool = False) -> None:
        # IANA splits IP space across two bootstrap files; load both.
        async with self._lock:
            if self._raw is not None and not force:
                return
            self._index.clear()
            raw: dict[str, Any] = {}
            build_seconds = 0.0
            for family in ("ipv4", "ipv6"):
                data = await self._fetch_json(IANA_BOOTSTRAP_URLS[family])
                start = time.perf_counter()
                self._build_index(data["services"])
                build_seconds += time.perf_counter() - start
                raw[family] = data
            self._raw = raw  # set last, so a mid-fetch failure retries next time
            logger.debug(
                "Built IP bootstrap index: %d networks in %.1f ms",
                len(self._index),
                build_seconds * 1000,
            )

    def _build_index(self, services: list) -> None:
        for cidrs, urls in services:
            url = self._pick_url(urls)
            for cidr in cidrs:
                self._index.append((ipaddress.ip_network(cidr), url))

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


class RDAPASNBootstrap(RDAPBootstrap):
    bootstrap_url = IANA_BOOTSTRAP_URLS["asn"]

    def __init__(self) -> None:
        super().__init__()
        # (start, end, base_url) inclusive AS-number ranges.
        self._index: list[tuple[int, int, str]] = []

    def _build_index(self, services: list) -> None:
        self._index.clear()
        for ranges, urls in services:
            url = self._pick_url(urls)
            for asn_range in ranges:
                start, _, end = asn_range.partition("-")
                low = int(start)
                self._index.append((low, int(end) if end else low, url))

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
