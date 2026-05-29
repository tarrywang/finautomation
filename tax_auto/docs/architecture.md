# 自建电子税务局发票自动化 · 架构设计

> Version: v1.0  
> Date: 2026-05-26  
> Owner: TarryAI  
> Status: Design (pre-implementation)

本文档把 `自建发票自动化方案.html` 里的 build plan 落到**可施工的工程架构**。读者拿到本文档应当能直接开始写代码,而不需要再做架构决策。

---

## 0. 设计原则(Design Tenets)

按优先级排列。任何决策冲突时,上层原则胜出。

1. **确定性优先(Determinism First)**——能用 selector 解决的,绝不调 LLM。LLM 只在确定性方案失败后的兜底链路里被动触发。
2. **拟人化操作(Human-Like Interaction)**——headed Chromium、persistent context、人工登录,所有可能触发风控的"自动化痕迹"都规避。
3. **失败可降级(Graceful Degradation)**——四级降级:`selector → vision fallback → 升级模型 → 人工接管`。任何一级失败都明确移交给下一级,不"乐观推进"。
4. **可观测性(Observability)**——每一步留下结构化日志 + 截图 + DOM 快照。事故复现成本必须 ≤ 5 分钟。
5. **多租户隔离(Tenant Isolation)**——每个税号一个独立 session 目录、独立 Chromium 实例、独立日志。一个客户的故障绝不影响另一个。
6. **简朴优于精巧(Boring Over Clever)**——不用 Celery、不用 Airflow、不用 K8s。launchd + SQLite + 本地文件系统。直到这套撑不住前都不引入分布式组件。
7. **演进式架构(Evolvable Core)**——核心 600 行 Playwright 逻辑未来给客户做产品时**不重写**,只在外围叠加 Web UI / 多租户管理层。

---

## 1. 系统上下文(System Context · C4 Level 1)

```
        ┌──────────────────────────────────────────────────────────────┐
        │                 上海电子税务局(External System)               │
        │   tpass.shanghai.chinatax.gov.cn → etax.shanghai.chinatax... │
        └──────────────────────▲───────────────────────────────────────┘
                               │ HTTPS · headed Chromium
                               │
        ┌──────────────────────┴───────────────────────────────────────┐
        │              tax_auto System (Mac mini · launchd)             │
        │                                                                │
        │   ① Login Bootstrap    ② Scheduler    ③ Worker (per tenant)  │
        │   ④ Vision Fallback    ⑤ Archiver     ⑥ Notifier             │
        └──┬────────────────────────────────────────────────────────┬──┘
           │                                                         │
           ▼                                                         ▼
    ┌──────────────┐                                        ┌────────────────┐
    │ Anthropic    │                                        │  飞书 Webhook   │
    │ API (Sonnet  │                                        │  + 人工(Tarry) │
    │ 4.6 / Opus)  │                                        └────────────────┘
    └──────────────┘
```

**Actors**
- **税务员/会计(主要用户)**——发起任务、完成扫脸/短信、查看汇总报告
- **被代账客户企业**——授权我们用其电子税务局账号办税(合规边界外,本文不展开)
- **运维(Tarry)**——监控告警、处理改版

**External Dependencies**
- 上海电子税务局(SPA · Element UI · 政务云)——黑盒,可能随时改版
- Anthropic API(Claude Sonnet 4.6 + Opus 4.7 兜底)
- 飞书机器人 Webhook(通知通道)
- (可选)GLM-5.1 / DeepSeek 国内备用模型——合规客户使用

---

## 2. 容器视图(Container View · C4 Level 2)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Host: Mac mini (mac-golden-lerdorf)                │
│                                                                          │
│  ┌────────────────┐    ┌──────────────────┐    ┌──────────────────┐   │
│  │  launchd       │───>│  scheduler.py    │───>│  worker.py       │   │
│  │  (cron daemon) │    │  (orchestrator)  │    │  (per-tenant)    │   │
│  └────────────────┘    └─────────┬────────┘    └────┬─────────────┘   │
│                                  │                   │                   │
│                                  ▼                   ▼                   │
│                        ┌──────────────────┐    ┌──────────────────┐   │
│                        │  metadata.db     │    │  Playwright       │   │
│                        │  (SQLite)        │    │  Chromium (headed)│   │
│                        └──────────────────┘    └────┬──────────────┘   │
│                                                     │                   │
│                                  ┌──────────────────┴──┐                │
│                                  ▼                     ▼                │
│                        ┌──────────────────┐  ┌──────────────────┐     │
│                        │  vision_fallback │  │  Filesystem       │     │
│                        │  (Anthropic SDK) │  │  session/ output/ │     │
│                        └──────────────────┘  │  traces/ logs/    │     │
│                                              └──────────────────┘     │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                       notifier.py (Lark Webhook)                │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

