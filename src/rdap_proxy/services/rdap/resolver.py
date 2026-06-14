import asyncio
from typing import Any

import httpx

from .bootstrap import RDAPASNBootstrap, RDAPDomainBootstrap, RDAPIPBootstrap
from .models import RDAPQueryType


class RDAPResolver:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._domain_bootstrap = RDAPDomainBootstrap(client=client)
        self._ip_bootstrap = RDAPIPBootstrap(client=client)
        self._asn_bootstrap = RDAPASNBootstrap(client=client)

    async def warm(self) -> None:
        await asyncio.gather(
            self._domain_bootstrap.fetch(),
            self._ip_bootstrap.fetch(),
            self._asn_bootstrap.fetch(),
        )

    async def aclose(self) -> None:
        """Cancel any in-flight background refreshes across all bootstraps."""
        await asyncio.gather(
            self._domain_bootstrap.aclose(),
            self._ip_bootstrap.aclose(),
            self._asn_bootstrap.aclose(),
        )

    async def lookup_domain(self, fqdn: str) -> Any:
        service = await self._domain_bootstrap.lookup_service(fqdn)
        if not service:
            return None
        return await service.query(fqdn, RDAPQueryType.DOMAIN)

    async def lookup_ip(self, ip: str) -> Any:
        service = await self._ip_bootstrap.lookup_service(ip)
        if not service:
            return None
        return await service.query(ip, RDAPQueryType.IP)

    async def lookup_asn(self, asn: int | str) -> Any:
        service = await self._asn_bootstrap.lookup_service(asn)
        if not service:
            return None
        return await service.query(str(asn), RDAPQueryType.AUTNUM)

    async def lookup_nameserver(self, hostname: str) -> Any:
        # Nameservers resolve via the parent domain's TLD registry, so they use
        # the same DNS bootstrap as domains.
        service = await self._domain_bootstrap.lookup_service(hostname)
        if not service:
            return None
        return await service.query(hostname, RDAPQueryType.NAMESERVER)
