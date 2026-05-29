"""Dashboard route — scoped to user's accessible companies."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, get_user_scope_taxnos
from ..deps import get_db, templates
from ...warehouse.models import Company, Invoice, SyncRun, User

router = APIRouter()


def _scope_invoices(stmt: Select, user: User, db: Session) -> Select:
    scope = get_user_scope_taxnos(user, db)
    if scope is None:
        return stmt
    if not scope:
        return stmt.where(Invoice.tax_no == "__none__")
    return stmt.where(Invoice.tax_no.in_(scope))


def _scope_syncruns(stmt: Select, user: User, db: Session) -> Select:
    scope = get_user_scope_taxnos(user, db)
    if scope is None:
        return stmt
    if not scope:
        return stmt.where(SyncRun.tax_no == "__none__")
    return stmt.where(SyncRun.tax_no.in_(scope))


# ─────────────────────── Quick search ───────────────────────

@router.get("/search")
def quick_search(
    user: Annotated[User, Depends(get_current_user)],
    q: str = "",
) -> Response:
    """Top-bar quick-search: jump to invoices list with sensible filter pre-applied.

    Heuristics:
      - all digits, 18-20 chars → 数电号码 prefix
      - else → 销方关键词 (xfsbh like)
    """
    q = q.strip()
    if not q:
        return RedirectResponse("/invoices", status_code=302)
    if q.isdigit() and 18 <= len(q) <= 22:
        # Treat as 数电号码 (sdfphm)
        return RedirectResponse(f"/invoices?xfsbh={q}", status_code=302)
    return RedirectResponse(f"/invoices?xfsbh={q}", status_code=302)


# ─────────────────────── Preset reports ───────────────────────

@router.get("/report/{name}")
def preset_report(name: str) -> Response:
    today = date.today()
    month_start = today.replace(day=1)
    week_ago = today - timedelta(days=7)
    if name == "month_input":
        return RedirectResponse(f"/invoices?data_type=1&kprq_from={month_start}&kprq_to={today}", status_code=302)
    if name == "month_output":
        return RedirectResponse(f"/invoices?data_type=2&kprq_from={month_start}&kprq_to={today}", status_code=302)
    if name == "week_new":
        return RedirectResponse("/invoices?since_hours=168", status_code=302)  # 7d * 24h
    if name == "abnormal":
        return RedirectResponse("/invoices?fpzt=已红冲-全额&fpzt=作废&fpzt=部分红冲", status_code=302)
    if name == "large":
        return RedirectResponse("/invoices?jshj_min=50000&data_type=1", status_code=302)
    return RedirectResponse("/invoices", status_code=302)


# ─────────────────────── Dashboard ───────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> HTMLResponse:
    scope = get_user_scope_taxnos(user, db)

    # ── Self-companies user can see ──
    self_q = select(Company).where(Company.is_self.is_(True))
    if scope is not None:
        if not scope:
            self_q = self_q.where(Company.tax_no == "__none__")
        else:
            self_q = self_q.where(Company.tax_no.in_(scope))
    self_companies = db.execute(self_q.order_by(Company.tax_no)).scalars().all()

    cards = []
    for c in self_companies:
        stats: dict[str, dict] = {"1": {"count": 0, "total": Decimal(0)},
                                   "2": {"count": 0, "total": Decimal(0)}}
        rows = db.execute(
            select(
                Invoice.data_type,
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.jshj), 0),
            )
            .where(Invoice.tax_no == c.tax_no)
            .group_by(Invoice.data_type)
        ).all()
        for dt, n, total in rows:
            if dt in stats:
                stats[dt] = {"count": n, "total": total}
        last_sync = db.execute(
            select(func.max(SyncRun.ended_at)).where(SyncRun.tax_no == c.tax_no)
        ).scalar()
        cards.append({
            "company": c,
            "input_count": stats["1"]["count"],
            "input_total": stats["1"]["total"],
            "output_count": stats["2"]["count"],
            "output_total": stats["2"]["total"],
            "last_sync": last_sync,
        })

    # ── Monthly trend (last 12 months, scoped) ──
    monthly_rows = db.execute(
        _scope_invoices(
            select(
                func.to_char(Invoice.kprq, "YYYY-MM").label("month"),
                Invoice.data_type,
                func.coalesce(func.sum(Invoice.jshj), 0),
            )
            .where(Invoice.kprq.isnot(None)),
            user, db,
        )
        .group_by("month", Invoice.data_type)
        .order_by("month")
    ).all()
    months = sorted({r.month for r in monthly_rows})[-12:]
    input_by_month = {r.month: float(r[2]) for r in monthly_rows if r.data_type == "1"}
    output_by_month = {r.month: float(r[2]) for r in monthly_rows if r.data_type == "2"}

    # ── Recent arrivals (last 24h, scoped) ──
    cutoff = datetime.now() - timedelta(hours=24)
    recent_summary = db.execute(
        _scope_invoices(
            select(
                Invoice.data_type,
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.jshj), 0),
            )
            .where(Invoice.first_seen_at >= cutoff),
            user, db,
        )
        .group_by(Invoice.data_type)
    ).all()
    recent_counts: dict[str, dict] = {"1": {"n": 0, "total": Decimal(0)},
                                       "2": {"n": 0, "total": Decimal(0)}}
    for dt, n, total in recent_summary:
        if dt in recent_counts:
            recent_counts[dt] = {"n": n, "total": total}

    recent_rows = db.execute(
        _scope_invoices(
            select(Invoice).where(Invoice.first_seen_at >= cutoff),
            user, db,
        )
        .order_by(Invoice.first_seen_at.desc())
        .limit(15)
    ).scalars().all()

    last_sync_run = db.execute(
        _scope_syncruns(select(SyncRun), user, db)
        .order_by(SyncRun.ended_at.desc().nullslast()).limit(1)
    ).scalar_one_or_none()

    # ── Top vendors (进项, scoped) ──
    top_vendors = db.execute(
        _scope_invoices(
            select(
                Invoice.xfmc,
                Invoice.xfsbh,
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.jshj), 0),
            )
            .where(Invoice.data_type == "1", Invoice.xfsbh.isnot(None)),
            user, db,
        )
        .group_by(Invoice.xfmc, Invoice.xfsbh)
        .order_by(func.coalesce(func.sum(Invoice.jshj), 0).desc())
        .limit(10)
    ).all()

    # ── Tax rate distribution (进项, scoped) ──
    tax_rate_rows = db.execute(
        _scope_invoices(
            select(
                Invoice.slv,
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.jshj), 0),
            )
            .where(Invoice.data_type == "1", Invoice.slv.isnot(None)),
            user, db,
        )
        .group_by(Invoice.slv)
        .order_by(func.count(Invoice.id).desc())
    ).all()

    # ── Sync health: last 7 days × self companies × {进, 销} ──
    seven_days_ago = datetime.now() - timedelta(days=7)
    sync_health_rows = db.execute(
        _scope_syncruns(
            select(
                func.date(SyncRun.started_at).label("d"),
                SyncRun.tax_no,
                SyncRun.data_type,
                SyncRun.status,
                func.count(SyncRun.id),
            ).where(SyncRun.started_at >= seven_days_ago),
            user, db,
        ).group_by("d", SyncRun.tax_no, SyncRun.data_type, SyncRun.status)
    ).all()
    # Build a per-day status: {tax_no: {date: {1: status, 2: status}}}
    sync_grid: dict = {}
    for row in sync_health_rows:
        d, tn, dt, st, _n = row
        sync_grid.setdefault(tn, {}).setdefault(str(d), {})[dt] = st

    today = date.today()
    last_7_days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]

    # ── 异常发票 count (作废 / 红冲) ──
    abnormal_count = db.execute(
        _scope_invoices(
            select(func.count(Invoice.id)).where(
                or_(
                    Invoice.fpzt.like("%红冲%"),
                    Invoice.fpzt == "作废",
                ),
            ),
            user, db,
        )
    ).scalar_one()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "cards": cards,
            "months": months,
            "input_data": [input_by_month.get(m, 0) for m in months],
            "output_data": [output_by_month.get(m, 0) for m in months],
            "top_vendors": top_vendors,
            "tax_rate_rows": tax_rate_rows,
            "recent_input_n": recent_counts["1"]["n"],
            "recent_input_total": recent_counts["1"]["total"],
            "recent_output_n": recent_counts["2"]["n"],
            "recent_output_total": recent_counts["2"]["total"],
            "recent_rows": recent_rows,
            "last_sync_run": last_sync_run,
            "sync_grid": sync_grid,
            "last_7_days": last_7_days,
            "abnormal_count": abnormal_count,
            "scope_count": len(self_companies),
            "is_admin": user.role == "admin",
            "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
