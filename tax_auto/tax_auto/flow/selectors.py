"""Selector catalog — single source of truth for all DOM queries.

Generated from docs/selectors.md by scripts/sync_selectors.py.
DO NOT EDIT BY HAND — change docs/selectors.md and re-run sync.
"""

from __future__ import annotations

# fmt: off
SELECTORS: dict[str, dict[str, list[str]]] = {
    "step_2_navigate": {
        "menu_办税": [
            "text=我要办税"
        ],
        "menu_税务数字账户": [
            "text=税务数字账户"
        ],
        "link_全量发票查询": [
            "text=全量发票查询"
        ],
        "page_loaded_anchor": [
            "input[placeholder*=\"开票日期\"]",
            "text=开票日期"
        ]
    },
    "step_3_fill_query": {
        "input_date_from": [
            "input[placeholder*=\"开票日期(起)\"]",
            "input[placeholder*=\"开票日期（起）\"]"
        ],
        "input_date_to": [
            "input[placeholder*=\"开票日期(止)\"]",
            "input[placeholder*=\"开票日期（止）\"]"
        ],
        "button_query": [
            "button:has-text(\"查询\")",
            "role=button[name=\"查询\"]"
        ],
        "result_count_label": [
            "text=/共.*?张/",
            "text=/共.*?条/"
        ],
        "empty_state": [
            "text=暂无数据",
            "text=没有符合条件"
        ]
    },
    "step_4_select_export": {
        "checkbox_select_all": [
            "th input[type=checkbox]",
            "role=columnheader >> input[type=checkbox]"
        ],
        "button_batch_download": [
            "button:has-text(\"批量下载\")",
            "role=button[name=\"批量下载\"]"
        ],
        "format_ofd_radio": [
            "label:has-text(\"OFD\")",
            "input[type=radio][value=\"OFD\"]"
        ],
        "format_pdf_radio": [
            "label:has-text(\"PDF\")"
        ],
        "button_confirm_export": [
            "button:has-text(\"确认\")",
            "button:has-text(\"确定\")"
        ],
        "toast_submitted": [
            "text=任务已提交",
            "text=任务提交成功"
        ]
    },
    "step_5_face_verify": {
        "modal_face_verify": [
            "text=扫脸认证",
            "text=人脸识别",
            "text=/请.*扫脸/"
        ]
    },
    "step_6_poll_export": {
        "link_导入导出": [
            "text=导入导出",
            "text=任务进度"
        ],
        "row_latest_task": [
            "table tbody tr:first-child"
        ],
        "status_done": [
            "text=/已完成|可下载/"
        ],
        "link_download_zip": [
            "a:has-text(\"下载\")",
            "role=link[name=\"下载\"]"
        ]
    }
}
# fmt: on


def get_candidates(step: str, key: str) -> list[str]:
    """Return ordered candidate selectors. Raises KeyError if unknown."""
    return SELECTORS[step][key]
