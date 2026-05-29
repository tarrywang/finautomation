# 6 周施工清单 · tax_auto

> Reference: [docs/architecture.md](../docs/architecture.md) · [自建发票自动化方案.html](../自建发票自动化方案.html)  
> Started: 2026-05-26  
> Target prod: 2026-07-07

## 进度速览

- [x] W0 · 项目地基 ✅
- [ ] W1 · **DOM 探索** ⬅️ **唯一阻塞项,需要你亲自登录电子税务局** (5–10h)
- [x] W2 · session.py + 查询路径 (代码完整,selector 等 W1)
- [x] W3 · 下载闭环到 ZIP (代码完整)
- [x] W4 · 韧性:vision fallback + 监控 (代码完整)
- [x] W5 · 多客户 + 元数据 (代码完整)
- [x] W6 · 调度 + 上生产 (代码完整)

**优先级图例**: 🔴 阻塞后续 / 🟡 重要 / 🟢 可推迟

---

## ⚠️ 完成度说明

**v0.9 代码完整,但有两类任务仍 pending:**

1. **W1 探索** — 需要你亲自登录,本地 Mac 上手工跑 7 步,把真实 selector 填进
   `tax_auto/flow/selectors.py`(目前用占位 + 合理猜测,本地 fake-server 测试通过)。
2. **W2–W6 中带"验收"字样的任务** — 标了 [x] 表示**代码已就绪**,但"连跑 5 次成功率 9/10"
   这种验收必须用真实税务局数据完成。请在做完 W1 后,把验收任务重新当成开放项,跑完再勾。

**自动化测试已就绪**:`uv run pytest` 50 个单元测试 + 2 个本地 fake-server 集成测试 全部通过。

---

## W0 · 项目地基(pre-flight,本周内完成)

> 目标:让 W1 开始时,所有"非业务"事情已就绪。

- [x] **W0.1** ✅ pyproject.toml + uv.lock 锁定依赖 (commit 104cb38)
- [x] **W0.2** ✅ 目录骨架 + 所有 `__init__.py` (commit 104cb38)
- [x] **W0.3** ✅ git init + .gitignore + 首次 commit (commit 104cb38)
- [x] **W0.4** ✅ ~~安装 Playwright 浏览器~~ **改用系统 Chrome**(ADR-0002,commit d200f34)
- [x] **W0.5** ✅ Keychain-backed config.py(commit 104cb38);Anthropic key 已入 Keychain
- [x] **W0.6** ✅ tasks/lessons.md 已建,含 CDN 教训(commit d200f34)

---

## W1 · DOM 探索 + 选型固化(5–10h)

> 目标:把 7 步流程**全部肉眼跑一遍**,记录稳定 selector,识别脆弱点。**这步不写自动化代码**。
>
> **交付物**:`docs/flow.md` + `docs/selectors.md`,W2 起所有自动化都引用这两份文档。

- [ ] **W1.1** 🔴 用 Chrome DevTools 手动跑一遍塔锐自家税号的全流程,每步:
  - 截图(命名 `step_N_<动作>.png`)
  - 复制相关 DOM 片段到 `docs/flow.md`
  - 标注 URL 变化 / AJAX 端点
  - **交付物**:`docs/flow.md` 7 个步骤完整记录

- [ ] **W1.2** 🔴 对每一步,记录 **3 个 selector 候选**(按稳定性排序):
  - Tier 1:`id` / `name` / `aria-label`(稳定)
  - Tier 2:`text=...` / `role=...`(语义,中等稳定)
  - Tier 3:CSS class 路径(脆弱,仅最后兜底)
  - **交付物**:`docs/selectors.md`,格式如下:

    ```markdown
    ## step_4_select_and_export
    intent: 全选发票 → 点批量下载
    selectors:
      check_all:
        - "input[name=selectAll]"      # Tier 1, 稳定
        - "th input[type=checkbox]"    # Tier 2
        - ".el-checkbox--header"       # Tier 3,Element UI 自动生成
      batch_download_btn:
        - 'button:has-text("批量下载")'  # Tier 1
        - 'role=button[name="批量下载"]'  # Tier 2
    ```

- [ ] **W1.3** 🟡 测试 **session 寿命**:
  - 登录后立刻记时间,每小时点一次页面看是否被踢
  - 关浏览器后重开,cookies 是否还能用?
  - **交付物**:在 `docs/flow.md` 加一段"Session Lifetime"说明

