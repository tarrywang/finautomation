"""FastAPI dependencies (DB session, templates)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..warehouse.session import get_sessionmaker

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


# ─── Jinja filters ─────────────────────────────────────────────────
# 发票通的 fppz 字段中文很长 (e.g. "数电发票（增值税专用发票）") 占列宽。
# 这个 filter 把它压成 6 字以内的短标签,完整原文用 title= 在 hover 上看到。
_FPPZ_SHORT = {
    "数电发票（增值税专用发票）": "数电·专",
    "数电发票（普通发票）": "数电·普",
    "数电发票（铁路电子客票）": "数电·铁路",
    "数电发票（航空运输电子客票行程单）": "数电·航空",
    "数电发票（机动车销售统一发票）": "数电·机动车",
    "数电发票（二手车销售统一发票）": "数电·二手车",
    "全电纸质发票（增值税专用发票）": "全纸·专",
    "全电纸质发票（普通发票）": "全纸·普",
    "全电纸质发票（机动车销售统一发票）": "全纸·机动车",
    "全电纸质发票（二手车销售统一发票）": "全纸·二手车",
    "增值税电子专用发票": "电专",
    "增值税电子普通发票": "电普",
    "增值税专用发票": "专票",
    "增值税普通发票": "普票",
    "增值税普通发票（卷式）": "卷票",
    "机动车销售统一发票": "机动车",
    "二手车销售统一发票": "二手车",
    "道路通行费电子普通发票": "通行费",
    "电子发票（铁路电子客票）": "铁路客票",
    "电子发票（航空运输电子客票行程单）": "航空行程",
}


def fppz_short(v: str | None) -> str:
    if not v:
        return "—"
    return _FPPZ_SHORT.get(v, v)


templates.env.filters["fppz_short"] = fppz_short


def get_db() -> Iterator[Session]:
    SessionLocal = get_sessionmaker()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
