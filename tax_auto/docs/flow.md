# 7-Step Flow · 真实电子税务局探索记录

> W1 交付物之一。请在用 `scripts/recon.py` 探索过程中边走边填本文档。
> 每步内容来源:`runtime/recon/step_*.md`(tarry.save 产物)+ 你的肉眼观察。

| 字段 | 含义 |
|---|---|
| URL | 该步骤结束时 `window.location.href` |
| 触发动作 | 用户/脚本要做的事(点击哪、填什么) |
| 预期校验 | 跳到下一步前必须可见的元素(给 `safe_wait_visible` 用) |
| Selector 命中情况 | Tier 1/2/3 哪一档抓到了 |
| 风险 / 观察 | 加载慢?有弹窗?偶发跳转? |

---

## Step 1 · 登录态校验

- **URL**: `<填>`
- **触发**: 打开 etax 首页;检查是否被重定向回 tpass
- **预期校验**: 页面 URL 包含 `etax.shanghai.chinatax.gov.cn`
- **风险**:cookies 过期时跳 tpass,这一步必须立即抛 `SessionExpired`

---

## Step 2 · 导航到全量发票查询

- **URL 起**: `<填>`
- **URL 止**: `<填>`
- **触发**: `我要办税` → `税务数字账户` → `全量发票查询`
- **每步等待**: `wait_for_load_state("networkidle")` + 200ms
- **预期校验**: 出现"开票日期"输入框
- **Selector**:
  - menu_办税: `<填,从 runtime/recon/step_2_menu_办税.md 抄>`
  - menu_税务数字账户: `<填>`
  - link_全量发票查询: `<填>`
  - page_loaded_anchor: `<填>`
- **风险 / 观察**: `<填>`

---

## Step 3 · 填查询条件 + 点查询

- **URL**: `<填>`
- **触发**: 填日期起/止 → 点查询
- **预期校验**: 结果列表出现"共 N 张/条"或"暂无数据"
- **Selector**:
  - input_date_from: `<填>`
  - input_date_to: `<填>`
  - button_query: `<填>`
  - result_count_label: `<填>`(用于解析 N)
  - empty_state: `<填>`
- **风险 / 观察**: 日期选择器是不是日历挂件?直接 fill 字符串能不能进?

---

## Step 4 · 全选 + 批量下载 + 选格式 + 确认

- **触发**: 勾全选 → 批量下载 → 选 OFD(或 PDF) → 确认
- **预期校验**: "任务已提交"toast
- **Selector**:
  - checkbox_select_all: `<填>`
  - button_batch_download: `<填>`
  - format_ofd_radio: `<填>`(若没有 OFD,记录"only PDF"事实)
  - format_pdf_radio: `<填>`
  - button_confirm_export: `<填>`
  - toast_submitted: `<填>`
- **风险 / 观察**: 单次能下多少张上限?

---

## Step 5 · 二次扫脸检测

- **触发**: 点了批量下载/确认后,有时弹"扫脸认证"
- **预期校验**: 没弹 → 继续 step_6;弹了 → 通知人工 + 等 180s
- **Selector**:
  - modal_face_verify: `<填,记录多种文案,如"扫脸认证" / "人脸识别">`
- **风险 / 观察**: 见下面 Face Verify Triggers 段

---

## Step 6 · 轮询导入导出进度

- **触发**: 跳到"导入导出"页 → 循环 reload 直到任务"已完成"
- **预期校验**: 行内出现"下载"链接
- **Selector**:
  - link_导入导出: `<填,记录菜单路径>`
  - row_latest_task: `<填>`
  - status_done: `<填>`
  - link_download_zip: `<填>`
- **风险 / 观察**: 平均要几次刷新?超时阈值合理吗?

---

## Step 7 · 下载 ZIP + 解压归档

- **触发**: 点下载链接 → Playwright `expect_download()` 捕获
- **预期校验**: 文件数 == step_3 解析出的 N
- **风险 / 观察**:
  - 文件命名规律(`24310000000001234567.ofd` 这种?)
  - 是 OFD 还是 XML(全电票)?
  - 单个 ZIP 还是分多个?

---

## Session Lifetime(W1.3)

记录登录后多久 session 被踢:

| 时间(自登录起) | 操作 | `tarry.check_session()` 结果 | 备注 |
|---|---|---|---|
| 0h | 刚登录 | alive | |
| 2h | `<填>` | `<填>` | |
| 4h | `<填>` | `<填>` | |
| 8h | `<填>` | `<填>` | |
| 跨天 | `<填>` | `<填>` | |

**结论**:`<填,例:有效期 ~6 小时,关浏览器后不丢>`

**影响代码**:`config.py` 的 `session_warning_due_days` 应改为 `<填>` 天。

---

## Face Verify Triggers(W1.4)

记录什么操作会触发二次扫脸:

| 场景 | 是否触发 | 说明 |
|---|---|---|
| 单次正常下载 | `<填>` | |
| 1 小时内连续下载 3 次 | `<填>` | |
| 切换查询日期范围 | `<填>` | |
| 跨设备登录(同税号 Mac + 手机) | `<填>` | |
| 静默 30 分钟后操作 | `<填>` | |

**结论 + 规避策略**:`<填>`

---

## 未覆盖场景(留给悦舍餐饮跑)

- 多页分页(本月发票 > 单页容量)
- 销项发票(本探索只覆盖了进项)
- 跨月查询(从今年到去年)
- 红字发票