**容器职责**
| 容器 | 职责 | 进程模型 |
|---|---|---|
| `launchd` 守护 | 定时唤起、崩溃重启 | 系统级 daemon |
| `scheduler.py` | 读任务表、派发到 worker、并发控制 | 短生命周期(任务派发完即退出) |
| `worker.py` | 单个税号的完整 7 步流程 | 长生命周期(15-30 分钟/任务) |
| `vision_fallback.py` | selector 失效时的视觉兜底 | 库,被 worker 同进程调用 |
| `notifier.py` | 飞书 webhook + macOS 通知 | 库 |
| `metadata.db` | 客户/任务/发票/会话元数据 | SQLite(WAL 模式) |
| 文件系统 | session cookies、ZIP/OFD、trace、日志 | 本地 ext4-ish |

---

## 3. 项目目录结构

```
tax_auto/
├── pyproject.toml                  # Poetry / uv 配置
├── README.md
├── RUNBOOK.md                      # 停机/改版/新客户加入的运维手册
├── .env.example                    # ANTHROPIC_API_KEY, LARK_WEBHOOK_URL 等
├── .gitignore                      # session/, output/, *.db, .env
│
├── docs/
│   ├── architecture.md             # 本文档
│   ├── flow.md                     # W1 产出:7 步 DOM 探索结果
│   ├── selectors.md                # 每步的 selector 候选清单
│   └── adr/                        # Architecture Decision Records
│       ├── 0001-no-agent-framework.md
│       ├── 0002-headed-not-headless.md
│       └── 0003-semantic-selector-over-coords.md
│
├── tasks/
│   ├── todo.md                     # 6 周任务清单
│   └── lessons.md                  # 踩坑日志(SuperClaude 自我改进闭环)
│
├── tax_auto/                       # Python package
│   ├── __init__.py
│   ├── cli.py                      # Typer 入口:login / fetch / list / run
│   ├── config.py                   # pydantic-settings 配置加载
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── browser.py              # Playwright 上下文工厂
│   │   ├── session.py              # session 生命周期 + 持久化
│   │   ├── state_machine.py        # Run 状态机定义
│   │   └── errors.py               # 异常分类(见 §8)
│   │
│   ├── flow/
│   │   ├── __init__.py
│   │   ├── steps.py                # 7 个 step 函数(纯逻辑)
│   │   ├── selectors.py            # selector 字典(单一事实源)
│   │   ├── waits.py                # SPA 等待策略封装
│   │   └── downloads.py            # ZIP 监听与解压
│   │
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── fallback.py             # resolve_with_claude() 主入口
│   │   ├── prompts.py              # 视觉 prompt 模板
│   │   ├── schemas.py              # Action / Decision pydantic 模型
│   │   └── escalation.py           # Sonnet → Opus 升级链路
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py                   # SQLite 连接管理
│   │   ├── models.py               # SQLModel ORM 定义
│   │   ├── migrations/             # alembic 迁移
│   │   └── fs_layout.py            # 文件系统目录约定
│   │
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── scheduler.py            # 任务派发
│   │   └── launchd.plist.template  # macOS launchd 模板
│   │
│   ├── notify/
│   │   ├── __init__.py
│   │   ├── lark.py                 # 飞书 webhook 客户端
│   │   ├── macos.py                # `osascript` 系统通知
│   │   └── templates.py            # 消息模板
│   │
│   └── obs/                        # observability
│       ├── __init__.py
│       ├── logging.py              # loguru 配置
│       ├── trace.py                # Playwright trace 归档
│       └── metrics.py              # 运行指标聚合
│
├── scripts/
│   ├── login.py                    # 一次性人工登录入口
│   ├── recon.py                    # W1 DOM 探索辅助
│   ├── replay.py                   # 用 trace 回放某次失败
│   └── install_launchd.sh          # 部署到 macOS
│
├── tests/
│   ├── unit/
│   │   ├── test_state_machine.py
│   │   ├── test_selectors.py
│   │   └── test_vision_schemas.py
│   ├── integration/
│   │   ├── test_login_flow.py      # 真实 session,人工触发
│   │   └── test_e2e_smoke.py
│   └── fixtures/
│       └── pages/                  # 离线 HTML 快照,用于 selector 单测
│
└── runtime/                        # gitignored,运行时产物
    ├── session/{tax_id}/           # Chromium user-data
    ├── output/invoices/{tax_id}/{YYYY-MM}/   # OFD/PDF 落地
    ├── traces/{run_id}/            # Playwright .zip trace
    ├── screenshots/{run_id}/       # 失败步骤截图
    ├── logs/                       # loguru 滚动日志
    └── metadata.db                 # SQLite
```

