"""Tests for RDAPService query behavior and HTTP client reuse."""

import httpx

from rdap_proxy.services.rdap.models import RDAPQueryType
from rdap_proxy.services.rdap.service import RDAPService


async def test_query_reuses_injected_client() -> None:
    # A MockTransport-backed client proves the injected client is used: a
    # throwaway fallback client would not route through this handler.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"objectClassName": "domain"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = RDAPService("https://rdap.example/", client=client)

    a = await service.query("example.com", RDAPQueryType.DOMAIN)
    b = await service.query("example.org", RDAPQueryType.DOMAIN)
    await client.aclose()

    assert a == {"objectClassName": "domain"}
    assert b == {"objectClassName": "domain"}
    assert seen == [
        "https://rdap.example/domain/example.com",
        "https://rdap.example/domain/example.org",
    ]  # both requests went through the one injected client
