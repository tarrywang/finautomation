"""Invoices list + detail + CSV export."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...warehouse.models import Company, Invoice, User
from ..auth import (
    assert_can_view,
    get_current_user,
    get_user_scope_taxnos,
)
from ..deps import get_db, templates

router = APIRouter(prefix="/invoices")


def _apply_scope(stmt: Select, user: User, db: Session) -> Select:
    """Inject WHERE invoice.tax_no IN (user's accessible companies). Admin passes through."""
    scope = get_user_scope_taxnos(user, db)
    if scope is None:
        return stmt
    if not scope:
        # No companies granted → empty result set
        return stmt.where(Invoice.tax_no == "__none__")
    return stmt.where(Invoice.tax_no.in_(scope))


# ─────────────────────── Filter parsing ───────────────────────


def _parse_csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _multi_values(params: list[str]) -> list[str]:
    """Accept either ?k=A&k=B (repeated) or ?k=A,B (CSV) → ['A', 'B']."""
    out: list[str] = []
    for v in params:
        if v is None:
            continue
        for x in v.split(","):
            x = x.strip()
            if x:
                out.append(x)
    # dedupe while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def _qs_drop(
    pairs: list[tuple[str, str]],
    key: str,
    value: str | None = None,
) -> str:
    """Return URL-encoded query string with one entry removed.

    If value is None, drops all entries with that key.
    If value is given, drops only the (key, value) tuple.
    """
    from urllib.parse import urlencode

    kept = [(k, v) for k, v in pairs if not (k == key and (value is None or v == value))]
    return urlencode(kept)


def _opt_date(v: str | None) -> date | None:
    """Parse YYYY-MM-DD; treat empty / None as None (so HTML form blanks don't 422)."""
    if not v or not v.strip():
        return None
    return date.fromisoformat(v.strip())


def _opt_float(v: str | None) -> float | None:
    """Parse float; treat empty / None as None."""
    if v is None or not str(v).strip():
        return None
    return float(v)


def _opt_int(v: str | None) -> int | None:
    if v is None or not str(v).strip():
        return None
    return int(v)


def _build_filters(
    stmt: Select,
    *,
    tax_nos: list[str],
    data_type: str | None,
    kprq_from: date | None,
    kprq_to: date | None,
    fppz_in: list[str],
    fpzt_in: list[str],
    xfsbh_like: str | None,
    jshj_min: float | None,
    jshj_max: float | None,
    tdywlx_in: list[str],
    deductible_in: list[str],
    since_hours: int | None = None,
) -> Select:
    if tax_nos:
        stmt = stmt.where(Invoice.tax_no.in_(tax_nos))
    if data_type:
        stmt = stmt.where(Invoice.data_type == data_type)
    if kprq_from:
        stmt = stmt.where(Invoice.kprq >= datetime.combine(kprq_from, datetime.min.time()))
    if kprq_to:
        # inclusive of end-of-day
        stmt = stmt.where(Invoice.kprq < datetime.combine(kprq_to, datetime.max.time()))
    if fppz_in:
        stmt = stmt.where(Invoice.fppz.in_(fppz_in))
    if fpzt_in:
        stmt = stmt.where(Invoice.fpzt.in_(fpzt_in))
    if xfsbh_like:
        like = f"%{xfsbh_like}%"
        stmt = stmt.where(or_(Invoice.xfmc.ilike(like), Invoice.xfsbh.ilike(like)))
    if jshj_min is not None:
        stmt = stmt.where(Invoice.jshj >= jshj_min)
    if jshj_max is not None:
        stmt = stmt.where(Invoice.jshj <= jshj_max)
    if tdywlx_in:
        stmt = stmt.where(Invoice.tdywlx.in_(tdywlx_in))
    if deductible_in:
        stmt = stmt.where(Invoice.deductible.in_(deductible_in))
    if since_hours is not None and since_hours > 0:
        cutoff = datetime.now() - timedelta(hours=since_hours)
        stmt = stmt.where(Invoice.first_seen_at >= cutoff)
    return stmt


