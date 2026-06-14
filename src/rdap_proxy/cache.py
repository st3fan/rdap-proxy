from pathlib import Path
from urllib.parse import urlsplit

from litestar.stores.base import Store
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore


def build_store(url: str) -> Store:
    """Create a Litestar Store from a cache URL, dispatching on its scheme."""
    parsed = urlsplit(url)
    match parsed.scheme:
        case "redis" | "rediss":
            return RedisStore.with_client(url=url)
        case "memory":
            return MemoryStore()
        case "file":
            return FileStore(path=Path(parsed.path or "."), create_directories=True)
        case _:
            raise ValueError(f"Unsupported cache_url scheme: {parsed.scheme!r}")
