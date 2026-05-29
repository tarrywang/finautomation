"""Minimal in-process HTTP server that mimics the e-tax bureau's 7-step flow.

Used by tests/integration/test_smoke_flow.py to drive the worker end-to-end
without touching the real bureau. Pages are deliberately simple but contain
selectors named in tax_auto.flow.selectors so the deterministic path lights up.
"""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def _page(title: str, body: str) -> bytes:
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>{body}</body></html>"""
    return html.encode("utf-8")


# In-memory state — reset between test runs
class _State:
    export_submitted: bool = False
    poll_count: int = 0


STATE = _State()


PAGES = {
    "/": _page("e-tax landing", """
        <a href="/banshui">我要办税</a>
    """),
    "/banshui": _page("我要办税", """
        <p>我要办税</p>
        <a href="/digital">税务数字账户</a>
    """),
    "/digital": _page("数字账户", """
        <p>税务数字账户</p>
        <a href="/query">全量发票查询</a>
    """),
    "/query": _page("全量发票查询", """
        <form action="/query/submit" method="get">
            <input placeholder="开票日期(起)" name="from" />
            <input placeholder="开票日期(止)" name="to" />
            <button type="submit">查询</button>
        </form>
    """),
}


class FakeEtaxHandler(BaseHTTPRequestHandler):
    """Routes the 7-step flow. Quiet logging to keep test output clean."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return  # silence

    def _respond(self, content: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in PAGES:
            self._respond(PAGES[path])
            return

        if path == "/query/submit":
            q = parse_qs(parsed.query)
            self._respond(_page("查询结果", f"""
                <p>开票日期(起): {q.get("from", [""])[0]}</p>
                <p>开票日期(止): {q.get("to", [""])[0]}</p>
                <p>共 2 张</p>
                <table>
                  <thead><tr><th><input type="checkbox" /></th><th>发票号</th></tr></thead>
                  <tbody>
                    <tr><td><input type="checkbox" /></td><td>24310000000001234567</td></tr>
                    <tr><td><input type="checkbox" /></td><td>24310000000001234568</td></tr>
                  </tbody>
                </table>
                <button onclick="location.href='/export'">批量下载</button>
            """))
            return

        if path == "/export":
            self._respond(_page("选格式", """
                <label><input type="radio" name="fmt" value="OFD" />OFD</label>
                <label><input type="radio" name="fmt" value="PDF" />PDF</label>
                <a href="/export/confirm"><button>确认</button></a>
            """))
            return

        if path == "/export/confirm":
            STATE.export_submitted = True
            STATE.poll_count = 0
            self._respond(_page("提交成功", """
                <p>任务已提交</p>
                <a href="/progress">导入导出</a>
            """))
            return

        if path == "/progress":
            STATE.poll_count += 1
            # On the 2nd visit, the ZIP is "ready"
            if STATE.poll_count >= 2:
                self._respond(_page("任务进度", """
                    <table><tbody><tr>
                        <td>已完成</td>
                        <td><a href="/zip">下载</a></td>
                    </tr></tbody></table>
                """))
            else:
                self._respond(_page("任务进度", """
                    <table><tbody><tr>
                        <td>生成中</td>
                        <td>-</td>
                    </tr></tbody></table>
                """))
            return

        if path == "/zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("24310000000001234567.xml",
                            "<Invoice><Fphm>24310000000001234567</Fphm>"
                            "<Kprq>2026-04-15</Kprq><Jshj>100.00</Jshj></Invoice>")
                zf.writestr("24310000000001234568.xml",
                            "<Invoice><Fphm>24310000000001234568</Fphm>"
                            "<Kprq>2026-04-20</Kprq><Jshj>200.00</Jshj></Invoice>")
            payload = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="invoices.zip"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(404)
        self.end_headers()


def start(port: int = 0) -> tuple[HTTPServer, threading.Thread, str]:
    """Boot the fake server on a random port. Returns (server, thread, base_url)."""
    srv = HTTPServer(("127.0.0.1", port), FakeEtaxHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    return srv, th, base


def reset_state() -> None:
    STATE.export_submitted = False
    STATE.poll_count = 0