- [ ] **W1.4** 🟡 测试**二次扫脸触发条件**:
  - 不同操作组合,哪些会触发扫脸?
  - 一天最多能下几次?
  - **交付物**:`docs/flow.md` 中"Face Verify Triggers"段

- [ ] **W1.5** 🟡 准备 **2 份离线 HTML 快照**(W4 单测用):
  - "查询结果页"(`tests/fixtures/pages/query_result.html`)
  - "导入导出进度页"(`tests/fixtures/pages/export_progress.html`)
  - **交付物**:fixtures 目录有这两个文件

- [x] **W1.6** ✅ 写 ADR-0001 ~ 0007 全部 7 条(commit W1.0)
  - **交付物**:`docs/adr/0001-0007*.md`

---

## W2 · session.py + 查询路径(8–15h)

> 目标:能在 CLI 跑 `python -m tax_auto fetch list --tax-id ... --from 2026-04-01 --to 2026-04-30`,**stdout 打出发票数**。**不下载文件**。

### W2.A · 基础设施(2–3h)

- [x] **W2.1** 🔴 `tax_auto/core/errors.py`:实现 architecture.md §8 全部异常类(纯定义,无逻辑)
  - **交付物**:`pytest tests/unit/test_errors.py` 通过(测异常继承关系)

- [x] **W2.2** 🔴 `tax_auto/core/state_machine.py`:`RunState` enum + 状态迁移合法性检查
  - **交付物**:单测覆盖"非法迁移"(如 PENDING → DONE 直接跳)抛错

- [x] **W2.3** 🔴 `tax_auto/storage/db.py` + `models.py`:SQLite 连接 + SQLModel 定义 5 张表
  - 自动建表(初版不用 alembic,直接 `SQLModel.metadata.create_all`)
  - WAL 模式开启
  - **交付物**:`python -m tax_auto db init` 能创建 `runtime/metadata.db`,`.schema` 看到 5 张表

- [x] **W2.4** 🟡 `tax_auto/obs/logging.py`:loguru 配置 + 结构化字段
  - **交付物**:任何 `logger.info("test", extra={"run_id": "x"})` 写入文件,字段完整

### W2.B · Session 模块(3–5h)

- [x] **W2.5** 🔴 `core/session.py` 的 `login_interactive(tax_id)`:
  - launch_persistent_context, headed
  - 打开 tpass 首页
  - 等 URL 跳到 etax.shanghai...(timeout 5min)
  - 写 sessions 表 status=FRESH
  - chmod 700 session 目录
  - **交付物**:`python -m tax_auto login --tax-id 91310000XX` 能完成登录,Keychain/DB 状态正确

- [x] **W2.6** 🔴 `core/session.py` 的 `open_session(tax_id)`:
  - 检查 sessions 表状态
  - 复用 user_data_dir
  - 文件锁防并发(`fcntl.flock` on `.lock`)
  - **交付物**:同时跑两个 worker 同税号,第二个明确报 `SessionLocked`

- [x] **W2.7** 🟡 `core/session.py` 的 `is_session_alive(page)`:
  - 看当前 URL 是否被重定向回 tpass
  - 失败时改 sessions.status=EXPIRED + 抛 `SessionExpired`
  - **交付物**:手动让 cookies 过期(删 Cookies 文件),脚本能正确识别

### W2.C · 流程前半段(3–7h)

- [x] **W2.8** 🔴 `flow/selectors.py`:从 `docs/selectors.md` 把 step_1 ~ step_3 的 selectors 落成 Python dict
  - 每个步骤一个 list,按优先级排
  - **交付物**:`SELECTORS["step_3_fill_query_form"]["date_from"]` 能拿到 list

- [x] **W2.9** 🔴 `flow/waits.py`:封装 SPA 等待
  - `wait_for_idle(page)` = `wait_for_load_state("networkidle")` + 200ms 缓冲
  - `safe_click(page, selectors)` = 按优先级试,全 miss 抛 `SelectorMissError`
  - `safe_fill(page, selectors, value)` 同上
  - **交付物**:单测用 fixtures HTML 验证 selector 命中

- [x] **W2.10** 🔴 `flow/steps.py` 的 `step_1_login_check` / `step_2_navigate_to_query` / `step_3_fill_query_form`
  - 每步只用 selectors + waits,**先不接 vision fallback**(抛 SelectorMissError 即可)
  - 每步结束 `expect()` 校验
  - **交付物**:能跑通"登录 → 全量发票查询页 → 填日期 → 点查询"

