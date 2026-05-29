# Selectors · 单一事实源(SSOT)

> 这是 `tax_auto/flow/selectors.py` 的人类可读版本。
>
> **填法**:从 `runtime/recon/step_*.md` 把 Tier 1/2/3 候选粘过来,按稳定性排序。
> **同步**:填完后跑 `uv run python scripts/sync_selectors.py` 自动把这里写回 `selectors.py`。

格式约定:

```yaml
step_<n>_<short_name>:
  <element_key>:
    - "<tier-1 most stable>"
    - "<tier-2 semantic>"
    - "<tier-3 brittle CSS, last resort>"
```

Playwright locator 语法参考:
- `text="精确文案"` / `text=/regex/`
- `role=button[name="按钮名"]`
- `[name="xx"]` / `[aria-label="xx"]` / `[placeholder*="xx"]`
- `button:has-text("批量下载")`

---

## step_2_navigate

```yaml
menu_办税:
  - 'text=我要办税'
  # TODO(W1): add Tier-1 (id/name/aria-label) and Tier-3 (css class) candidates

menu_税务数字账户:
  - 'text=税务数字账户'
  # TODO(W1)

link_全量发票查询:
  - 'text=全量发票查询'
  # TODO(W1)

page_loaded_anchor:
  - 'input[placeholder*="开票日期"]'
  - 'text=开票日期'
  # TODO(W1): add a stable id-based selector if available
```

---

## step_3_fill_query

```yaml
input_date_from:
  - 'input[placeholder*="开票日期(起)"]'
  - 'input[placeholder*="开票日期（起）"]'
  # TODO(W1): add name= or id= variant

input_date_to:
  - 'input[placeholder*="开票日期(止)"]'
  - 'input[placeholder*="开票日期（止）"]'
  # TODO(W1)

button_query:
  - 'button:has-text("查询")'
  - 'role=button[name="查询"]'
  # TODO(W1)

result_count_label:
  - 'text=/共.*?张/'
  - 'text=/共.*?条/'
  # TODO(W1): confirm the exact wording on real page

empty_state:
  - 'text=暂无数据'
  - 'text=没有符合条件'
  # TODO(W1): record actual empty-state text
```

---

## step_4_select_export

```yaml
checkbox_select_all:
  - 'th input[type=checkbox]'
  - 'role=columnheader >> input[type=checkbox]'
  # TODO(W1)

button_batch_download:
  - 'button:has-text("批量下载")'
  - 'role=button[name="批量下载"]'
  # TODO(W1)

format_ofd_radio:
  - 'label:has-text("OFD")'
  - 'input[type=radio][value="OFD"]'
  # TODO(W1): may not exist if site only offers PDF

format_pdf_radio:
  - 'label:has-text("PDF")'
  # TODO(W1)

button_confirm_export:
  - 'button:has-text("确认")'
  - 'button:has-text("确定")'
  # TODO(W1)

toast_submitted:
  - 'text=任务已提交'
  - 'text=任务提交成功'
  # TODO(W1): record exact toast text
```

---

## step_5_face_verify

```yaml
modal_face_verify:
  - 'text=扫脸认证'
  - 'text=人脸识别'
  - 'text=/请.*扫脸/'
  # TODO(W1): record actual modal title + any inner button
```

---

## step_6_poll_export

```yaml
link_导入导出:
  - 'text=导入导出'
  - 'text=任务进度'
  # TODO(W1): real menu path may need 1-2 intermediate clicks

row_latest_task:
  - 'table tbody tr:first-child'
  # TODO(W1): confirm sort order is "newest first"

status_done:
  - 'text=/已完成|可下载/'
  # TODO(W1)

link_download_zip:
  - 'a:has-text("下载")'
  - 'role=link[name="下载"]'
  # TODO(W1)
```

---

## 备注

- 任何用 Element-UI 自动生成 class 的元素(`.el-button--xxxx`),把它放 Tier 3,
  并在旁边写注释说明"重新构建时会变化"
- 如果某 key 的 Tier 1 找不到稳定锚点(政务系统经常如此),
  在文档里明确写"Tier 1: none",并依赖 Tier 2 + vision fallback
