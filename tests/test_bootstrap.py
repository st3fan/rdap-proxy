"""Tests for RDAPIPBootstrap index building and longest-prefix lookup."""

import pytest

from rdap_proxy.services.rdap.bootstrap import RDAPASNBootstrap, RDAPIPBootstrap
from rdap_proxy.services.rdap.exceptions import RDAPNotFoundError


def _bootstrap(services: list) -> RDAPIPBootstrap:
    """Build an IP bootstrap with a pre-populated index (no network fetch)."""
    b = RDAPIPBootstrap()
    b._build_index(services)
    b._raw = {}  # mark as fetched so lookup_service() skips the network
    return b


def _asn_bootstrap(services: list) -> RDAPASNBootstrap:
    """Build an ASN bootstrap with a pre-populated index (no network fetch)."""
    b = RDAPASNBootstrap()
    b._build_index(services)
    b._raw = {}
    return b


async def test_ipv4_match_returns_base_url() -> None:
    b = _bootstrap([[["1.1.1.0/24"], ["https://rdap.apnic.net/"]]])
    service = await b.lookup_service("1.1.1.1")
    assert service is not None
    assert service.base_url == "https://rdap.apnic.net"


async def test_ipv6_match_returns_base_url() -> None:
    b = _bootstrap([[["2606:4700::/32"], ["https://rdap.arin.net/"]]])
    service = await b.lookup_service("2606:4700::1111")
    assert service is not None
    assert service.base_url == "https://rdap.arin.net"


async def test_longest_prefix_wins() -> None:
    b = _bootstrap(
        [
            [["1.0.0.0/8"], ["https://broad.example/"]],
            [["1.1.1.0/24"], ["https://specific.example/"]],
        ]
    )
    service = await b.lookup_service("1.1.1.1")
    assert service is not None
    assert service.base_url == "https://specific.example"


async def test_no_match_returns_none() -> None:
    b = _bootstrap([[["1.1.1.0/24"], ["https://rdap.apnic.net/"]]])
    assert await b.lookup_service("8.8.8.8") is None


async def test_cross_family_does_not_match() -> None:
    b = _bootstrap([[["2606:4700::/32"], ["https://rdap.arin.net/"]]])
    assert await b.lookup_service("1.1.1.1") is None


async def test_invalid_ip_raises_not_found() -> None:
    b = _bootstrap([[["1.1.1.0/24"], ["https://rdap.apnic.net/"]]])
    with pytest.raises(RDAPNotFoundError):
        await b.lookup_service("not-an-ip")


async def test_asn_range_match() -> None:
    b = _asn_bootstrap([[["36864-37887"], ["https://rdap.afrinic.net/"]]])
    service = await b.lookup_service(37000)
    assert service is not None
    assert service.base_url == "https://rdap.afrinic.net"


async def test_asn_single_value_range() -> None:
    b = _asn_bootstrap([[["13"], ["https://rdap.arin.net/"]]])
    assert (await b.lookup_service(13)).base_url == "https://rdap.arin.net"
    assert await b.lookup_service(14) is None


@pytest.mark.parametrize("query", ["AS37000", "as37000", "37000"])
async def test_asn_accepts_as_prefix_and_strings(query: str) -> None:
    b = _asn_bootstrap([[["36864-37887"], ["https://rdap.afrinic.net/"]]])
    service = await b.lookup_service(query)
    assert service is not None
    assert service.base_url == "https://rdap.afrinic.net"


async def test_asn_no_match_returns_none() -> None:
    b = _asn_bootstrap([[["36864-37887"], ["https://rdap.afrinic.net/"]]])
    assert await b.lookup_service(15169) is None


async def test_invalid_asn_raises_not_found() -> None:
    b = _asn_bootstrap([[["13"], ["https://rdap.arin.net/"]]])
    with pytest.raises(RDAPNotFoundError):
        await b.lookup_service("not-an-asn")
