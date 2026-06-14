from typing import Any

import httpx

from .exceptions import RDAPNotFoundError, RDAPServiceError
from .models import RDAPQueryType


class RDAPService:
    def __init__(
        self, base_url: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    def _build_url(self, query: str, query_type: RDAPQueryType) -> str:
        match query_type:
            case RDAPQueryType.DOMAIN:
                return f"{self.base_url}/domain/{query.lower()}"
            case RDAPQueryType.IP:
                return f"{self.base_url}/ip/{query}"
            case RDAPQueryType.AUTNUM:
                asn = str(query).upper().removeprefix("AS")
                return f"{self.base_url}/autnum/{asn}"
            case RDAPQueryType.NAMESERVER:
                return f"{self.base_url}/nameserver/{query.lower()}"
            case RDAPQueryType.ENTITY:
                return f"{self.base_url}/entity/{query}"
            case _:
                raise ValueError(f"Unknown query type: {query_type!r}")

    async def query(self, query: str, query_type: RDAPQueryType) -> Any:
        url = self._build_url(query, query_type)
        try:
            response = await self._get(url)
        except httpx.HTTPError as e:
            raise RDAPServiceError(
                f"HTTP error querying RDAP service at {url}: {e}"
            ) from e

        if response.status_code == 404:
            raise RDAPNotFoundError(f"No RDAP record found for {query_type} {query!r}")

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise RDAPServiceError(
                f"Rate limited by RDAP service at {self.base_url} "
                f"(Retry-After: {retry_after})"
            )

        if response.status_code >= 400:
            raise RDAPServiceError(
                f"RDAP service returned {response.status_code} for {url}"
            )

        try:
            data = response.json()
        except Exception as e:
            raise RDAPServiceError(f"Invalid JSON in RDAP response from {url}") from e

        # TODO If we do an RDAPResponse it should be an RDAPProxyResponse that has the origin service and the data
        # return RDAPResponse.from_dict(data)

        return data

    async def _get(self, url: str) -> httpx.Response:
        """GET via the shared client, or a throwaway one if none was injected."""
        headers = {"Accept": "application/rdap+json"}
        if self._client is not None:
            return await self._client.get(
                url, headers=headers, follow_redirects=True, timeout=10.0
            )
        async with httpx.AsyncClient() as client:
            return await client.get(
                url, headers=headers, follow_redirects=True, timeout=10.0
            )

    def __repr__(self) -> str:
        return f"RDAPService(base_url={self.base_url!r})"
