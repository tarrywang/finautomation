# ADR-0003 · Vision fallback 返回语义化 selector,不返回坐标

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry
- **Relates to**: ADR-0001(不用 agent framework)

## Context

当 Playwright selector 全部 miss 时,vision fallback 让 Claude 看截图并给出
"应该点哪/填什么"。模型可以返回两种东西:

A. **绝对坐标** `(x=480, y=312)`,由 Playwright `page.mouse.click(x, y)` 执行
B. **语义化 selector** `text="批量下载"` / `role=button[name="确认"]`,由 Playwright locator 执行

Anthropic Computer Use API 走的是路线 A;我们决定走 B。

## Decision

**Vision fallback 只接受语义化 selector,不接受坐标。**

`tax_auto/vision/schemas.py` 的 `Action` 模型只有 `selector` 字段,不接受 x/y。
模型输出经 `_SELECTOR_DENY` 过滤后由 `page.locator(sel)` 执行,失败仍走
Playwright 自身的 wait + retry 机制。

## Rationale

| 维度 | 坐标 | 语义化 selector |
|---|---|---|
| 浏览器尺寸变化 | 失效 | 不受影响 |
| 元素移动 (滚动/重排) | 失效 | 不受影响 |
| DPR / 缩放 | 模型容易算错 | 不存在该问题 |
| 可调试性 | 看 trace 看不出"为啥点这里" | selector 本身可读 |
| 失败后重试 | 必须重新看图 | Playwright 自动等元素出现 |
| 安全风险 | 几乎无(就点一下) | 需 deny list 防 `javascript:` 注入 |

**核心论点**:坐标是"硬兜底"——错了就错了,没有挽回;语义化 selector 是
"软兜底"——即便模型给的不对,`page.locator()` 仍会按 Playwright 的等待
逻辑去匹配,失败抛 TimeoutError,可继续升级到 Tier 2 (Opus) 或 Human。

## Consequences

### 正面
- 失败时三级降级链路全程可观测(每级都留 `llm_calls.jsonl`)
- selector 字符串本身就是事故复盘的"可读证据"
- 安全栏杆简单:一个 deny list 卡掉 `javascript:` / `eval` 等就够

### 负面
- 当真没有任何 selector 能锚定时(纯 canvas 渲染),会直接进 HumanRequired
- prompt 需要明确教模型"返回 selector 而非坐标",一次性 prompt 设计成本

### 缓解
- prompt 里加示例(`text=...`, `role=...`),让模型几乎不可能输出坐标
- `_SELECTOR_DENY` 包含 `evaluate`, `eval(`, `page.goto`, `<script` 这些
  也覆盖了"模型试图越权"的尾部风险

## Implementation

- [tax_auto/vision/schemas.py](../../tax_auto/vision/schemas.py)
  `Action.selector: str` + `_SELECTOR_DENY` + `field_validator`
- [tax_auto/vision/prompts.py](../../tax_auto/vision/prompts.py) `SYSTEM_PROMPT`
  明确告诉模型"绝不返回坐标、JavaScript、eval"
- [tax_auto/flow/waits.py](../../tax_auto/flow/waits.py) `_resolve_via_vision()`
  把模型给的 selector 喂回 `page.locator(...).wait_for(state="visible", timeout=3000)`,
  失败就降级,不爆炸

## References

- [Anthropic Computer Use docs](https://docs.anthropic.com/claude/docs/computer-use) — 对比基础
- 架构文档 §7 Vision Fallback Design
