"""Tests for the cache store factory in rdap_proxy.cache."""

import pytest
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

from rdap_proxy.cache import build_store


def test_memory_url_builds_memory_store() -> None:
    assert isinstance(build_store("memory://"), MemoryStore)


def test_file_url_builds_file_store(tmp_path) -> None:
    store = build_store(f"file://{tmp_path}/cache")
    assert isinstance(store, FileStore)


@pytest.mark.parametrize("url", ["redis://localhost:6379/0", "rediss://localhost:6379"])
def test_redis_url_builds_redis_store(url: str) -> None:
    # Constructs a client without connecting.
    assert isinstance(build_store(url), RedisStore)


@pytest.mark.parametrize("url", ["postgres://x", "http://x", "nonsense"])
def test_unsupported_scheme_raises(url: str) -> None:
    with pytest.raises(ValueError):
        build_store(url)