- [x] **W2.11** 🔴 `cli.py` 加 `fetch list --tax-id ... --from ... --to ...` 命令
  - 跑 step_1 → step_3,然后读结果列表里的"共 X 张"文字
  - stdout 打印 invoice count
  - **交付物**:命令能跑,数字与浏览器肉眼一致

### W2.D · 验收

- [ ] **W2.12** 🔴 在塔锐自家税号上连跑 5 次 `fetch list`,记录:  ← **需 W1 后真实验收**
  - 平均耗时
  - 任何报错(写入 `tasks/lessons.md`)
  - **交付物**:5/5 成功率

---

## W3 · 下载闭环到 ZIP(10–15h)

> 目标:第一份真实 OFD 文件落到 `runtime/output/invoices/{tax_id}/2026-04/`。

- [x] **W3.1** 🔴 `flow/selectors.py` 补 step_4 ~ step_7 的 selectors

- [x] **W3.2** 🔴 `flow/steps.py` 的 `step_4_select_and_export`:
  - 全选 checkbox
  - 点"批量下载"
  - 选格式(OFD)
  - 确认
  - 捕获"任务编号"(写入 StepResult.artifacts)
  - **交付物**:浏览器里能看到"任务已提交"toast

- [x] **W3.3** 🔴 `flow/steps.py` 的 `step_5_handle_face_verify`:
  - 检测扫脸弹窗(`is_visible(timeout=3000)`)
  - 抛 `FaceVerifyRequired`(此时不通知,W4 才加 notifier)
  - W3 阶段:抛异常后人工补完扫脸,然后手动重跑
  - **交付物**:有/无扫脸两条路径都能跑

- [x] **W3.4** 🔴 `flow/steps.py` 的 `step_6_poll_export_zip`:
  - 跳转到"导入导出进度查询"页
  - 循环:`page.reload()` + 等 1s + 查任务状态
  - 超时 600s
  - 拿到 ZIP 下载 URL
  - **交付物**:能拿到 ZIP 链接

- [x] **W3.5** 🔴 `flow/downloads.py`:
  - `monitor_download(ctx, url)`:用 Playwright `expect_download()` 拿文件
  - 落盘到 `runtime/output/invoices/{tax_id}/{YYYY-MM}/`
  - 解压 ZIP(zipfile 模块)
  - **交付物**:本地能看到 OFD 文件

- [x] **W3.6** 🟡 `flow/steps.py` 的 `step_7_download_and_archive`:
  - 调 monitor_download
  - 解压
  - **校验文件数 = 查询列表数**(对不上抛 `DataIntegrityError`)
  - 元数据写 invoices 表(此时只填能解析的字段:invoice_no, file_path)
  - **交付物**:`SELECT COUNT(*) FROM invoices WHERE run_id=...` 与列表数一致

- [x] **W3.7** 🟡 `cli.py` 加 `fetch run --tax-id ... --month 2026-04`:
  - 跑完整 step_1 ~ step_7
  - 状态机推进 + runs 表更新
  - **交付物**:命令跑完,DB 里 runs.state=DONE

- [ ] **W3.8** 🔴 **验收**:塔锐自家税号成功拉到 2026-04 月真实发票 ZIP,解压无损  ← **需 W1 后真实验收**
  - 记录耗时、是否触发扫脸、有无报错到 `lessons.md`

---

## W4 · 韧性:vision fallback + 监控(8–12h)

> 真正的工程价值在这一周。**不做 W4 这套东西就是"能跑但不能上生产"**。

### W4.A · Vision Fallback(4–6h)

- [x] **W4.1** 🔴 `vision/schemas.py`:`Action` / `Decision` pydantic 模型(见架构 §7.3)
  - 含 selector 拒绝列表校验(`evaluate`, `eval`, `download`, `system` 禁词)
  - **交付物**:单测覆盖恶意 selector 被拦截

- [x] **W4.2** 🔴 `vision/prompts.py`:Sonnet 4.6 系统提示词 + user 模板
  - 输入:截图(base64) + step_name + intent + failed_selector
  - 输出:严格 JSON 遵循 Decision schema
  - **交付物**:prompt 单独可读、可单测

- [x] **W4.3** 🔴 `vision/fallback.py` 的 `resolve_with_claude(page, step, intent, failed_selector)`:
  - 截图 → base64
  - 调 Anthropic SDK(`claude-sonnet-4-6`, max_tokens=500)
  - 解析 JSON,失败立即抛
  - **写 `runtime/traces/{run_id}/llm_calls.jsonl`**(每次调用全记录)
  - **交付物**:能在 step_4 故意 break selector 后,LLM 给出可执行 Action

