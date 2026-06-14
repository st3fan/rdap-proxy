# rdap-proxy

A small [RDAP](https://en.wikipedia.org/wiki/Registration_Data_Access_Protocol)
proxy. Given an object type and a value, it figures out which registry is
authoritative for that object — using IANA's bootstrap data — and proxies the
RDAP lookup to that registry, optionally caching the result.

RDAP (Registration Data Access Protocol, RFC 9082/9083) is the modern,
JSON-over-HTTP successor to WHOIS for looking up registration data about
domains, IP addresses, and autonomous system numbers.

> [!WARNING]
> **This project is under active development and is not production-ready.**
> APIs, configuration, and behavior may change without notice. There are
> **no hardened production deployment instructions yet** — the guidance below
> covers local development only. Do not rely on this for anything important.

## What it does

Instead of you needing to know that `.com` is served by Verisign or that
`1.1.1.1` belongs to APNIC, you ask this proxy and it resolves the authoritative
RDAP service for you:

1. Parse the object type and value from the request path.
2. Resolve the authoritative RDAP base URL from IANA bootstrap data
   (`dns.json`, `ipv4.json`, `ipv6.json`, `asn.json`).
3. Proxy the query to that registry and return its RDAP JSON response.
4. Optionally cache successful responses.

## API

A single endpoint dispatches by object type:

```
GET /rdap/{object_type}/{value}
```

| Type         | Example                                   | Resolved via                          |
| ------------ | ----------------------------------------- | ------------------------------------- |
| `domain`     | `/rdap/domain/example.com`                | DNS bootstrap (longest TLD suffix)    |
| `ip`         | `/rdap/ip/1.1.1.1`, `/rdap/ip/2606:4700::1111` | IPv4/IPv6 bootstrap (longest-prefix CIDR) |
| `autnum`     | `/rdap/autnum/15169`, `/rdap/autnum/AS15169` | ASN bootstrap (integer range)         |
| `nameserver` | `/rdap/nameserver/ns1.example.com`        | DNS bootstrap (parent domain)         |

Notes:

- Unknown object types and `entity` return `404` — `entity` has no IANA
  bootstrap, so it can't be resolved to a registry deterministically.
- Invalid input (e.g. a malformed IP or AS number) returns `404`.
- Upstream/registry failures return `502`.
- `GET /health` returns `{"status": "ok"}`.

## Requirements

- Python **3.13+**
- [uv](https://docs.astral.sh/uv/) for dependency management

## Running locally (development)

Install dependencies and start the dev server (auto-reload enabled):

```bash
uv sync
uv run dev
```

By default it listens on `http://127.0.0.1:8000`. Try a lookup:

```bash
curl -s http://127.0.0.1:8000/rdap/domain/example.com | jq
curl -s http://127.0.0.1:8000/rdap/ip/1.1.1.1 | jq
```

### Development configuration (`.env`)

Configuration is read from environment variables (and a local `.env` file,
which is git-ignored). `settings.py` holds **production** defaults; for local
development create a `.env` in the project root, for example:

```dotenv
DEBUG=true
LOG_LEVEL=DEBUG
CACHE_URL=memory://
LOG_EXCEPTIONS=always
```

This enables debug mode, verbose logging, and an in-process cache so you don't
need Redis while developing.

### Configuration reference

| Setting          | Env var          | Default          | Description                                                        |
| ---------------- | ---------------- | ---------------- | ------------------------------------------------------------------ |
| `debug`          | `DEBUG`          | `false`          | Litestar debug mode.                                               |
| `log_level`      | `LOG_LEVEL`      | `INFO`           | Root log level (`DEBUG` to see e.g. bootstrap index timings).      |
| `log_exceptions` | `LOG_EXCEPTIONS` | `always`         | When to log exceptions: `always` / `debug` / `never`.              |
| `host`           | `HOST`           | `127.0.0.1`      | Bind address.                                                      |
| `port`           | `PORT`           | `8000`           | Bind port.                                                         |
| `cache_url`      | `CACHE_URL`      | `redis://redis`  | Cache backend. Unset disables caching. See below.                 |
| `cache_ttl`      | `CACHE_TTL`      | `3600`           | Cache TTL in seconds.                                              |

### Caching

Response caching is optional and backend-agnostic, selected by the `cache_url`
scheme:

- `redis://host:6379/0` — Redis (intended for production)
- `memory://` — in-process cache (handy for development)
- `file://./cache` — on-disk cache

When `cache_url` is set, successful (`2xx`) lookups are cached for `cache_ttl`
seconds. Leave it unset to disable caching entirely.

## Tests

```bash
uv run test
```

## Project layout

```
src/rdap_proxy/
  main.py                  # Litestar app, DI, startup, exception/logging config
  settings.py              # pydantic-settings configuration
  cache.py                 # cache_url -> Litestar Store factory
  routers/
    health.py              # GET /health
    rdap.py                # GET /rdap/{object_type}/{value} + dispatch
  services/rdap/
    models.py              # RDAPQueryType enum
    resolver.py            # typed lookup methods, singleton, warm()
    bootstrap.py           # IANA bootstrap fetch + index (domain/ip/asn)
    service.py             # per-registry RDAP HTTP client
    exceptions.py          # RDAP error types
tests/                     # dispatch, cache factory, bootstrap resolution
```

## Status & roadmap

This is a work in progress. Known gaps / things still to do:

- **No production deployment story yet** — no container image, process manager
  guidance, health/readiness wiring, or hardening notes.
- `entity` lookups are not supported.
- Bootstrap data is fetched at startup and cached for the process lifetime;
  there is no periodic refresh.
- More tests and end-to-end coverage are needed.

## License

MIT — see [LICENSE](LICENSE).
