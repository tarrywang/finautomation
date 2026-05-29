"""SQLAlchemy 2.0 ORM models for the invoice warehouse.

Field naming follows the 发票通 API JSON keys (Pinyin abbreviations) where the
mapping is one-to-one, so anyone reading raw responses can cross-reference.

Source of truth for the JSON shape:
  rawdata/API接入/4.1.api发票实时归集接入.pdf
  rawdata/API接入/4.api发票归集接入.pdf
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════
#  Auth / Multi-user
# ═══════════════════════════════════════════════════════════════════


class User(Base):
    """系统用户。

    role 含义:
      - 'admin'      : 系统管理 + 全部公司全部权限
      - 'supervisor' : 仅授权公司,可改可删 (crud)
      - 'operator'   : 仅授权公司,只查 (view)

    UserCompanyAccess 决定 supervisor/operator 能看哪些公司。
    admin 不需要在那张表里有记录,默认全公司全权限。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))  # admin / supervisor / operator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(INET)
    password_changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    company_scopes: Mapped[list[UserCompanyAccess]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserCompanyAccess.user_id",
    )


class UserCompanyAccess(Base):
    """用户 ↔ 公司 ↔ 权限粒度三元组。

    permission:
      - 'view' : 只能浏览 / 导出
      - 'crud' : 可触发同步、可标注 / 软删

    admin 角色不会出现在这张表里(默认全权限)。
    """

    __tablename__ = "user_company_access"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tax_no: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.tax_no", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(10), default="view")  # view / crud
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    granted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    user: Mapped[User] = relationship(back_populates="company_scopes", foreign_keys=[user_id])
    company: Mapped[Company] = relationship()


class Company(Base):
    """企业主数据 — 我们这边查询的对象(自家公司)或对手方均会被自动注册。"""

    __tablename__ = "companies"

    tax_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    alias: Mapped[str | None] = mapped_column(Text)  # 用户自定义短名,UI 显示用
    is_self: Mapped[bool] = mapped_column(default=False)  # True = 我们要查的目标公司
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class SyncRun(Base):
    """每次调发票通 syncInvoicesRealTime 的执行记录。"""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tax_no: Mapped[str] = mapped_column(String(20), index=True)
    data_type: Mapped[str] = mapped_column(String(1))  # '1' 进项 / '2' 销项
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/success/failed
    invoice_count: Mapped[int | None] = mapped_column(Integer)
    raw_response_size_bytes: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(Text)  # 发票通 returns this for tracing
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    raw_payloads: Mapped[list[RawPayload]] = relationship(
        back_populates="sync_run", cascade="all, delete-orphan"
    )


class RawPayload(Base):
    """原始 base64 解码后的 JSON 归档,字段映射改了之后能重放。"""

    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sync_runs.id", ondelete="CASCADE")
    )
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    decoded_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    sync_run: Mapped[SyncRun] = relationship(back_populates="raw_payloads")


class Invoice(Base):
    """发票头 — 一张唯一发票一行。"""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tax_no", "data_type", "sdfphm", name="uq_invoice_natural_key"),
        Index("ix_invoice_kprq", "kprq"),
        Index("ix_invoice_tax_no_data_type", "tax_no", "data_type"),
        Index("ix_invoice_xfsbh", "xfsbh"),
        Index("ix_invoice_gfsbh", "gfsbh"),
        Index("ix_invoice_fpzt", "fpzt"),
        Index("ix_invoice_fppz", "fppz"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── 关键标识 ──
    tax_no: Mapped[str] = mapped_column(String(20))  # 我们查询时使用的税号
    data_type: Mapped[str] = mapped_column(String(1))  # '1' 进项 / '2' 销项
    sdfphm: Mapped[str | None] = mapped_column(String(40))  # 数电发票号码(20 位)
    fphm: Mapped[str | None] = mapped_column(String(40))  # 旧版发票号码
    fpdm: Mapped[str | None] = mapped_column(String(20))  # 发票代码

    # ── 销/购方 ──
    xfsbh: Mapped[str | None] = mapped_column(String(20))  # 销方税号
    xfmc: Mapped[str | None] = mapped_column(Text)  # 销方名称
    gfsbh: Mapped[str | None] = mapped_column(String(20))  # 购方税号
    gfmc: Mapped[str | None] = mapped_column(Text)  # 购方名称

    # ── 时间 ──
    kprq: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))  # 开票日期

    # ── 票面分类 ──
    fppz: Mapped[str | None] = mapped_column(Text)  # 发票品种
    fpzt: Mapped[str | None] = mapped_column(String(40))  # 发票状态
    fpfxdj: Mapped[str | None] = mapped_column(String(20))  # 发票风险等级
    sfzsfp: Mapped[str | None] = mapped_column(String(10))  # 是否纸质发票

    # ── 金额 ──
    jshj: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # 价税合计
    je: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # 金额(不含税)
    se: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # 税额
    slv: Mapped[str | None] = mapped_column(String(20))  # 税率

    # ── 业务/备注 ──
    tdywlx: Mapped[str | None] = mapped_column(String(40))  # 特定业务类型
    bz: Mapped[str | None] = mapped_column(Text)  # 备注
    kpr: Mapped[str | None] = mapped_column(Text)  # 开票人

    # ── 抵扣(仅进项)──
    deductible: Mapped[str | None] = mapped_column(String(10))  # 勾选状态
    deductible_period: Mapped[str | None] = mapped_column(String(10))  # 勾选属期 YYYYMM

    # ── 出处 + 原始字段 ──
    raw_section: Mapped[str | None] = mapped_column(String(40))  # XXHZB/FPJCXX/JZFW/...
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # ── 追溯 ──
    sync_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sync_runs.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    items: Mapped[list[InvoiceItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.row_no"
    )


class InvoiceItem(Base):
    """发票商品行 — 从 XXHZB 同 SDFPHM 多行重组而来。"""

    __tablename__ = "invoice_items"
    __table_args__ = (UniqueConstraint("invoice_id", "row_no", name="uq_invoice_item_row"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    row_no: Mapped[int] = mapped_column(Integer)  # XH 行号

    commodity_name: Mapped[str | None] = mapped_column(Text)  # HWHYSLWMC
    spec: Mapped[str | None] = mapped_column(Text)  # GGXH
    unit: Mapped[str | None] = mapped_column(String(20))  # DW
    qty: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))  # SL
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(22, 8))  # DJ
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # JE 行金额
    tax_rate: Mapped[str | None] = mapped_column(String(20))  # SLV
    tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # SE 行税额
    tax_classify_code: Mapped[str | None] = mapped_column(String(40))  # SSFLBM

    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="items")