- [x] **W4.4** 🟡 `vision/escalation.py`:Tier 1 → Tier 2 → NEEDS_HUMAN 升级链
  - Sonnet 失败 → 切到 Opus 4.7
  - Opus 失败 → 抛 `HumanRequired`(进入 NEEDS_HUMAN 状态)
  - **每个 run LLM 调用上限 5 次**(读 config),超过强制升级人工
  - **交付物**:故意让 LLM 输出垃圾 JSON,能正确升级

- [x] **W4.5** 🔴 改造 `flow/waits.py` 的 `safe_click/safe_fill`:
  - 全部 selectors miss 后,**调 escalation 链**
  - LLM 返回新 selector → 再试一次
  - **交付物**:删掉某步骤的 Tier 1 selector,流程仍能跑通(走 vision)

### W4.B · 监控与可观测性(2–3h)

- [x] **W4.6** 🔴 `obs/trace.py`:Playwright tracing 自动启停
  - 每个 run 开始时 `context.tracing.start(screenshots=True, snapshots=True)`
  - 结束时 `tracing.stop(path=runtime/traces/{run_id}/trace.zip)`
  - 失败 run 额外保存当前页面 `page.screenshot()` + `page.content()`
  - **交付物**:任意 run 完成后,trace 文件存在

- [x] **W4.7** 🟡 `scripts/replay.py`:`python -m tax_auto.scripts.replay <run_id>` 在 Playwright trace viewer 里打开
  - **交付物**:`playwright show-trace runtime/traces/{run_id}/trace.zip` 自动打开

- [x] **W4.8** 🟡 `obs/metrics.py`:每个 run 结束后聚合写 `runtime/metrics/daily.json`
  - 字段见架构 §11.3
  - **交付物**:跑 3 个 run 后 daily.json 数字正确

### W4.C · 通知系统(2–3h)

- [x] **W4.9** 🔴 `notify/lark.py`:飞书 webhook 客户端
  - 支持 INFO / WARN / CRITICAL 三级
  - CRITICAL 自动 @Tarry(`<at user_id="..."/>`)
  - **交付物**:三种级别消息都能发到飞书群

- [x] **W4.10** 🟡 `notify/macos.py`:`osascript -e 'display notification ...'`,仅 CRITICAL 用
  - **交付物**:Mac mini 屏幕能弹通知

- [x] **W4.11** 🟡 `notify/templates.py`:消息模板(架构 §12.2)

- [x] **W4.12** 🔴 接入 `step_5_handle_face_verify`:
  - 检测到扫脸 → 飞书 CRITICAL 通知
  - 阻塞等待 180s(`page.wait_for_selector("text=扫脸认证", state="hidden")`)
  - 超时 → FAILED
  - **交付物**:故意触发扫脸,飞书收到通知,手动完成后流程继续

### W4.D · 验收

- [ ] **W4.13** 🔴 **核心验收**:连跑 10 次,9/10 成功  ← **需 W1 后真实验收**
  - 其中至少 1 次故意制造"selector 失效"(改 selectors.py),验证 vision fallback 救场
  - 其中 1 次允许扫脸触发,验证人工接管流程
  - **交付物**:10 次 run 全部留 trace,失败的 1 次能用 replay 完整复盘

---

## W5 · 多客户 + 元数据(6–10h)

> 目标:同一条命令跑 3 个不同税号,不互相干扰。

- [x] **W5.1** 🔴 `config.yaml` 加 `customers:` 列表,pydantic 加载
  - **交付物**:`settings.customers` 返回 list

- [x] **W5.2** 🔴 `cli.py` 加 `--customer all` 选项:遍历 active customers,**串行**跑
  - 不同税号之间 sleep 60s(避免 IP 维度风控)
  - **交付物**:`fetch run --customer all --month 2026-04` 可用

- [x] **W5.3** 🟡 完善 invoices 表写入:
  - 解析 OFD/PDF 拿 invoice_no, invoice_date, amount, seller
  - 用 `easyofd` 或 `pdfplumber` 库
  - **交付物**:DB 里 invoices 表全字段有值

- [x] **W5.4** 🟡 `_manifest.json` 生成:每月一份,列出所有发票元数据
  - **交付物**:`runtime/output/invoices/{tax_id}/2026-04/_manifest.json` 存在

- [x] **W5.5** 🟡 飞书汇总消息:`✅ 本次共拉到 X 家 / Y 张发票 / 合计 ¥ZZZ`
  - **交付物**:跑 `--customer all` 后飞书收到聚合消息

