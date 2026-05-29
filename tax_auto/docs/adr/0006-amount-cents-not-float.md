# ADR-0006 · 金额用 `INTEGER cents`,不用 `FLOAT yuan`

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry

## Context

发票金额需要持久化。两种典型表示:

A. `amount: FLOAT` (单位:元) — 直观,但 `0.1 + 0.2 != 0.3`
B. `amount_cents: INTEGER` (单位:分) — 不直观,但精确

涉税场景的特征:
- 法定单位是元,显示和印刷都用元
- 但**审计**层面,任何金额差 1 分都要解释
- 加减乘除频繁(求和、税率换算、抵扣)

## Decision

**全链路用 `INTEGER amount_cents`(单位:分)。展示前才转元(`÷100`)。**

数据库 schema、Python 模型、JSON API(将来若有)统一这一约定。

## Rationale

| 维度 | FLOAT yuan | INTEGER cents |
|---|---|---|
| 精度 | 二进制浮点,小数有误差 | 精确 |
| 求和 | Σ 1000 张 ¥0.01 ≠ ¥10 | 完全准确 |
| 类型注解 | `float` | `int`,可被静态检查器识别 |
| 数据库存储 | REAL,跨 DB 表现不一致 | INTEGER 通用 |
| 性能 | 浮点比整数运算慢 | 整数最快 |
| 显示 | 直接显示 | `f"¥{cents/100:.2f}"` 一行 |
| 调试 | 看到 `49.999999...` 一脸懵 | 看到 `5000` 立刻知道是 ¥50 |

**核心论点**:涉税审计的容错是 1 分钱。浮点不能保证这个。
"我们金额都不大,1 分钱无所谓"是错的:1000 张发票求和误差是百分之几,
解释不清。

## Consequences

### 正面
- `sum(invoices.amount_cents)` 永远准确
- 跨 SQLite/Postgres 类型一致(都是 INTEGER)
- 对账时 `INTEGER == INTEGER` 比较干脆

### 负面
- 解析时要乘 100 转 int(`parse_invoice.py` 的 `_yuan_to_cents()` 处理)
- 显示时要除 100 转元(`f"¥{cents/100:.2f}"`)
- 阅读 DB 时 `12345` 不如 `123.45` 直观——加一句 `select amount_cents/100.0 as yuan` 即可

### 中性
- 这不是新发明:Stripe / PayPal / 多数支付系统都这么做

## Implementation

- [tax_auto/storage/models.py](../../tax_auto/storage/models.py)
  `Invoice.amount_cents: int`,字段注释明确"分为单位避免浮点"
- [tax_auto/flow/parse_invoice.py](../../tax_auto/flow/parse_invoice.py)
  `_yuan_to_cents("123.45") == 12345`,处理 OFD/PDF/XML 解析时统一转换
- 测试:[tests/unit/test_storage.py](../../tests/unit/test_storage.py)
  `test_invoice_amount_is_integer_cents` 验证读回来是 int
- 测试:[tests/unit/test_parse_invoice.py](../../tests/unit/test_parse_invoice.py)
  `test_yuan_to_cents_normal` 覆盖正常 + 边界

## References

- [Decimals and Currency](https://0.30000000000000004.com/) — 浮点入门
- [Stripe API: Amount in cents](https://stripe.com/docs/currencies) — 行业惯例
- 架构文档 §4.1