**关键约定**
- 所有运行时产物全在 `runtime/`,一行 `.gitignore` 处理:`runtime/`
- 配置三层优先级:`.env` < `config.yaml` < CLI flag
- 业务包名 `tax_auto`,可作为库被外部进程 import(为未来 Web UI 留口)

---

## 4. 数据模型

### 4.1 SQLite Schema(`metadata.db`)

```sql
-- 客户(被代账企业)
CREATE TABLE customers (
    tax_id          TEXT PRIMARY KEY,           -- 统一社会信用代码 / 税号
    alias           TEXT NOT NULL,              -- 显示名(如"悦舍餐饮")
    contact         TEXT,                       -- 联系人(出问题找谁)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    active          INTEGER NOT NULL DEFAULT 1
);

-- 浏览器 session 状态(每客户一行,反映当前最新状态)
CREATE TABLE sessions (
    tax_id          TEXT PRIMARY KEY REFERENCES customers(tax_id),
    user_data_dir   TEXT NOT NULL,              -- 绝对路径
    last_login_at   TEXT,                       -- ISO8601
    last_used_at    TEXT,
    expires_hint_at TEXT,                       -- 估算过期时间
    status          TEXT NOT NULL               -- FRESH | VALID | EXPIRED | LOCKED
                    CHECK(status IN ('FRESH','VALID','EXPIRED','LOCKED'))
);

-- 任务定义(可周期可一次性)
CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_id          TEXT NOT NULL REFERENCES customers(tax_id),
    kind            TEXT NOT NULL,              -- FETCH_INVOICES | LOGIN_REFRESH | ...
    params_json     TEXT NOT NULL,              -- {"date_from": "2026-04-01", ...}
    schedule_cron   TEXT,                       -- NULL = 一次性
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 单次执行(每次跑都有一行)
CREATE TABLE runs (
    id              TEXT PRIMARY KEY,           -- ULID
    task_id         INTEGER NOT NULL REFERENCES tasks(id),
    tax_id          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    state           TEXT NOT NULL,              -- 见 §5 状态机
    last_step       TEXT,                       -- step_1 .. step_7
    error_class     TEXT,                       -- TransientError / SessionExpired / ...
    error_message   TEXT,
    trace_path      TEXT,                       -- runtime/traces/{run_id}/
    llm_calls       INTEGER NOT NULL DEFAULT 0,
    llm_cost_cents  INTEGER NOT NULL DEFAULT 0
);

-- 拉到的发票
CREATE TABLE invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    tax_id          TEXT NOT NULL,
    invoice_code    TEXT,                       -- 发票代码(老票) / NULL(全电票)
    invoice_no      TEXT NOT NULL,              -- 发票号码
    invoice_date    TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,           -- 金额含税,分为单位避免浮点
    seller_tax_id   TEXT,
    seller_name     TEXT,
    file_path       TEXT NOT NULL,              -- 相对 runtime/output/
    file_format     TEXT NOT NULL,              -- OFD | PDF | XML
    UNIQUE(tax_id, invoice_no, invoice_date)
);

-- 关键操作审计(用于事后追溯,尤其涉税合规)
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    run_id          TEXT,
    actor           TEXT NOT NULL,              -- system | human:tarry
    action          TEXT NOT NULL,              -- LOGIN | EXPORT | FACE_VERIFY | DELETE
    detail_json     TEXT
);

CREATE INDEX idx_runs_state ON runs(state, started_at);
CREATE INDEX idx_invoices_tax_date ON invoices(tax_id, invoice_date);
```

**关键设计选择**
- **金额用 `INTEGER cents`**——避免浮点 0.01 误差,涉税场景零容忍
- **`runs.id` 用 ULID**——单调递增 + 全局唯一,日志/目录命名直接用
- **`invoices` 加 UNIQUE(tax_id, invoice_no, invoice_date)**——重复拉取自动去重
- **审计表独立**——即使主表被改,审计记录不动(为合规留证据)

### 4.2 文件系统布局

