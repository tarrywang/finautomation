"""FastAPI entrypoint for the invoice warehouse web UI.

Run:
    uv run uvicorn tax_auto.web.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from financeautomation/ before importing anything DB-touching
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV = _PROJECT_ROOT / ".env"
if _ENV.exists():
    load_dotenv(_ENV)

from fastapi import FastAPI  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from . import auth as auth_module  # noqa: E402
from .routes import admin_users, dashboard, invoices, profile  # noqa: E402

app = FastAPI(title="发票通仓库", version="0.1.0")

# ─── Auth ─────────────────────────────────────────────────────────────
# Middleware add order: applied OUTSIDE-IN.
# So request flows: SessionMiddleware → CSRFProtect → AuthRedirect → routes
_session_secret = os.environ.get("SESSION_SECRET")
if not _session_secret or len(_session_secret) < 32:
    raise RuntimeError(
        "SESSION_SECRET must be set in .env (>= 32 chars). "
        "Generate: python3 -c 'import secrets; print(secrets.token_hex(32))'"
    )

# Detect HTTPS deployment via env var
_is_https = os.environ.get("WEB_HTTPS", "").lower() in ("1", "true", "yes")
_session_max_age_h = int(os.environ.get("SESSION_MAX_AGE_HOURS", "8"))

app.add_middleware(auth_module.AuthRedirectMiddleware)
app.add_middleware(auth_module.LoadCurrentUserMiddleware)
app.add_middleware(auth_module.CSRFProtectMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie="fapiao_session",
    max_age=60 * 60 * _session_max_age_h,
    same_site="lax",
    https_only=_is_https,
)

app.include_router(auth_module.router)
app.include_router(profile.router)
app.include_router(admin_users.router)
app.include_router(dashboard.router)
app.include_router(invoices.router)


# Expose current user to all templates via a context processor-like injection.
# Jinja2Templates doesn't support context processors directly; we set request.state
# in get_current_user, and templates can read it via request.state.
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if _is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
