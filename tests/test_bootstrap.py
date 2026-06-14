"""Tests for bootstrap index building, lookup, and HTTP-cached refresh."""

import asyncio
import time

import httpx
import pytest

from rdap_proxy.services.rdap.bootstrap import (
    RDAPASNBootstrap,
    RDAPDomainBootstrap,
    RDAPIPBootstrap,
)
from rdap_proxy.services.rdap.exceptions import RDAPBootstrapError, RDAPNotFoundError


def _seed(b):
    """Mark a bootstrap as loaded and fresh so lookup_service skips the network."""
    b._loaded = True
    b._expiry = time.monotonic() + 3600
    return b


def _bootstrap(services: list) -> RDAPIPBootstrap:
    """Build an IP bootstrap with a pre-populated index (no network fetch)."""
    b = RDAPIPBootstrap()
    b._build_index(services)
    return _seed(b)


def _asn_bootstrap(services: list) -> RDAPASNBootstrap:
    """Build an ASN bootstrap with a pre-populated index (no network fetch)."""
    b = RDAPASNBootstrap()
    b._build_index(services)
    return _seed(b)


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


# --- HTTP-cached refresh -------------------------------------------------------


def _response(status: int, *, services=None, max_age=60, etag=None) -> httpx.Response:
    headers = {"Cache-Control": f"max-age={max_age}"}
    if etag is not None:
        headers["ETag"] = etag
    json = {"services": services or []} if status == 200 else None
    request = httpx.Request("GET", "https://data.iana.org/rdap/dns.json")
    return httpx.Response(status, json=json, headers=headers, request=request)


def _patch_get(monkeypatch, responses: list[httpx.Response]) -> list[dict]:
    """Patch _conditional_get to return queued responses; record sent headers."""
    sent: list[dict] = []
    queue = list(responses)

    async def fake_get(self, source):
        sent.append(
            {
                "url": source.url,
                "If-None-Match": source.etag,
                "If-Modified-Since": source.last_modified,
            }
        )
        return queue.pop(0)

    monkeypatch.setattr(
        "rdap_proxy.services.rdap.bootstrap.RDAPBootstrap._conditional_get", fake_get
    )
    return sent


async def test_initial_fetch_builds_index(monkeypatch) -> None:
    _patch_get(monkeypatch, [_response(200, services=[[["com"], ["https://v/"]]])])
    b = RDAPDomainBootstrap()
    await b.fetch()
    assert b._loaded
    service = await b.lookup_service("example.com")
    assert service is not None and service.base_url == "https://v"


async def test_no_refetch_within_ttl(monkeypatch) -> None:
    sent = _patch_get(
        monkeypatch, [_response(200, services=[[["com"], ["https://v/"]]], max_age=300)]
    )
    b = RDAPDomainBootstrap()
    await b.fetch()
    await b.fetch()  # still fresh -> no network
    assert len(sent) == 1


async def test_304_renews_expiry_without_reindex(monkeypatch) -> None:
    _patch_get(
        monkeypatch,
        [
            _response(200, services=[[["com"], ["https://v/"]]], etag="v1", max_age=0),
            _response(304, max_age=120),
        ],
    )
    b = RDAPDomainBootstrap()
    await b.fetch()  # 200, builds index, expires immediately (max-age=0)

    calls = []
    original = b._build_index
    monkeypatch.setattr(b, "_build_index", lambda s: calls.append(s) or original(s))

    await b.fetch()  # expired -> conditional GET -> 304
    assert calls == []  # not rebuilt
    assert b._expiry > time.monotonic()  # expiry renewed
    assert (await b.lookup_service("example.com")).base_url == "https://v"


async def test_200_on_refresh_reindexes(monkeypatch) -> None:
    _patch_get(
        monkeypatch,
        [
            _response(200, services=[[["com"], ["https://old/"]]], max_age=60),
            _response(200, services=[[["com"], ["https://new/"]]], max_age=60),
        ],
    )
    b = RDAPDomainBootstrap()
    await b.fetch()
    assert (await b.lookup_service("example.com")).base_url == "https://old"  # fresh
    b._expiry = time.monotonic() - 1  # force expiry
    await b.fetch()  # 200 with changed data -> rebuild
    assert (await b.lookup_service("example.com")).base_url == "https://new"