```
runtime/
├── session/
│   └── 91310000XXXXXXXXXX/            # 一个税号一个目录
│       ├── Default/                   # Chromium user-data
│       ├── Cookies                    # chmod 600
│       └── .session_meta.json         # last_login_at 等
│
├── output/
│   └── invoices/
│       └── 91310000XXXXXXXXXX/
│           └── 2026-04/
│               ├── 24310000000001234567.ofd
│               ├── 24310000000001234568.ofd
│               └── _manifest.json     # 本月全部发票元数据
│
├── traces/
│   └── 01HXXXXX/                      # = run_id
│       ├── trace.zip                  # Playwright trace
│       ├── step_3.png                 # 失败步骤截图
│       ├── step_3.html                # DOM 快照
│       └── llm_calls.jsonl            # 该 run 所有 LLM 调用记录
│
├── screenshots/                       # 同 traces,失败时归档
├── logs/
│   ├── tax_auto.log                   # 当前
│   └── tax_auto.log.2026-05-25.gz
│
└── metadata.db                        # SQLite WAL
```

**权限模型**
- `runtime/session/` → `chmod 700`(只有当前用户可读)
- `.env` → `chmod 600`,且**通过 macOS Keychain 引用而非明文**(见 §10 安全)
- `runtime/traces/{run_id}/llm_calls.jsonl` 不含明文 cookie,可分享给团队调试

---

## 5. 核心状态机

### 5.1 Run 状态机

```
   ┌─────────┐
   │ PENDING │
   └────┬────┘
        │ scheduler picks up
        ▼
┌──────────────────┐
│ AUTHENTICATING   │── session 失败 ──┐
└────────┬─────────┘                  │
         │ session OK                  │
         ▼                             │
┌──────────────────┐                   │
│ NAVIGATING       │── selector miss ──┼──> ┌──────────────────────┐
└────────┬─────────┘                   │    │ VISION_FALLBACK      │
         │ on target page              │    │ (Sonnet → Opus → ❌) │
         ▼                             │    └──────┬──────────┬────┘
┌──────────────────┐                   │           │ 解析成功 │
│ QUERYING         │                   │           ▼          │ 失败
└────────┬─────────┘                   │      返回主循环       │
         │                             │                       ▼
         ▼                             │              ┌──────────────────┐
┌──────────────────┐                   │              │ NEEDS_HUMAN      │
│ DOWNLOADING      │── 二次扫脸 ────────┼─────────────>│ (飞书通知 + 等)  │
└────────┬─────────┘                   │              └────┬─────────┬───┘
         │ ZIP 任务提交                 │                   │ 人完成   │ 超时
         ▼                             │                   ▼          │
┌──────────────────┐                   │           回到上一步         │
│ POLLING          │── 超时 ───────────┘                              │
└────────┬─────────┘                                                  │
         │ ZIP 就绪                                                    │
         ▼                                                              │
┌──────────────────┐                                                   │
│ ARCHIVING        │                                                   │
└────────┬─────────┘                                                   │
         │                                                              │
         ▼                                                              ▼
   ┌──────────┐                                                  ┌─────────┐
   │   DONE   │                                                  │ FAILED  │
   └──────────┘                                                  └─────────┘
```

**State 定义(`tax_auto/core/state_machine.py`)**
```python
class RunState(str, Enum):
    PENDING = "PENDING"
    AUTHENTICATING = "AUTHENTICATING"
    NAVIGATING = "NAVIGATING"
    QUERYING = "QUERYING"
    DOWNLOADING = "DOWNLOADING"
    POLLING = "POLLING"
    ARCHIVING = "ARCHIVING"
    VISION_FALLBACK = "VISION_FALLBACK"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    DONE = "DONE"
    FAILED = "FAILED"
```

**迁移规则**
- 每次状态变更**必须**写 `runs.state` + `audit_log`,不允许"内存中迁移完才回写"
- `NEEDS_HUMAN` 是阻塞态:worker 等 180 秒,人没处理就升级到 `FAILED`
- `VISION_FALLBACK` 是子状态:成功后**必须**回到原状态(而不是跳到下一步),保证幂等

### 5.2 Session 状态机

```
              ┌──────┐
   人工登录 ─>│FRESH │── 任务首次使用 ──> VALID
              └──────┘
                  ▲
                  │ 重新登录
                  │
              ┌──────┐                      ┌────────┐
              │VALID │── 401 / redirect ──> │EXPIRED │
              └──┬───┘                      └────────┘
                 │
                 │ 并发任务占用
                 ▼
              ┌──────┐
              │LOCKED│── 释放 ──> VALID
              └──────┘
```

**关键决策**
- **同税号同时只允许一个 Chromium 进程**——通过 `sessions.status = LOCKED` + 文件锁双保险
- `EXPIRED` 状态不会自动恢复——必须人工跑 `python -m tax_auto login --tax-id <id>` 才能转回 `FRESH`
- session 失效检测:看 `page.url` 是否 startswith `https://tpass.`(被重定向回登录页)

---

## 6. 7 步主流程详细分解

