# Lessons · 踩坑日志

> 每次出错、被纠正、或发现"原来如此"的事都写一条。  
> 格式:`YYYY-MM-DD · [W#] · 一句话总结` + 详细描述 + 后续防御措施。

---

## 2026-05-26 · [W0] · `playwright install chromium` 国内 CDN 下载失败

**现象**:`uv run playwright install chromium` 报 `Download failure code=1`,默认 azureedge CDN
和 `npmmirror.com/mirrors/playwright-cdn` 镜像都拿不到 `chromium-mac-arm64.zip`(后者 404,
路径已变更)。

**根因**:

1. Playwright 官方 CDN(playwright.azureedge.net)国内访问不稳定
2. npmmirror 的 playwright 镜像路径在历次迁移后已不可用
3. 真正可用的镜像需要去看 `registry.npmmirror.com/-/binary/playwright/` 这种新路径

**修复**:**不再下载 Playwright chromium,改用系统 Chrome**(`channel="chrome"`)。
macOS/Windows 都自带或常装 Chrome,反风控也更可信(navigator 完全是真 Chrome)。
详见 [ADR-0002](../docs/adr/0002-system-chrome-not-playwright-chromium.md)。

**防御**:

- `core/browser.py` 唯一入口 `make_persistent_context()`,内置 `channel="chrome"`
- `tax-auto doctor` 主动 launch Chrome 验证,出问题立刻报错
- README + RUNBOOK 加"前置:装 Google Chrome"一条
- 写了 ADR-0002 固化决策,未来不再走 chromium 下载路径

---

## 2026-05-26 · [W2-W6] · SQLAlchemy DetachedInstanceError 在多处出现

**现象**:`compute_daily()`、`get_customer()` 返回 ORM 对象给调用方,调用方
读 `.alias` / `.started_at` 时抛 `DetachedInstanceError`。

**根因**:`session_scope()` context manager 退出时,session 关闭,所有 ORM
对象的 lazy-load 都失效。调用方拿到的是"标本",不是活的连接。

**修复**:两种策略二选一,**不要混用**:

1. **物化为 plain tuple/dataclass**:在 session 内立即解构成 `(a, b, c)`,
   把不需要 ORM 行为的字段抽出来——`metrics.py` 走这条
2. **expunge**:`sess.expunge(row)` 显式从 session 解绑——`customers.py` 走这条
   (因为调用方期望拿到 Customer 对象做 dict-like 访问)

**防御**:

- 单测 conftest 用 `tmp_path` 给每个测试独立 DB,这种 detached 问题立刻显形
- 写了 `test_metrics.py` + `test_storage.py` 覆盖 happy path
- 以后所有"DAO 返回 ORM 对象"的函数都必须二选一其中策略

---

## 2026-05-26 · [W4] · Pydantic v2 跨字段验证用 `field_validator` 不可靠

**现象**:`Decision` 类要求 `is_blocked=True` 时 `blocked_reason` 必填,
用 `@field_validator("blocked_reason")` 看 `info.data` 实现,**该验证器
在 `blocked_reason` 字段被省略时根本不触发**(pydantic v2 行为)。

**根因**:`field_validator` 只在字段被赋值时执行;`is_blocked=True` +
`blocked_reason` 省略的组合下,验证器跳过,bug 漏过单测。

**修复**:改用 `@model_validator(mode="after")` 在整个模型构造完后做检查。

**防御**:`test_decision_blocked_requires_reason` 覆盖了"不传 blocked_reason"
的退化用例;以后任何"X 字段存在 → Y 字段必填"的跨字段约束都用 model_validator。

---

## Template

```markdown
## YYYY-MM-DD · [W#] · 简短标题

**现象**:发生了什么

**根因**:为什么会这样

**修复**:怎么解决的

**防御**:加了什么测试 / 规则 / ADR,防止再犯
```