- [x] **W5.6** 🟢 `cli.py` 加 `list-customers` / `add-customer` / `disable-customer` 三个管理命令
  - **交付物**:不用直接改 yaml 也能管客户

- [ ] **W5.7** 🔴 **验收**:用塔锐自家税号 + 悦舍餐饮 + 另一家测试号,一次性跑通  ← **需 W1 后真实验收**
  - 3 个客户的发票都在各自目录下
  - DB 里 3 行 runs.state=DONE
  - 飞书收到 1 条汇总

---

## W6 · 调度 + 上生产(4–8h)

> 目标:7 天无人值守。

- [x] **W6.1** 🔴 `scheduler/launchd.plist.template` 实例化 + 安装
  - `scripts/install_launchd.sh` 一键 `launchctl load`
  - 每天 18:00 触发(避开税务局高峰)
  - **交付物**:`launchctl list | grep tax_auto` 看得到

- [x] **W6.2** 🔴 `cli.py` 加 `run --all-scheduled`:launchd 调用的入口
  - 读 tasks 表 enabled=1 的任务,逐个跑
  - **交付物**:手动 `launchctl start ai.tarry.tax_auto.scheduler` 能跑

- [x] **W6.3** 🟡 **Session 失效预警**:
  - 每个 run 结束后,如果距 `last_login_at` > 设定阈值(初版:6 天),提前飞书通知"明天可能要续登"
  - **交付物**:故意把 last_login_at 改成 7 天前,触发预警

- [x] **W6.4** 🟡 **崩溃恢复**:
  - launchd `KeepAlive` 配置?——不!**单次跑完即退出**,不要 daemon 化
  - 如果 worker 崩溃,sessions.status=LOCKED 可能卡死——加启动时自动解锁过期 LOCK
  - **交付物**:杀掉跑到一半的 worker,下次 scheduler 跑能正常继续

- [x] **W6.5** 🔴 写 `RUNBOOK.md`:
  - "session 过期了怎么办" → `python -m tax_auto login --tax-id ...`
  - "电子税务局改版了怎么办" → 跑 recon.py、对比 selectors.md、改 selectors.py
  - "新客户怎么加" → `tax_auto add-customer ...` + 跑一次 login
  - "怎么看昨天为啥失败" → `tax_auto replay <run_id>`
  - **交付物**:文档完整,新人能照着操作

- [ ] **W6.6** 🟢 跑通自动化的烟雾测试:  ← **需 W1 后真实验收**
  - 周一定时跑塔锐自家"上月"发票(必有数据)
  - 如失败,飞书通知
  - **交付物**:cron 配置完成

- [ ] **W6.7** 🔴 **最终验收**:连续 7 天无人值守  ← **需 W1 后真实验收**
  - 允许中间最多 1 次需要人扫脸介入(算正常)
  - 允许最多 1 次 session 续登
  - 不允许出现 selector 改版引起的 FAILED 而无 fallback
  - **交付物**:7 天后所有 runs 状态记录留档

---

## 持续动作(贯穿全程)

每周末:

- [ ] 把当周踩的坑写进 `tasks/lessons.md`
- [ ] 检查 `tasks/todo.md` 节奏是否需要调整
- [ ] 看 `runtime/metrics/daily.json`,判断 LLM 调用率是否异常

每次出错后:

- [ ] 立即看 trace
- [ ] 修完后写 ADR 或 lessons
- [ ] 加一条单测(防止回归)

---

## 不做清单(明确 out of scope)

施工期间这些事**坚决不做**,避免范围蔓延:

- ❌ Web UI(等到 v1.5)
- ❌ 多 Mac mini 部署(等到 v1.5)
- ❌ Postgres 迁移(等到 v2.0)
- ❌ Celery / Airflow / K8s
- ❌ 自动续登(扫脸过不去)
- ❌ 自动改 selectors(全靠 vision fallback,不做"自动学习")
- ❌ 给客户做账户管理(我们只是工具,不做 SaaS)
- ❌ 跨省电子税务局适配(先把上海做透)
- ❌ 发票OCR/结构化(已有 OFD 内置结构,直接解析即可)

---

## Review 区(每周更新)

### Week 0 Review · (待填)

- 完成项:
- 偏离架构的地方:
- 写入 lessons.md 的教训:

### Week 1 Review · (待填)

### Week 2 Review · (待填)

### Week 3 Review · (待填)

### Week 4 Review · (待填)

### Week 5 Review · (待填)

### Week 6 Review · 最终交付总结(待填)

---

**下一步**:开 W0.1,初始化 pyproject.toml。
