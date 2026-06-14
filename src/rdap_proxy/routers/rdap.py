from typing import Any

from litestar import Controller, get
from litestar.exceptions import NotFoundException

from rdap_proxy.services.rdap.models import RDAPQueryType
from rdap_proxy.services.rdap.resolver import RDAPResolver
from rdap_proxy.settings import settings


async def dispatch_lookup(resolver: RDAPResolver, query_type: RDAPQueryType, query: str) -> Any:
    """Route a query to the resolver method for its RDAP object type."""
    match query_type:
        case RDAPQueryType.DOMAIN:
            return await resolver.lookup_domain(query)
        case RDAPQueryType.IP:
            return await resolver.lookup_ip(query)
        case RDAPQueryType.AUTNUM:
            return await resolver.lookup_asn(query)
        case RDAPQueryType.NAMESERVER:
            return await resolver.lookup_nameserver(query)
        case _:
            raise NotFoundException(f"Unsupported RDAP object type: {query_type}")


class RDAPController(Controller):
    path = "/rdap"

    @get("/{object_type:str}/{value:str}", cache=settings.cache_ttl if settings.cache_url else False)
    async def query(self, object_type: str, value: str, rdap: RDAPResolver) -> Any:
        try:
            query_type = RDAPQueryType(object_type)
        except ValueError:
            raise NotFoundException(f"Unknown RDAP object type: {object_type!r}")
        return await dispatch_lookup(rdap, query_type, value)
