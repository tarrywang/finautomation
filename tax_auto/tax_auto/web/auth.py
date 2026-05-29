"""Web auth: multi-user login, session cookie, role + scope guards.

Flow:
  POST /login (username, password)
    1. rate-limit by IP (10 fails / 5min via in-memory bucket)
    2. find user; if locked_until > now → refuse
    3. verify password
       ├ ok  → reset failed_login_count, set last_login_*, write session.uid
       └ bad → failed_login_count++; if >= 5 → locked_until = now+30min
    4. redirect to ?next (only internal paths)

Dependencies (used by routes):
  get_current_user(request) → User      # raises 401 / 302 if missing
  require_role("admin", ...)            # raise 403 if wrong role
  get_user_scope_taxnos(user, db)       # set[str] | None (None = admin/all)
  require_company_access(tax_no, *crud) # raise 404 if not in scope
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from ..warehouse.models import User, UserCompanyAccess
from .deps import get_db, templates
from .security import (
    generate_csrf_token,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

SESSION_UID_KEY = "uid"
SESSION_CSRF_KEY = "csrf"
SESSION_LOGIN_TS_KEY = "login_ts"

# Routes that don't require auth at all
_PUBLIC_PATHS: set[str] = {"/login", "/logout", "/healthz"}
_PUBLIC_PREFIXES: tuple[str, ...] = ("/static/",)

# Login rate-limit config
_MAX_LOGIN_ATTEMPTS_PER_IP = 10  # 10 attempts
_LOGIN_RATE_WINDOW_S = 5 * 60  # in 5 min
_MAX_USER_FAILED = 5  # user fails 5x → lock
_USER_LOCKOUT_DURATION_S = 30 * 60  # 30 min


# ─── In-memory IP rate limiter ──────────────────────────────────────
# Simple deque-per-IP, single-process. Fine for single uvicorn worker.
# If we ever go multi-worker / multi-host: replace with Postgres-backed.

_ip_attempts: dict[str, deque[float]] = defaultdict(deque)


def _is_ip_rate_limited(ip: str) -> bool:
    now = time.time()
    q = _ip_attempts[ip]
    while q and q[0] < now - _LOGIN_RATE_WINDOW_S:
        q.popleft()
    return len(q) >= _MAX_LOGIN_ATTEMPTS_PER_IP


def _record_ip_attempt(ip: str) -> None:
    _ip_attempts[ip].append(time.time())


# ─── Helpers ─────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def _logged_in_uid(request: Request) -> int | None:
    try:
        v = request.session.get(SESSION_UID_KEY)
        return int(v) if v else None
    except (AssertionError, TypeError, ValueError):
        return None


# ─── Middleware: redirect unauthenticated to /login ──────────────────


class AuthRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if is_public_path(request.url.path) or _logged_in_uid(request) is not None:
            return await call_next(request)
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}", status_code=302)


class LoadCurrentUserMiddleware(BaseHTTPMiddleware):
    """Populate request.state.current_user so templates can read user info
    without each route having to pass it explicitly."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.current_user = None
        uid = _logged_in_uid(request)
        if uid is not None:
            from ..warehouse.session import get_sessionmaker

            sm = get_sessionmaker()
            with sm() as s:
                user = s.get(User, uid)
                if user and user.is_active:
                    # Detach so it's safe to use outside the session
                    s.expunge(user)
                    request.state.current_user = user
        # Also expose CSRF token if there's a session
        if request.session.get(SESSION_UID_KEY) or is_public_path(request.url.path):
            request.state.csrf_token = get_or_make_csrf(request) if request.session else ""
        else:
            request.state.csrf_token = ""
        return await call_next(request)


# ─── Dependencies for routes ─────────────────────────────────────────


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the User from session. Raises 401 if not authed or user gone."""
    uid = _logged_in_uid(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        # Stale session — clear it
        request.session.clear()
        raise HTTPException(status_code=401, detail="User not found or inactive")
    # Inject into request.state for downstream loggers
    request.state.current_user = user
    return user


def require_role(*allowed_roles: str) -> Callable[..., User]:
    """Return a dependency that gates on role(s)."""

    def dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="无权访问该资源")
        return user

    return dep


def get_user_scope_taxnos(user: User, db: Session) -> set[str] | None:
    """Tax numbers the user can access. None = admin (all)."""
    if user.role == "admin":
        return None
    rows = (
        db.execute(select(UserCompanyAccess.tax_no).where(UserCompanyAccess.user_id == user.id))
        .scalars()
        .all()
    )
    return set(rows)


def get_user_crud_taxnos(user: User, db: Session) -> set[str] | None:
    """Tax numbers user can MUTATE (write). None = admin (all)."""
    if user.role == "admin":
        return None
    if user.role != "supervisor":  # operator can never crud
        return set()
    rows = (
        db.execute(
            select(UserCompanyAccess.tax_no).where(
                UserCompanyAccess.user_id == user.id,
                UserCompanyAccess.permission == "crud",
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def assert_can_view(user: User, db: Session, tax_no: str) -> None:
    """Raise 404 (not 403, to avoid resource enumeration) if user can't view that tax_no."""
    scope = get_user_scope_taxnos(user, db)
    if scope is None:
        return
    if tax_no not in scope:
        raise HTTPException(status_code=404, detail="Not found")


