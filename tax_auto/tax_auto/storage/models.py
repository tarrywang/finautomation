"""SQLModel ORM definitions. See docs/architecture.md §4.1."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Customer(SQLModel, table=True):
    """被代账客户企业."""

    __tablename__ = "customers"

    tax_id: str = Field(primary_key=True, max_length=32)  # 统一社会信用代码
    alias: str = Field(max_length=64)  # 显示名
    contact: str | None = Field(default=None, max_length=128)
    notification_level: str = Field(default="INFO", max_length=16)  # INFO|WARN|CRITICAL
    use_china_llm: bool = False  # ADR-0001 followup
    created_at: datetime = Field(default_factory=_utcnow)
    active: bool = True


class Session(SQLModel, table=True):
    """每客户一行,反映当前最新 session 状态."""

    __tablename__ = "sessions"

    tax_id: str = Field(primary_key=True, foreign_key="customers.tax_id", max_length=32)
    user_data_dir: str  # absolute path
    last_login_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_hint_at: datetime | None = None
    status: str = Field(default="FRESH", max_length=16)  # SessionState


class Task(SQLModel, table=True):
    """任务定义(可周期可一次性)."""

    __tablename__ = "tasks"

    id: int | None = Field(default=None, primary_key=True)
    tax_id: str = Field(foreign_key="customers.tax_id", max_length=32, index=True)
    kind: str = Field(max_length=32)  # FETCH_INVOICES | LOGIN_REFRESH | ...
    params_json: str  # JSON-encoded dict
    schedule_cron: str | None = None  # None = one-shot
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class Run(SQLModel, table=True):
    """每次执行一行."""

    __tablename__ = "runs"

    id: str = Field(primary_key=True, max_length=32)  # ULID
    task_id: int | None = Field(default=None, foreign_key="tasks.id")
    tax_id: str = Field(max_length=32, index=True)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    state: str = Field(default="PENDING", max_length=24, index=True)
    last_step: str | None = Field(default=None, max_length=64)
    error_class: str | None = Field(default=None, max_length=64)
    error_message: str | None = None
    trace_path: str | None = None
    llm_calls: int = 0
    llm_cost_cents: int = 0


class Invoice(SQLModel, table=True):
    """拉到的发票."""

    __tablename__ = "invoices"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", max_length=32)
    tax_id: str = Field(max_length=32, index=True)
    invoice_code: str | None = Field(default=None, max_length=32)
    invoice_no: str = Field(max_length=32)
    invoice_date: str = Field(max_length=16)  # YYYY-MM-DD
    amount_cents: int  # 含税,分为单位 — never float
    seller_tax_id: str | None = Field(default=None, max_length=32)
    seller_name: str | None = Field(default=None, max_length=128)
    file_path: str
    file_format: str = Field(max_length=8)  # OFD | PDF | XML

    __table_args__ = ({"sqlite_autoincrement": True},)


class AuditLog(SQLModel, table=True):
    """关键操作审计(合规留证)."""

    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow, index=True)
    run_id: str | None = Field(default=None, max_length=32)
    actor: str = Field(max_length=64)  # system | human:tarry
    action: str = Field(max_length=32)  # LOGIN | EXPORT | FACE_VERIFY | DELETE
    detail_json: str | None = None
