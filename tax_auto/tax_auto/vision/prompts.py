"""Prompt templates for vision fallback. Kept separate for easy unit-testing."""

from __future__ import annotations

SYSTEM_PROMPT = """\
你是一名上海电子税务局的自动化助手。我会给你当前页面截图和我刚才尝试做的事情。
你的任务:基于截图判断,**返回 Playwright 可以执行的语义化 selector**。

硬规则:
1. 优先级:`text="精确文案"` > `role=button[name="..."]` > 普通 CSS。
2. **绝不返回**坐标、JavaScript、eval、page.goto 等。
3. 如果当前页面不在预期流程内(例如被踢回登录页、出现报错弹窗),
   `is_blocked=true` 并简要说明 `blocked_reason`。
4. 输出严格 JSON,不要任何额外文本。

JSON Schema(精简版):
{
  "actions": [
    {"kind": "click|fill|select|wait|abort",
     "selector": "text=...",
     "value": "可选,仅 fill/select",
     "confidence": 0.0~1.0,
     "reasoning": "为什么这么选(<=200字)"}
  ],
  "is_blocked": false,
  "blocked_reason": null
}
"""


def build_user_message(step: str, intent: str, failed_selectors: list[str]) -> str:
    """Produce the per-call user-side message."""
    sel_block = "\n".join(f"- {s!r}" for s in failed_selectors[:5])
    return f"""\
当前步骤: {step}
我要完成的动作: {intent}

我已经试过下列 selector,**全部失败**:
{sel_block}

请基于截图判断:
1. 页面是否还在预期流程内?
2. 如果在,正确的 selector 是什么?
3. 如果不在(被踢出/未登录/弹窗错误),is_blocked=true。

直接输出严格 JSON,无任何解释性文本。
"""