每步定义为**纯函数 + 校验**,签名统一为 `step(page: Page, ctx: RunContext) -> StepResult`。

| # | Step | 输入 | 关键动作 | 校验(`expect()`) | 失败处理 |
|---|------|------|---------|------------------|---------|
| 1 | `login_check` | session 目录 | 打开首页,检查是否被重定向 | URL 含 `etax.shanghai...` | → `SessionExpired` |
| 2 | `navigate_to_query` | - | 我要办税 → 税务数字账户 → 全量发票查询 | 看到日期筛选输入框 | → `VisionFallback` |
| 3 | `fill_query_form` | `date_from`, `date_to` | 填日期 → 点查询 | 结果列表出现(或"无数据") | → `VisionFallback` |
| 4 | `select_and_export` | - | 全选 checkbox → "批量下载" → 选 OFD → 确认 | 弹出"任务已提交" toast | → `VisionFallback`,失败 3 次升 `NEEDS_HUMAN` |
| 5 | `handle_face_verify` | - | 检测扫脸弹窗;如有,通知人工 | 弹窗在 180s 内消失 | 超时 → `FAILED` |
| 6 | `poll_export_zip` | 任务号 | 轮询"导入导出"页,等 ZIP 就绪 | ZIP 出现下载链接 | 600s 超时 → `FAILED` |
| 7 | `download_and_archive` | ZIP URL | 监听 `download` 事件 → 落盘 → 解压 → 入库 | 文件数与列表数一致 | 不一致 → `FAILED`(数据完整性优先) |

**StepResult 协议**
```python
@dataclass
class StepResult:
    ok: bool
    next_state: RunState                # 期望迁移到哪
    artifacts: dict[str, Any] = {}      # 如 step_4 产出 export_task_id
    error: BaseException | None = None
```

**幂等性**——每一步都设计成可重入:
- `step_1` 命中已登录态直接返回 ok
- `step_3` 重复填表无副作用
- `step_7` 用文件名 + invoice_no UNIQUE 约束去重

---

## 7. Vision Fallback 设计

### 7.1 触发契约

只有以下情况调用 LLM,**不允许**主循环主动调:
- `selector` lookup 抛 `TimeoutError`
- `expect()` 状态校验失败
- 出现**未在白名单**里的弹窗/modal

### 7.2 调用链路(降级序列)

```
selector 失败
    ↓
┌───────────────────────────────────────────────────┐
│ Tier 1: Sonnet 4.6 + 全屏截图 + 该步骤语义意图     │
│ Cost: ~$0.01/call · Latency: ~3s                  │
└──────────────┬────────────────────────────────────┘
               │ 返回可执行 Action
               │ ✓ 命中
               ▼
           执行 → 校验 → 回主循环

  ✗ 解析失败 / Action 执行后校验仍 fail
               ▼
┌───────────────────────────────────────────────────┐
│ Tier 2: Opus 4.7 + 截图 + 上一步 DOM 节选          │
│ Cost: ~$0.08/call · Latency: ~8s                  │
└──────────────┬────────────────────────────────────┘
               │ ✓
               ▼
           执行 → 校验

  ✗ 仍失败
               ▼
┌───────────────────────────────────────────────────┐
│ Tier 3: NEEDS_HUMAN(飞书 @Tarry,带截图链接)      │
└───────────────────────────────────────────────────┘
```

### 7.3 Action 协议(模型输出 schema)

```python
class Action(BaseModel):
    kind: Literal["click", "fill", "select", "wait", "abort"]
    selector: str                    # 优先 text=... / role=... 语义化
    value: str | None = None         # 仅 fill/select 用
    confidence: float = Field(ge=0, le=1)
    reasoning: str                   # 模型解释为啥这么选(写日志)

class Decision(BaseModel):
    actions: list[Action]            # 可能需要多步(罕见,但允许)
    is_blocked: bool = False         # True = 需要人接管
    blocked_reason: str | None = None
```

**为什么不返回坐标?**(ADR-0003)
- 坐标随浏览器尺寸/缩放变化,脆弱
- selector 是 Playwright 原生执行单位,即使 LLM 给错也可被 `expect()` 验证
- 软兜底:LLM 给出的 selector 仍走 Playwright 的重试/wait 机制

### 7.4 Prompt 模板要点

```
你正在帮助自动化操作上海电子税务局。
当前步骤:{step_name} - {step_intent}
预期可见元素:{expected_visible}
但 selector `{failed_selector}` 没找到元素。

请基于截图判断:
1. 页面是否还在预期流程内?(如果不是,is_blocked=true)
2. 如果在,正确的元素是什么?返回 selector 优先级:
   - text="精确文本"
   - role="button" name="按钮名"
   - 最后才用 CSS

输出 JSON 严格遵循 schema(略)
```