def _fetch_filter_options(db: Session, user: User) -> dict[str, Any]:
    """Distinct values for filter dropdowns, scoped to user's accessible companies."""
    scope = get_user_scope_taxnos(user, db)

    self_q = select(Company).where(Company.is_self.is_(True))
    if scope is not None:
        if not scope:
            self_q = self_q.where(Company.tax_no == "__none__")
        else:
            self_q = self_q.where(Company.tax_no.in_(scope))
    self_companies = db.execute(self_q.order_by(Company.tax_no)).scalars().all()

    base_distinct = select(Invoice.fppz).where(Invoice.fppz.isnot(None))
    base_distinct = _apply_scope(base_distinct, user, db)
    fppz_vals = [r[0] for r in db.execute(base_distinct.distinct().order_by(Invoice.fppz)).all()]

    fpzt_q = _apply_scope(select(Invoice.fpzt).where(Invoice.fpzt.isnot(None)), user, db)
    fpzt_vals = [r[0] for r in db.execute(fpzt_q.distinct().order_by(Invoice.fpzt)).all()]

    tdywlx_q = _apply_scope(select(Invoice.tdywlx).where(Invoice.tdywlx.isnot(None)), user, db)
    tdywlx_vals = [r[0] for r in db.execute(tdywlx_q.distinct().order_by(Invoice.tdywlx)).all()]

    return {
        "self_companies": self_companies,
        "fppz_options": fppz_vals,
        "fpzt_options": fpzt_vals,
        "tdywlx_options": tdywlx_vals,
    }


# ─────────────────────── List page ───────────────────────


