import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.background import maintenance_loop
from app.config import get_settings, validate_pricing_coverage, validate_production_settings
from app.credential_crypto import ensure_fernet_key_exists
from app.db import async_session
from app.jwt_keys import ensure_keys_exist
from app.logging_config import configure_logging
from app.migrate import run_migrations
from app.routers import admin, chat, user
from app.seed import seed_admin_user, seed_virtual_keys

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Both validators fail the process rather than degrading: an unpriced
    # model silently meters as free, and a default admin password on a
    # production deployment is a permanent hole once seeded.
    validate_production_settings(settings)
    validate_pricing_coverage()
    ensure_keys_exist()
    ensure_fernet_key_exists()
    if settings.run_migrations_on_startup:
        await run_migrations()
    async with async_session() as session:
        await seed_virtual_keys(session)
        await seed_admin_user(session)

    maintenance: asyncio.Task | None = None
    if settings.maintenance_enabled:
        maintenance = asyncio.create_task(maintenance_loop())

    # CORS origins are logged because a mismatch here is invisible from the
    # server's side - the browser blocks the call and the console shows a
    # generic "Failed to fetch" while the API looks perfectly healthy. Seeing
    # the effective list at startup turns that into a two-second check.
    logger.info(
        "gateway ready",
        extra={
            "environment": settings.environment,
            "cors_allow_origins": settings.cors_allow_origin_list,
        },
    )
    try:
        yield
    finally:
        if maintenance is not None:
            maintenance.cancel()
            try:
                await maintenance
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Prism Gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(user.router)


@app.exception_handler(HTTPException)
async def gateway_error_handler(request, exc: HTTPException):
    """GatewayError puts an OpenAI-shaped {"error": {...}} dict in exc.detail;
    return it as the top-level body instead of Starlette's default {"detail": ...} wrapper."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc: Exception):
    """Anything that escapes a route handler. Without this, Starlette returns
    a bare 500 whose body doesn't match the OpenAI-shaped error contract every
    other path honors, and the traceback goes nowhere structured.

    The message is deliberately generic - an internal exception string can
    leak schema or credential detail to a caller who shouldn't see it. The
    real traceback goes to the log."""
    logger.exception(
        "unhandled error", extra={"path": request.url.path, "method": request.method}
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "internal_error",
                "code": "internal_error",
            }
        },
    )