**安全栏杆**
- max_tokens 限定 500——防止 LLM 长篇大论
- 拒绝列表:`evaluate`, `eval`, `download`, `system` 这些 selector 永不执行
- 每个 run 最多调 5 次 LLM,超过自动升级到 NEEDS_HUMAN(防 LLM 死循环烧钱)

---

## 8. 错误分类与处理矩阵

```python
class TaxAutoError(Exception): ...

# 瞬时,自动重试 3 次
class TransientError(TaxAutoError): ...
class NetworkTimeoutError(TransientError): ...

# session 层
class SessionExpired(TaxAutoError): ...        # 重定向到登录页
class SessionLocked(TaxAutoError): ...         # 并发冲突

# selector 层 → 触发 vision fallback
class SelectorMissError(TaxAutoError):
    def __init__(self, step, selector, intent): ...

# 业务层
class FaceVerifyRequired(TaxAutoError): ...    # 二次扫脸,通知人工
class ExportTimeout(TaxAutoError): ...         # ZIP 超过 10 分钟还没好
class DataIntegrityError(TaxAutoError): ...    # 拉到的文件数不对

# 致命,不重试
class TaxBureauChanged(TaxAutoError): ...      # vision 三次都失败
class InvalidConfig(TaxAutoError): ...
```

| 异常 | 重试 | 升级 | 通知 |
|------|------|------|------|
| `TransientError` | 3 次,指数退避 | 仍失败 → `FAILED` | WARN |
| `SessionExpired` | ❌ | → `NEEDS_HUMAN`(去登录) | CRITICAL |
| `SelectorMissError` | ❌ | → `VISION_FALLBACK` | INFO |
| `FaceVerifyRequired` | 等 180s | → `FAILED` | CRITICAL(@人) |
| `ExportTimeout` | 1 次重新提交 | → `FAILED` | WARN |
| `DataIntegrityError` | ❌ | → `FAILED` | CRITICAL |
| `TaxBureauChanged` | ❌ | → `FAILED` | CRITICAL(@人) |

---

## 9. 调度与并发模型

### 9.1 单机调度

```
launchd (每天 18:00)
    ↓
scheduler.py
    ├─ 查 tasks WHERE enabled=1 AND schedule_cron 匹配今天
    ├─ 查 sessions WHERE status='VALID' AND last_used_at < now - 5min
    └─ 按客户分桶,串行派发(同税号一定串行)
        ↓
worker.py --tax-id 91310000... --task-id 42
    ↓
    单进程,跑完一个客户的完整 7 步
    ↓
    返回 exit code,scheduler 决定是否派下一个
```

### 9.2 并发规则

- **同税号串行**(税务局风控边界)——锁基于 `sessions.status='LOCKED'` + 文件锁 `flock(runtime/session/{tax_id}/.lock)`
- **跨税号并行**——但最多 2 个并发 worker(Mac mini 资源约束 + 避免显得"机器人化")
- **失败后 30 分钟内不重试**——防止重复触发风控

### 9.3 launchd 配置(`scheduler/launchd.plist.template`)

```xml
<plist version="1.0">
<dict>
    <key>Label</key><string>ai.tarry.tax_auto.scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/tarrywang/.venv/tax_auto/bin/python</string>
        <string>-m</string><string>tax_auto.cli</string>
        <string>run</string><string>--all-scheduled</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    <key>StandardOutPath</key><string>/Users/tarrywang/.../runtime/logs/launchd.out.log</string>
    <key>StandardErrorPath</key><string>/Users/tarrywang/.../runtime/logs/launchd.err.log</string>
    <key>WorkingDirectory</key><string>/Users/tarrywang/.../tax_auto</string>
</dict>
</plist>
```

---

## 10. 安全模型

### 10.1 凭证管理

| 凭证 | 存储 | 访问方式 |
|------|------|---------|
| 电子税务局账号密码 | **不存储**——只在人工登录时手动输入 | N/A |
| Chromium cookies | `runtime/session/{tax_id}/`,chmod 700 | Playwright 原生 |
| ANTHROPIC_API_KEY | macOS Keychain | `keyring` 库读取 |
| LARK_WEBHOOK_URL | `.env`(本地)/ Keychain(生产) | pydantic-settings |
| GLM/DeepSeek key(备) | macOS Keychain | 同上 |

**`.env.example`**(committed)
```
ANTHROPIC_API_KEY=          # 从 Keychain 读取,或临时覆盖
LARK_WEBHOOK_URL=
DEFAULT_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
```