@router.get("", response_class=HTMLResponse)
def invoices_list(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    tax_no: list[str] = Query(default=[]),  # multi-value
    data_type: str | None = None,
    # Accept all numeric/date params as strings so HTML-form blanks ("") don't 422;
    # convert below with _opt_date / _opt_float / _opt_int.
    kprq_from: str | None = None,
    kprq_to: str | None = None,
    # Multi-value filters: accept either ?fppz=A&fppz=B or ?fppz=A,B
    fppz: list[str] = Query(default=[]),
    fpzt: list[str] = Query(default=[]),
    xfsbh: str | None = None,  # like
    jshj_min: str | None = None,
    jshj_max: str | None = None,
    tdywlx: list[str] = Query(default=[]),
    deductible: list[str] = Query(default=[]),
    since_hours: str | None = None,  # shortcut: invoices first ingested within last N hours
    page: int = 1,
    page_size: int = 50,
    sort: str = "kprq_desc",
) -> HTMLResponse:
    fppz_in = _multi_values(fppz)
    fpzt_in = _multi_values(fpzt)
    tdywlx_in = _multi_values(tdywlx)
    deductible_in = _multi_values(deductible)
    # Coerce empty strings → None (form submissions with blank inputs)
    kprq_from_d = _opt_date(kprq_from)
    kprq_to_d = _opt_date(kprq_to)
    jshj_min_f = _opt_float(jshj_min)
    jshj_max_f = _opt_float(jshj_max)
    since_hours_i = _opt_int(since_hours)
    data_type_v = data_type or None  # empty string → None

    filter_kwargs = dict(
        tax_nos=tax_no,
        data_type=data_type_v,
        kprq_from=kprq_from_d,
        kprq_to=kprq_to_d,
        fppz_in=fppz_in,
        fpzt_in=fpzt_in,
        xfsbh_like=xfsbh,
        jshj_min=jshj_min_f,
        jshj_max=jshj_max_f,
        tdywlx_in=tdywlx_in,
        deductible_in=deductible_in,
        since_hours=since_hours_i,
    )
    base = _apply_scope(_build_filters(select(Invoice), **filter_kwargs), user, db)

    # Total count + sum — apply scope + filters to fresh aggregate selects
    total_count = db.execute(
        _apply_scope(_build_filters(select(func.count(Invoice.id)), **filter_kwargs), user, db)
    ).scalar_one()
    sums_stmt = _apply_scope(
        _build_filters(
            select(
                func.coalesce(func.sum(Invoice.jshj), 0),
                func.coalesce(func.sum(Invoice.se), 0),
            ),
            **filter_kwargs,
        ),
        user,
        db,
    )
    sums = db.execute(sums_stmt).first()
    sum_jshj, sum_se = sums or (0, 0)

    # Order
    sort_col = {
        "kprq_desc": Invoice.kprq.desc().nullslast(),
        "kprq_asc": Invoice.kprq.asc().nullsfirst(),
        "jshj_desc": Invoice.jshj.desc().nullslast(),
        "jshj_asc": Invoice.jshj.asc().nullsfirst(),
    }.get(sort, Invoice.kprq.desc().nullslast())

    page = max(1, page)
    page_size = min(max(page_size, 10), 200)
    rows = (
        db.execute(base.order_by(sort_col).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )

    total_pages = max(1, (total_count + page_size - 1) // page_size)
    opts = _fetch_filter_options(db, user)

    # Build query string fragment for pagination + chips (preserves all filters).
    # One pair per value for multi-value fields so chip-removal URLs can target
    # individual values via _qs_drop(pairs, key, value).
    qs_pairs: list[tuple[str, str]] = []
    for t in tax_no:
        qs_pairs.append(("tax_no", t))
    if data_type_v:
        qs_pairs.append(("data_type", data_type_v))
    if kprq_from_d:
        qs_pairs.append(("kprq_from", kprq_from_d.isoformat()))
    if kprq_to_d:
        qs_pairs.append(("kprq_to", kprq_to_d.isoformat()))
    for v in fppz_in:
        qs_pairs.append(("fppz", v))
    for v in fpzt_in:
        qs_pairs.append(("fpzt", v))
    if xfsbh:
        qs_pairs.append(("xfsbh", xfsbh))
    if jshj_min_f is not None:
        qs_pairs.append(("jshj_min", str(jshj_min_f)))
    if jshj_max_f is not None:
        qs_pairs.append(("jshj_max", str(jshj_max_f)))
    for v in tdywlx_in:
        qs_pairs.append(("tdywlx", v))
    for v in deductible_in:
        qs_pairs.append(("deductible", v))
    if since_hours_i is not None:
        qs_pairs.append(("since_hours", str(since_hours_i)))
    if sort:
        qs_pairs.append(("sort", sort))
    qs_pairs.append(("page_size", str(page_size)))

    from urllib.parse import urlencode

    qs_base = urlencode(qs_pairs)

    # ── Active filter chips ──
    # Each chip's remove_url drops just that one filter (or one value of a multi-value)
    tax_name = {c.tax_no: (c.alias or c.name or c.tax_no) for c in opts["self_companies"]}
    dt_label = {"1": "进项", "2": "销项"}
    deductible_label = {"0": "未勾选", "1": "已勾选", "2": "不勾选"}
    chips: list[dict[str, str]] = []

    def _chip(label: str, text: str, drop_key: str, drop_value: str | None = None) -> None:
        chips.append(
            {
                "label": label,
                "text": text,
                "remove_url": f"/invoices?{_qs_drop(qs_pairs, drop_key, drop_value)}",
            }
        )

    for tn in tax_no:
        _chip("我方", tax_name.get(tn, tn), "tax_no", tn)
    if data_type_v:
        _chip("类型", dt_label.get(data_type_v, data_type_v), "data_type")
    if kprq_from_d:
        _chip("开票自", kprq_from_d.isoformat(), "kprq_from")
    if kprq_to_d:
        _chip("开票至", kprq_to_d.isoformat(), "kprq_to")
    for v in fppz_in:
        _chip("品种", v, "fppz", v)
    for v in fpzt_in:
        _chip("状态", v, "fpzt", v)
    if xfsbh:
        _chip("对手方", xfsbh, "xfsbh")
    if jshj_min_f is not None:
        _chip("金额≥", f"¥{jshj_min_f:,.2f}", "jshj_min")
    if jshj_max_f is not None:
        _chip("金额≤", f"¥{jshj_max_f:,.2f}", "jshj_max")
    for v in tdywlx_in:
        _chip("业务", v, "tdywlx", v)
    for v in deductible_in:
        _chip("勾选", deductible_label.get(v, v), "deductible", v)
    if since_hours_i is not None:
        _chip("最近", f"{since_hours_i} 小时入库", "since_hours")

    return templates.TemplateResponse(
        request,
        "invoices_list.html",
        {
            "invoices": rows,
            "total_count": total_count,
            "sum_jshj": sum_jshj,
            "sum_se": sum_se,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": sort,
            # Filter form state. Multi-value fields are list[str] so templates
            # use `{% if v in current.fppz %}` instead of `.split(",")`.
            "current": {
                "tax_no": tax_no,
                "data_type": data_type_v,
                "kprq_from": kprq_from_d.isoformat() if kprq_from_d else "",
                "kprq_to": kprq_to_d.isoformat() if kprq_to_d else "",
                "fppz": fppz_in,
                "fpzt": fpzt_in,
                "xfsbh": xfsbh or "",
                "jshj_min": jshj_min_f if jshj_min_f is not None else "",
                "jshj_max": jshj_max_f if jshj_max_f is not None else "",
                "tdywlx": tdywlx_in,
                "deductible": deductible_in,
            },
            "qs_base": qs_base,
            "chips": chips,
            "since_hours": since_hours_i,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
            **opts,
        },
    )


# ─────────────────────── CSV export ───────────────────────


@router.get("/export.csv")
def invoices_export_csv(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    tax_no: list[str] = Query(default=[]),
    data_type: str | None = None,
    kprq_from: str | None = None,
    kprq_to: str | None = None,
    fppz: list[str] = Query(default=[]),
    fpzt: list[str] = Query(default=[]),
    xfsbh: str | None = None,
    jshj_min: str | None = None,
    jshj_max: str | None = None,
    tdywlx: list[str] = Query(default=[]),
    deductible: list[str] = Query(default=[]),
    since_hours: str | None = None,
) -> StreamingResponse:
    base = _apply_scope(
        _build_filters(
            select(Invoice).order_by(Invoice.kprq.desc().nullslast()),
            tax_nos=tax_no,
            data_type=data_type or None,
            kprq_from=_opt_date(kprq_from),
            kprq_to=_opt_date(kprq_to),
            fppz_in=_multi_values(fppz),
            fpzt_in=_multi_values(fpzt),
            xfsbh_like=xfsbh,
            jshj_min=_opt_float(jshj_min),
            jshj_max=_opt_float(jshj_max),
            tdywlx_in=_multi_values(tdywlx),
            deductible_in=_multi_values(deductible),
            since_hours=_opt_int(since_hours),
        ),
        user,
        db,
    )
    invs = db.execute(base).scalars().all()

    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel reads as UTF-8
    writer = csv.writer(buf)
    writer.writerow(
        [
            "我方税号",
            "进/销项",
            "数电号码",
            "开票日期",
            "销方税号",
            "销方名称",
            "购方税号",
            "购方名称",
            "发票品种",
            "发票状态",
            "价税合计",
            "金额",
            "税额",
            "税率",
            "特定业务类型",
            "勾选状态",
            "备注",
        ]
    )
    dt_label = {"1": "进项", "2": "销项"}
    for inv in invs:
        writer.writerow(
            [
                inv.tax_no,
                dt_label.get(inv.data_type, inv.data_type),
                inv.sdfphm or "",
                inv.kprq.strftime("%Y-%m-%d %H:%M:%S") if inv.kprq else "",
                inv.xfsbh or "",
                inv.xfmc or "",
                inv.gfsbh or "",
                inv.gfmc or "",
                inv.fppz or "",
                inv.fpzt or "",
                inv.jshj or "",
                inv.je or "",
                inv.se or "",
                inv.slv or "",
                inv.tdywlx or "",
                inv.deductible or "",
                inv.bz or "",
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"invoices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────── Detail page ───────────────────────


@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(
    request: Request,
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HTMLResponse:
    inv = db.execute(
        select(Invoice).options(selectinload(Invoice.items)).where(Invoice.id == invoice_id)
    ).scalar_one_or_none()
    if not inv:
        return HTMLResponse("<h1>404 — invoice not found</h1>", status_code=404)
    # Scope check: return 404 (not 403) to avoid enumeration
    assert_can_view(user, db, inv.tax_no)
    return templates.TemplateResponse(
        request,
        "invoice_detail.html",
        {
            "inv": inv,
            "raw_pretty": json.dumps(inv.raw_data, ensure_ascii=False, indent=2),
            "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
