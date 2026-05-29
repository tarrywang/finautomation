"""/profile — current user's settings + self-service password change."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ...warehouse.models import User, UserCompanyAccess, Company
from ..auth import get_current_user, get_or_make_csrf
from ..deps import get_db, templates
from ..security import check_password_policy, hash_password, verify_password
from sqlalchemy import select

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_class=HTMLResponse)
def profile_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HTMLResponse:
    # Show user's company scope
    if user.role == "admin":
        scopes = []  # template handles 'all' display
    else:
        scopes = db.execute(
            select(UserCompanyAccess, Company)
            .join(Company, Company.tax_no == UserCompanyAccess.tax_no)
            .where(UserCompanyAccess.user_id == user.id)
            .order_by(Company.tax_no)
        ).all()
    return templates.TemplateResponse(
        request, "profile.html",
        {
            "user": user,
            "scopes": scopes,
            "csrf_token": get_or_make_csrf(request),
        },
    )


@router.get("/password", response_class=HTMLResponse)
def password_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    forced: int = 0,
    success: int = 0,
    error: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "profile_password.html",
        {
            "user": user,
            "forced": bool(forced),
            "success": bool(success),
            "error": error,
            "csrf_token": get_or_make_csrf(request),
        },
    )


@router.post("/password")
def change_password(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    csrf_token: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
) -> Response:
    # CSRF was verified by middleware

    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(
            url="/profile/password?error=current", status_code=302
        )
    if new_password != new_password_confirm:
        return RedirectResponse(
            url="/profile/password?error=mismatch", status_code=302
        )
    err = check_password_policy(new_password, user.username)
    if err:
        return RedirectResponse(
            url=f"/profile/password?error={err}", status_code=302
        )

    from datetime import datetime, timezone
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/profile/password?success=1", status_code=302)