### 10.2 数据脱敏

- 日志里**永不**打印完整 cookie / Authorization header
- LLM 调用前对截图做轻度处理:**保留 UI 结构,但模糊化可能含 PII 的明细文字行**(后期需要时再加,初版不做)
- 飞书通知里发金额汇总,**不发明细发票号**

### 10.3 合规边界

- **登录交给人**——扫脸/短信由用户本人完成,我们绝不存密码
- **审计完整**——`audit_log` 表完整记录每次 LOGIN/EXPORT,留 3 年
- **数据出境**——默认 Anthropic API 在境外,**对数据敏感客户切换 GLM/DeepSeek**(在 config 里一行切换)
- **session 不离开本机**——cookies 不上云、不进 git

---

## 11. 观测性(Observability)

### 11.1 日志层级(loguru)

```python
logger.add(
    "runtime/logs/tax_auto.log",
    rotation="50 MB", retention="30 days", compression="gz",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[run_id]} | {extra[tax_id]} | {message}",
    serialize=True,  # JSON,便于后期接 Loki
)
```

**结构化字段**(每条日志都带):
- `run_id` · `tax_id` · `step` · `state` · `duration_ms`

### 11.2 Playwright Trace

- **每次 run 都开 trace**——`context.tracing.start(screenshots=True, snapshots=True, sources=False)`
- 成功 run 保留 7 天,失败 run 保留 30 天
- 通过 `python -m tax_auto.scripts.replay <run_id>` 一键在 trace viewer 里回放

### 11.3 指标(`obs/metrics.py`)

每日聚合,写到 `runtime/metrics/daily.json`:
```json
{
  "date": "2026-05-26",
  "runs_total": 12,
  "runs_done": 11,
  "runs_failed": 1,
  "llm_calls_total": 18,
  "llm_cost_cents": 47,
  "selectors_miss_rate": 0.04,
  "p50_duration_ms": 142000,
  "p95_duration_ms": 380000
}
```

未来接 Grafana 时直接 `tail -F`。

---

## 12. 通知系统

### 12.1 通知级别

| Level | 触发 | 通道 |
|-------|------|------|
| `INFO` | run 开始 / 完成,有汇总 | 飞书机器人静默消息 |
| `WARN` | selector miss、轻度重试 | 飞书,不 @ 人 |
| `CRITICAL` | session 过期、二次扫脸、`FAILED` | 飞书 @Tarry + macOS 通知 |

### 12.2 飞书消息模板

```
✅ [tax_auto] 悦舍餐饮 · 2026-04 月发票拉取完成
    · 发票 38 张,合计 ¥24,580.00
    · 耗时 2m18s,LLM 调用 0 次
    · run_id: 01HXKM...
```

```
🚨 [tax_auto] @Tarry 塔锐自家税号触发二次扫脸,等待人工
    · 步骤 step_4_select_and_export
    · 截图: http://mac-golden.local:8080/traces/01HXKM/step_4.png
    · 操作:打开 Chromium 窗口完成扫脸,脚本会自动继续
```

---

## 13. 配置管理

### 13.1 三层 config

```python
# config.yaml(项目级,可 commit)
defaults:
  timeout_navigation_ms: 30000
  timeout_export_poll_s: 600
  llm_model_primary: claude-sonnet-4-6
  llm_model_fallback: claude-opus-4-7
  llm_max_calls_per_run: 5
  notification_level_default: INFO

customers:
  - tax_id: 91310000XXXXXXXXXX
    alias: 塔锐信息
    notification_level: WARN
  - tax_id: 91310000YYYYYYYYYY
    alias: 悦舍餐饮
    notification_level: CRITICAL
    use_china_llm: true   # 数据敏感,切到 GLM
```

