from litestar import Router, get


@get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


router = Router(path="/", route_handlers=[health_check])