def assert_can_crud(user: User, db: Session, tax_no: str) -> None:
    """Raise 403 if user can't mutate that tax_no."""
    scope = get_user_crud_taxnos(user, db)
    if scope is None:
        return
    if tax_no not in scope:
        raise HTTPException(status_code=403, detail="无操作权限")


# ─── CSRF (synchronizer token in session) ────────────────────────────


def get_or_make_csrf(request: Request) -> str:
    tok = request.session.get(SESSION_CSRF_KEY)
    if not tok:
        tok = generate_csrf_token()
        request.session[SESSION_CSRF_KEY] = tok
    return tok


def verify_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get(SESSION_CSRF_KEY) or ""
    if not submitted or not expected:
        raise HTTPException(status_code=403, detail="CSRF token missing")
    import hmac as _hmac

    if not _hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8")):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


class CSRFProtectMiddleware(BaseHTTPMiddleware):
    """Enforce CSRF on POST/PATCH/DELETE/PUT requests outside the public list."""

    _EXEMPT_PATHS: ClassVar[set[str]] = {"/login"}  # login form has its own CSRF check inline

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        method = request.method.upper()
        if method not in {"POST", "PATCH", "PUT", "DELETE"}:
            return await call_next(request)
        if request.url.path in self._EXEMPT_PATHS:
            return await call_next(request)
        if is_public_path(request.url.path):
            return await call_next(request)
        # Pull token from form or header
        body_token: str | None = None
        try:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith(
                "application/x-www-form-urlencoded"
            ) or content_type.startswith("multipart/form-data"):
                # We need the form; awkward — we cache it on state
                form = await request.form()
                request.state._cached_form = form
                body_token = form.get("csrf_token")  # type: ignore[assignment]
        except Exception:
            pass
        header_token = request.headers.get("x-csrf-token")
        submitted = body_token or header_token
        try:
            verify_csrf(request, submitted)
        except HTTPException as e:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        return await call_next(request)


# ─── Routes ─────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    error: str = "",
    locked: int = 0,
) -> HTMLResponse:
    if _logged_in_uid(request) is not None:
        return RedirectResponse(url=next or "/", status_code=302)  # type: ignore[return-value]
    csrf = get_or_make_csrf(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": next or "/", "error": error, "locked_min": locked, "csrf_token": csrf},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
) -> Response:
    verify_csrf(request, csrf_token)

    ip = _client_ip(request)
    if _is_ip_rate_limited(ip):
        logger.warning("login rate-limited for ip=%s", ip)
        return RedirectResponse(
            url=f"/login?error=ratelimit&next={quote(next, safe='')}", status_code=302
        )
    _record_ip_attempt(ip)

    user = (
        db.execute(select(User).where(User.username == username, User.is_active.is_(True)))
        .scalars()
        .first()
    )

    now = datetime.now(UTC)

    if user is None:
        logger.warning("login fail: unknown username=%r ip=%s", username, ip)
        return RedirectResponse(url=f"/login?error=1&next={quote(next, safe='')}", status_code=302)

    # Check lockout
    if user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() / 60) + 1
        logger.warning("login refused: user %s is locked for %dm", user.username, remaining)
        return RedirectResponse(
            url=f"/login?locked={remaining}&next={quote(next, safe='')}", status_code=302
        )

    # Verify password
    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= _MAX_USER_FAILED:
            user.locked_until = now + timedelta(seconds=_USER_LOCKOUT_DURATION_S)
            user.failed_login_count = 0
            logger.warning(
                "login lockout: user %s locked %dmin", user.username, _USER_LOCKOUT_DURATION_S // 60
            )
        db.commit()
        return RedirectResponse(url=f"/login?error=1&next={quote(next, safe='')}", status_code=302)

    # Success
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    db.commit()

    request.session[SESSION_UID_KEY] = user.id
    request.session[SESSION_LOGIN_TS_KEY] = int(now.timestamp())
    # Rotate CSRF on login (session-fixation defence)
    request.session[SESSION_CSRF_KEY] = generate_csrf_token()
    logger.info("login OK: user=%s ip=%s", user.username, ip)

    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"

    # First-time-login forces password change
    if user.must_change_password:
        return RedirectResponse(url="/profile/password?forced=1", status_code=302)
    return RedirectResponse(url=safe_next, status_code=302)


@router.get("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