```bash
# .env(本地秘密,gitignored)
ANTHROPIC_API_KEY=sk-ant-...
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

```bash
# CLI flag(临时覆盖)
python -m tax_auto fetch --customer all --month 2026-04 --log-level DEBUG
```

### 13.2 加载顺序

`pydantic-settings` 自动按 CLI > env > .env > config.yaml > defaults 解析。

---

## 14. 测试策略

| 层级 | 范围 | 工具 | 跑的频率 |
|------|------|------|---------|
| 单元 | state machine、selector dict、vision schema 解析 | pytest | 每次 commit |
| 契约 | Action JSON schema、LLM 输出解析 | pytest + 录制的 LLM response 回放 | 每次 commit |
| 集成 | 用 fixture 离线 HTML 跑 selector 命中率 | pytest + 真实 DOM 快照 | 每次 commit |
| 烟雾 | 对塔锐自家税号跑完整 7 步(干跑,不真下载) | pytest + 真实 Chromium | **每周一次** |
| 端到端 | 真实拉取塔锐自家上月发票 | 手动 | 改版后 |

**不会做的事**:
- 不 mock 政务局系统(mock 出来的"成功"毫无意义)
- 不写覆盖率 KPI(覆盖率高 ≠ 不出事故,真实事故复现优先)

---

## 15. 演进路径(Roadmap Beyond v1)

### v1.0 (本文档范围,W1-W6)
单租户、单机、CLI 触发、launchd 调度。给 1-3 个自己/熟人客户用。

### v1.5(产品化前夜,~3 个月后)
- Web UI(FastAPI + 简单 React):任务可视化、手动触发、查看历史
- 多 Mac mini 部署(主备),用 `rsync` 同步 session 目录
- Grafana + Loki 接管观测

### v2.0(SaaS 给客户用,~6 个月后)
- 真·多租户:数据库切 Postgres,session 加密上云
- 客户自助登录(他们扫脸,数据落到他们租户的 bucket)
- 套到 OpenClaw Hermes/小诸葛 体系里,变成"开发者卖给会计师事务所的工具"

**关键不变量**:`tax_auto/flow/` 这部分 600 行不重写。

---

## 16. 关键架构决策记录(ADR 索引)

- **ADR-0001** 不用 agent framework(Browser-Use / LangGraph)——见 HTML 文档 §1
- **ADR-0002** 用系统 Chrome(`channel="chrome"`)+ headed + persistent context,不下载 playwright chromium——反风控 + 绕开国内 CDN
- **ADR-0003** Vision 返回语义化 selector,不返回坐标——降级更软
- **ADR-0004** SQLite 而不是 Postgres——单机 + 任务量小,过度工程化反负担
- **ADR-0005** 同税号串行,跨税号并行 ≤2 —— 风控约束
- **ADR-0006** 金额用整型分(`amount_cents`)——浮点禁区
- **ADR-0007** 通知用飞书而不是邮件——延迟敏感场景

每条 ADR 都用 [Michael Nygard 模板](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md) 落到 `docs/adr/` 里(W1 起逐条补)。

---

## 17. 与 HTML build plan 的对齐 / 偏离

**完全对齐**:
- 技术栈选型(Playwright + Sonnet 4.6 兜底)
- 5 层架构(登录入口 → 调度 → Worker → Vision → 输出归档)
- 6 周路线图节奏(见 `tasks/todo.md`)

**HTML 未展开,本文档新增**:
- 完整 SQLite schema 与字段约束
- Run/Session 双状态机的形式化定义
- Vision Fallback 的三级降级链路(Sonnet → Opus → Human)
- LLM 调用上限(5 次/run)防止烧钱循环
- Action schema 与 selector 拒绝列表(安全栏杆)
- ADR 索引

**偏离/调整**:
- HTML 说"飞书 webhook + Mac 系统通知"——本文档进一步分了 INFO/WARN/CRITICAL 三级,只有 CRITICAL 才走 macOS 通知
- HTML 暗示 OFD 是默认格式——本文档保留 PDF/XML 多格式支持(全电票是 XML),但默认仍是 OFD

---

## 附录 A · 关键接口签名速查

```python
# tax_auto/core/session.py
def login_interactive(tax_id: str) -> SessionMeta: ...
def open_session(tax_id: str) -> tuple[BrowserContext, Page]: ...
def is_session_alive(page: Page) -> bool: ...

# tax_auto/flow/steps.py
def step_1_login_check(page: Page, ctx: RunContext) -> StepResult: ...
def step_2_navigate_to_query(page: Page, ctx: RunContext) -> StepResult: ...
def step_3_fill_query_form(page: Page, ctx: RunContext) -> StepResult: ...
def step_4_select_and_export(page: Page, ctx: RunContext) -> StepResult: ...
def step_5_handle_face_verify(page: Page, ctx: RunContext) -> StepResult: ...
def step_6_poll_export_zip(page: Page, ctx: RunContext) -> StepResult: ...
def step_7_download_and_archive(page: Page, ctx: RunContext) -> StepResult: ...

# tax_auto/vision/fallback.py
def resolve_with_claude(
    page: Page, step: str, intent: str, failed_selector: str
) -> Decision: ...

# tax_auto/cli.py
@app.command()
def login(tax_id: str): ...
@app.command()
def fetch(customer: str, month: str, dry_run: bool = False): ...
@app.command()
def run(all_scheduled: bool = False, task_id: int | None = None): ...
@app.command()
def replay(run_id: str): ...
```

---

**文档完。开始按 `tasks/todo.md` 施工。**
