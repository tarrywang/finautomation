# ADR-0001 · 不使用 Agent Framework (LangGraph / Browser-Use / AutoGen)

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry

## Context

我们要做"上海电子税务局发票批量下载"自动化。市面上的方案分两大流派:

1. **Agent framework**(Browser-Use / LangGraph / AutoGen / CrewAI)——LLM 在主循环里决策下一步
2. **确定性脚本**(Playwright / Selenium)——人预先写好步骤,代码按 selector 跑

业务特征:
- 单一垂直场景:**一个网站、固定 7 步流程、所有步骤已知**
- 每月每客户跑 1-2 次,总量小
- 流程在改版时会失效,但**改版周期以月计**,不是每次跑都变

## Decision

**采用纯 Playwright + Claude vision 兜底,不引入任何 agent framework。**

具体:
- 主循环 100% 由 Python + Playwright selector 跑
- Anthropic SDK 仅在 selector miss 时被动调用(< 5% 路径)
- 模型负责"识别当前页面 + 给出语义化 selector",**不参与主循环决策**

## Rationale

| 维度 | Agent framework | Playwright + vision 兜底 |
|------|----------------|------------------------|
| 每步成本 | $0.01–0.05/step LLM 调用 | 0(95% 路径) |
| 单任务耗时 | 5-15 分钟(每步等 LLM) | 1-3 分钟 |
| 可调试性 | LLM 决策路径难复盘 | Playwright trace viewer 一键回放 |
| 改版鲁棒性 | LLM 自动适配,但慢且贵 | 兜底链路救场,大改版仍需人工 |
| 适用场景的契合度 | 开放任务("帮我办税")✅ | 固定流程("拉发票")✅ |

**核心论点**:agent framework 的价值在"动态规划下一步"。我们的下一步**预先就是已知的**,所以这个价值用不上;但它的成本(慢、贵、不可调试)我们要全额承担。这是负 ROI。

## Consequences

### 正面
- 95% 路径零 LLM 成本——月运行成本 ¥10-50 而不是 ¥500+
- 可调试:Playwright trace 是行业标准,事故复现 5 分钟
- 演进可控:未来真有"开放任务"诉求时,可在确定性核心外**叠加** agent layer,核心不重写

### 负面
- 改版后**必须有人介入修 selector**——vision fallback 只能扛"小改",不能扛"重做"
- 失去"零代码维护"幻觉——但这本来就是幻觉

### 中性
- 团队需要熟 Playwright,不需要熟 LangGraph 生态——对全栈开发者反而是减负

## Alternatives Considered

- **Stagehand**(Playwright + AI 元素识别):合理备选,但每个 `page.act()` 都过 LLM。如果电子税务局未来改版频率 > 月度,可切换。当前先用纯 Playwright。
- **Computer Use API**(Claude 直接控制鼠标坐标):坐标比 selector 脆弱,我们让 vision 返回 selector 而不是坐标(见 ADR-0003)。
- **八爪鱼/影刀 RPA**:低代码受限于 Windows + 不可编程,与我们的 macOS + Python 栈不兼容。

## References

- 《自建发票自动化方案.html》§1 框架决策表
- docs/architecture.md §0 Design Tenets #1(Determinism First)
