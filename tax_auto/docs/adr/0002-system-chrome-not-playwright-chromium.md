# ADR-0002 · 用系统 Chrome,不下载 Playwright 自带 chromium

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry
- **Supersedes**: 架构文档原计划的 `playwright install chromium`

## Context

执行 W0.4 时,`playwright install chromium` 失败:
- 默认 CDN(`playwright.azureedge.net`)国内访问不稳定
- npmmirror 镜像路径已变更,返回 404

同时发现 macOS 已安装 Google Chrome.app。Playwright 原生支持 `channel="chrome"` 直接驱动系统已安装的 Chrome / Edge,**不需要下载自家 chromium**。

## Decision

**全项目用系统 Chrome,不下载 Playwright chromium。**

所有 launch 调用统一为:
```python
p.chromium.launch_persistent_context(
    user_data_dir=...,
    channel="chrome",   # ← 关键
    headless=False,
    ...
)
```

## Rationale

| 维度 | Playwright chromium | 系统 Chrome |
|------|--------------------|-----------|
| 安装 | 需下载 ~150MB,国内 CDN 易失败 | 用户日常已装 |
| 反风控 | "Playwright build vXXX" 在 navigator 里 | 完全是真 Chrome,无水印 |
| 升级 | 跟 Playwright 版本绑定 | 跟随系统自动更新 |
| 跨机部署 | 每台机都要 install | 装 Chrome 一次,通用 |
| 政务系统兼容性 | 政务网站偶尔做"非主流浏览器"限制 | Chrome 是合规默认 |

**核心理由**:Design Tenet #2 是"Human-Like Interaction"——目标就是让网站认为是真人在用 Chrome。**Playwright 自带的 chromium 本质是为测试构建,系统 Chrome 才是真用户用的**。

## Consequences

### 正面
- W0.4 任务从"等下载"变成"验证可用",节省 5-10 分钟构建时间
- 部署到新 Mac 时不再依赖能访问 playwright CDN(对悦舍餐饮等客户机房友好)
- 反风控水位提高一档:`navigator.userAgent`、可用插件列表都是真实 Chrome 的

### 负面
- 依赖用户已装 Chrome(99% Mac 都有,可接受)
- 跨 Chrome 大版本时 Playwright 1.49 可能出现 API 不兼容——观察期内每月手动验一次
- CI 环境(未来)要单独装 Chrome,稍微多一步

### 中性
- 与 ADR-0001(不用 agent framework)叠加:**确定性 + 真人化** 这套组合是我们的护城河

## Implementation

1. `tax_auto/core/browser.py` 提供唯一的 `make_context()` 工厂,内置 `channel="chrome"`
2. `cli.py doctor` 命令验证 Chrome 可启动(不再验证 chromium 下载)
3. `pyproject.toml` 不再要求跑 `playwright install`
4. README 加一条 "macOS 装 Google Chrome" 前置要求

## Fallback

如果某天系统 Chrome 出问题,可以临时切回 Playwright chromium:
```bash
PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net uv run playwright install chromium
# 然后改 channel=None
```
但这只是急救手段,不进主流程。

## References

- [Playwright docs: Browsers and channels](https://playwright.dev/python/docs/browsers#chromium-headed-shell)
- `tasks/lessons.md` 2026-05-26 W0 CDN failure entry
- 架构文档 §0 Design Tenet #2(Human-Like Interaction)