async def test_conditional_headers_sent_after_etag_known(monkeypatch) -> None:
    sent = _patch_get(
        monkeypatch,
        [
            _response(200, services=[[["com"], ["https://v/"]]], etag="v1", max_age=0),
            _response(304),
        ],
    )
    b = RDAPDomainBootstrap()
    await b.fetch()
    await b.fetch()
    assert sent[0]["If-None-Match"] is None  # first request: no validator yet
    assert sent[1]["If-None-Match"] == "v1"  # second: conditional


async def test_serve_stale_on_refresh_failure(monkeypatch) -> None:
    _patch_get(
        monkeypatch, [_response(200, services=[[["com"], ["https://v/"]]], max_age=0)]
    )
    b = RDAPDomainBootstrap()
    await b.fetch()  # loaded, immediately expired

    async def boom(self, source):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(
        "rdap_proxy.services.rdap.bootstrap.RDAPBootstrap._conditional_get", boom
    )
    await b.fetch()  # must not raise
    assert (await b.lookup_service("example.com")).base_url == "https://v"  # stale kept
    assert b._expiry > time.monotonic()  # expiry extended


async def test_initial_failure_raises(monkeypatch) -> None:
    async def boom(self, source):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(
        "rdap_proxy.services.rdap.bootstrap.RDAPBootstrap._conditional_get", boom
    )
    b = RDAPDomainBootstrap()
    with pytest.raises(RDAPBootstrapError):
        await b.fetch()


# --- Stale-while-revalidate ----------------------------------------------------


async def test_stale_lookup_serves_old_data_then_refreshes(monkeypatch) -> None:
    _patch_get(monkeypatch, [_response(200, services=[[["com"], ["https://new/"]]])])
    b = RDAPDomainBootstrap()
    b._build_index([[["com"], ["https://old/"]]])
    b._loaded = True
    b._expiry = time.monotonic() - 1  # loaded but stale

    # Lookup serves the stale index immediately; the background task has been
    # scheduled but cannot have run yet (no intervening await on the loop).
    service = await b.lookup_service("example.com")
    assert service.base_url == "https://old"
    assert b._refresh_task is not None

    await b._refresh_task  # let the background refresh complete
    assert (await b.lookup_service("example.com")).base_url == "https://new"


async def test_concurrent_stale_lookups_trigger_single_refresh(monkeypatch) -> None:
    sent = _patch_get(
        monkeypatch, [_response(200, services=[[["com"], ["https://new/"]]])]
    )
    b = RDAPDomainBootstrap()
    b._build_index([[["com"], ["https://old/"]]])
    b._loaded = True
    b._expiry = time.monotonic() - 1  # stale

    results = await asyncio.gather(*(b.lookup_service("example.com") for _ in range(5)))
    assert all(s.base_url == "https://old" for s in results)  # all served stale

    await b._refresh_task
    assert len(sent) == len(b._sources)  # exactly one refresh round, not one per reader


async def test_cold_lookup_blocks_until_loaded(monkeypatch) -> None:
    _patch_get(monkeypatch, [_response(200, services=[[["com"], ["https://v/"]]])])
    b = RDAPDomainBootstrap()  # not loaded
    service = await b.lookup_service("example.com")  # cold path must block and fetch
    assert service is not None and service.base_url == "https://v"
    assert b._loaded


async def test_background_refresh_failure_keeps_stale(monkeypatch) -> None:
    b = RDAPDomainBootstrap()
    b._build_index([[["com"], ["https://old/"]]])
    b._loaded = True
    b._expiry = time.monotonic() - 1  # stale

    async def boom(self, source):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(
        "rdap_proxy.services.rdap.bootstrap.RDAPBootstrap._conditional_get", boom
    )
    service = await b.lookup_service("example.com")
    assert service.base_url == "https://old"  # stale served immediately

    await b._refresh_task  # must not raise
    assert (await b.lookup_service("example.com")).base_url == "https://old"  # kept
    assert b._expiry > time.monotonic()  # expiry extended by stale TTL
