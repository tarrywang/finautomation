"""/admin/users — admin-only user CRUD + scope (company access) management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...warehouse.models import Company, User, UserCompanyAccess
from ..auth import get_or_make_csrf, require_role
from ..deps import get_db, templates
from ..security import check_password_policy, hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN = require_role("admin")


# ─────────────────────── List ───────────────────────

@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(_ADMIN)],
    success: str = "",
    error: str = "",
) -> HTMLResponse:
    users = db.execute(select(User).order_by(User.id)).scalars().all()
    return templates.TemplateResponse(
        request, "admin_users_list.html",
        {
            "users": users,
            "csrf_token": get_or_make_csrf(request),
            "success": success,
            "error": error,
        },
    )


# ─────────────────────── Create ───────────────────────

@router.get("/users/new", response_class=HTMLResponse)
def user_new_page(
    request: Request,
    _admin: Annotated[User, Depends(_ADMIN)],
    error: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "admin_user_form.html",
        {"u": None, "error": error, "csrf_token": get_or_make_csrf(request)},
    )


@router.post("/users")
def user_create(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
    username: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("operator"),
    password: str = Form(""),
    must_change_password: str = Form(""),
) -> Response:
    username = username.strip()
    if not username or len(username) < 3:
        return RedirectResponse(url="/admin/users/new?error=用户名至少 3 位", status_code=302)
    if role not in ("admin", "supervisor", "operator"):
        return RedirectResponse(url="/admin/users/new?error=非法角色", status_code=302)
    err = check_password_policy(password, username)
    if err:
        return RedirectResponse(url=f"/admin/users/new?error={err}", status_code=302)

    exists = db.execute(select(User).where(User.username == username)).scalars().first()
    if exists:
        return RedirectResponse(
            url=f"/admin/users/new?error=用户名 {username!r} 已存在",
            status_code=302,
        )

    u = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip() or None,
        email=email.strip() or None,
        role=role,
        is_active=True,
        must_change_password=bool(must_change_password),
        created_by=admin.id,
    )
    db.add(u)
    db.commit()
    return RedirectResponse(
        url=f"/admin/users/{u.id}/scope?success=用户 {username} 已创建",
        status_code=302,
    )


# ─────────────────────── Edit ───────────────────────

@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_edit_page(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(_ADMIN)],
    error: str = "",
    success: str = "",
) -> HTMLResponse:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "admin_user_form.html",
        {
            "u": u, "error": error, "success": success,
            "csrf_token": get_or_make_csrf(request),
        },
    )


@router.post("/users/{user_id}")
def user_update(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("operator"),
    is_active: str = Form(""),
    new_password: str = Form(""),
    force_change: str = Form(""),
) -> Response:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if role not in ("admin", "supervisor", "operator"):
        return RedirectResponse(url=f"/admin/users/{user_id}?error=非法角色", status_code=302)

    # Prevent admin from disabling/demoting their own account
    if u.id == admin.id:
        if role != "admin":
            return RedirectResponse(
                url=f"/admin/users/{user_id}?error=不能修改自己的角色",
                status_code=302,
            )
        if not is_active:
            return RedirectResponse(
                url=f"/admin/users/{user_id}?error=不能禁用自己",
                status_code=302,
            )

    u.display_name = display_name.strip() or None
    u.email = email.strip() or None
    u.role = role
    u.is_active = bool(is_active)
    if new_password:
        err = check_password_policy(new_password, u.username)
        if err:
            return RedirectResponse(url=f"/admin/users/{user_id}?error={err}", status_code=302)
        u.password_hash = hash_password(new_password)
        u.password_changed_at = datetime.now(timezone.utc)
        u.must_change_password = bool(force_change)
        u.failed_login_count = 0
        u.locked_until = None
    db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}?success=已保存",
        status_code=302,
    )


@router.post("/users/{user_id}/unlock")
def user_unlock(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
) -> Response:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    u.failed_login_count = 0
    u.locked_until = None
    db.commit()
    return RedirectResponse(url=f"/admin/users/{user_id}?success=已解锁", status_code=302)


@router.post("/users/{user_id}/delete")
def user_delete(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
) -> Response:
    if user_id == admin.id:
        return RedirectResponse(url="/admin/users?error=不能删自己", status_code=302)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    db.delete(u)
    db.commit()
    return RedirectResponse(
        url=f"/admin/users?success=已删除 {u.username}", status_code=302
    )


# ─────────────────────── Scope (company access) ───────────────────────

@router.get("/users/{user_id}/scope", response_class=HTMLResponse)
def user_scope_page(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(_ADMIN)],
    success: str = "",
    error: str = "",
) -> HTMLResponse:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)

    # Currently granted (with company info)
    granted = db.execute(
        select(UserCompanyAccess, Company)
        .join(Company, Company.tax_no == UserCompanyAccess.tax_no)
        .where(UserCompanyAccess.user_id == user_id)
        .order_by(Company.tax_no)
    ).all()
    granted_taxnos = {a.tax_no for a, _ in granted}

    # All self companies (admin can grant any)
    all_self = db.execute(
        select(Company).where(Company.is_self.is_(True)).order_by(Company.tax_no)
    ).scalars().all()
    available = [c for c in all_self if c.tax_no not in granted_taxnos]

    return templates.TemplateResponse(
        request, "admin_user_scope.html",
        {
            "u": u,
            "granted": granted,
            "available": available,
            "success": success,
            "error": error,
            "csrf_token": get_or_make_csrf(request),
        },
    )


@router.post("/users/{user_id}/scope")
def user_scope_grant(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
    tax_no: str = Form(""),
    permission: str = Form("view"),
) -> Response:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if permission not in ("view", "crud"):
        return RedirectResponse(
            url=f"/admin/users/{user_id}/scope?error=非法权限", status_code=302
        )
    existing = db.get(UserCompanyAccess, (user_id, tax_no))
    if existing:
        existing.permission = permission
        existing.granted_by = admin.id
    else:
        db.add(UserCompanyAccess(
            user_id=user_id, tax_no=tax_no,
            permission=permission, granted_by=admin.id,
        ))
    db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}/scope?success=已授权 {tax_no} ({permission})",
        status_code=302,
    )


@router.post("/users/{user_id}/scope/{tax_no}/revoke")
def user_scope_revoke(
    user_id: int,
    tax_no: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(_ADMIN)],
    csrf_token: str = Form(""),
) -> Response:
    access = db.get(UserCompanyAccess, (user_id, tax_no))
    if access:
        db.delete(access)
        db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}/scope?success=已撤销 {tax_no}",
        status_code=302,
    )
