from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings, validate_pricing_coverage
from app.db import async_session
from app.jwt_keys import ensure_keys_exist
from app.migrate import run_migrations
from app.routers import admin, chat
from app.seed import seed_admin_user, seed_virtual_keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_pricing_coverage()
    ensure_keys_exist()
    if get_settings().run_migrations_on_startup:
        await run_migrations()
    async with async_session() as session:
        await seed_virtual_keys(session)
        await seed_admin_user(session)
    yield


app = FastAPI(title="Prism Gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)
app.include_router(admin.router)


@app.exception_handler(HTTPException)
async def gateway_error_handler(request, exc: HTTPException):
    """GatewayError puts an OpenAI-shaped {"error": {...}} dict in exc.detail;
    return it as the top-level body instead of Starlette's default {"detail": ...} wrapper."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
