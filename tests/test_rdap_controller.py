"""Tests for the RDAP query dispatch in rdap_proxy.routers.rdap."""

import pytest
from litestar.exceptions import NotFoundException

from rdap_proxy.routers.rdap import dispatch_lookup
from rdap_proxy.services.rdap.models import RDAPQueryType


class StubResolver:
    """Records which typed lookup method dispatch_lookup invokes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def lookup_domain(self, query: str) -> str:
        self.calls.append(("domain", query))
        return "domain-result"

    async def lookup_ip(self, query: str) -> str:
        self.calls.append(("ip", query))
        return "ip-result"

    async def lookup_asn(self, query: str) -> str:
        self.calls.append(("asn", query))
        return "asn-result"

    async def lookup_nameserver(self, query: str) -> str:
        self.calls.append(("nameserver", query))
        return "nameserver-result"


@pytest.mark.parametrize(
    "query_type, expected_call, expected_result",
    [
        (RDAPQueryType.DOMAIN, "domain", "domain-result"),
        (RDAPQueryType.IP, "ip", "ip-result"),
        (RDAPQueryType.AUTNUM, "asn", "asn-result"),
        (RDAPQueryType.NAMESERVER, "nameserver", "nameserver-result"),
    ],
)
async def test_dispatch_routes_to_matching_method(
    query_type: RDAPQueryType, expected_call: str, expected_result: str
) -> None:
    resolver = StubResolver()

    result = await dispatch_lookup(resolver, query_type, "example")

    assert resolver.calls == [(expected_call, "example")]
    assert result == expected_result


async def test_dispatch_rejects_entity() -> None:
    resolver = StubResolver()

    with pytest.raises(NotFoundException):
        await dispatch_lookup(resolver, RDAPQueryType.ENTITY, "handle-123")

    assert resolver.calls == []
