from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "rdap-proxy"
    debug: bool = False
    log_level: str = "INFO"
    log_exceptions: Literal["always", "debug", "never"] = "always"

    host: str = "127.0.0.1"
    port: int = 8000

    # Optional response cache. When cache_url is set, RDAP lookups are cached for
    # cache_ttl seconds. Scheme selects the backend, e.g. redis://localhost:6379/0,
    # memory://, or file://./cache. Unset disables caching.
    cache_url: str | None = "redis://redis"
    cache_ttl: int = 3600


settings = Settings()
