"""发票通 mars API client (synchronous, httpx-based).

Covers the endpoints we actually use:
- syncInvoicesRealTime  (#4.1) — real-time pull, returns base64-encoded JSON in `data`
- onlineStatus          (#25) — 国信助手 status probe (cert-mode only; misleading in 中间号 mode)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date
from email.utils import formatdate
from typing import Any

import httpx

from .sign import build_auth_value, build_canonical_string, sign

logger = logging.getLogger(__name__)

BASE_URL = "https://ivs.fapiao.com/mars"
API_VERSION = "20190618"


class FapiaoError(Exception):
    """Base class for 发票通 API errors."""

    def __init__(self, code: str, message: str, request_id: str | None = None):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"{code}: {message}")


@dataclass
class FapiaoCredentials:
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> FapiaoCredentials:
        cid = os.environ.get("FAPIAO_CLIENT_ID")
        csec = os.environ.get("FAPIAO_CLIENT_SECRET")
        if not (cid and csec):
            raise RuntimeError(
                "FAPIAO_CLIENT_ID and FAPIAO_CLIENT_SECRET must be set in environment"
            )
        return cls(client_id=cid, client_secret=csec)


class FapiaoClient:
    """Synchronous client. Single-tenant; pass credentials at construction time."""

    def __init__(
        self,
        creds: FapiaoCredentials,
        base_url: str = BASE_URL,
        timeout: float = 120.0,
    ):
        self._creds = creds
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FapiaoClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _sign_and_send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Sign + send, return (http_status, parsed_json_body)."""
        body_bytes = (
            json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        )
        content_md5 = (
            base64.b64encode(hashlib.md5(body_bytes).digest()).decode("ascii") if body_bytes else ""
        )
        headers = {
            "Accept": "",
            "Content-MD5": content_md5,
            "Content-Type": "application/json",
            "Date": formatdate(timeval=None, usegmt=True),
            "x-mars-api-version": API_VERSION,
            "x-mars-signature-nonce": str(uuid.uuid4()),
        }
        full_url = self._base_url + path
        sts = build_canonical_string(method, full_url, headers, query)
        signature = sign(self._creds.client_secret, sts)
        headers["signature"] = build_auth_value(self._creds.client_id, signature)

        logger.debug("→ %s %s", method, full_url)
        if method == "GET":
            resp = self._http.get(full_url, headers=headers, params=query)
        else:
            resp = self._http.request(method, full_url, headers=headers, content=body_bytes)
        logger.debug("← %d", resp.status_code)
        try:
            return resp.status_code, resp.json()
        except json.JSONDecodeError as e:
            raise FapiaoError(
                "NonJSON", f"non-JSON response (status={resp.status_code}): {resp.text[:200]}"
            ) from e

    # ─────────────────── public endpoints ───────────────────

    def online_status(self, tax_no: str) -> dict[str, Any]:
        """GET /api/base/onlineStatus — 国信助手在线状态。

        Note: in 中间号 mode this often returns UnboundAuthCode even when
        syncInvoicesRealTime works fine. Don't use as a health check in
        中间号 deployments. Use for cert-mode deployments only.
        """
        status, body = self._sign_and_send("GET", "/api/base/onlineStatus", query={"taxNo": tax_no})
        if status != 200:
            raise FapiaoError(
                body.get("code", "Unknown"),
                body.get("message", ""),
                body.get("requestId"),
            )
        return body

    def sync_invoices_realtime(
        self,
        tax_no: str,
        data_type: str,
        billing_date_start: date,
        billing_date_end: date,
        page_size: int = 50,
        *,
        invoice_type: str | None = None,
        invoice_status: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """POST /api/collect/syncInvoicesRealTime — real-time invoice pull.

        Returns:
            (raw_response, decoded_data) tuple.
            raw_response = the JSON wrapper {data: <base64>, ...}
            decoded_data = base64-decoded JSON dict with sections XXHZB/FPJCXX/JZFW/etc.
        """
        body: dict[str, Any] = {
            "taxNo": tax_no,
            "dataType": data_type,
            "startBillingDate": billing_date_start.isoformat(),
            "endBillingDate": billing_date_end.isoformat(),
            "pageSize": page_size,
        }
        if invoice_type:
            body["invoiceType"] = invoice_type
        if invoice_status:
            body["invoiceStatus"] = invoice_status

        t0 = time.time()
        status, raw = self._sign_and_send("POST", "/api/collect/syncInvoicesRealTime", body=body)
        elapsed = time.time() - t0
        logger.info(
            "syncInvoicesRealTime taxNo=%s dataType=%s [%s ~ %s] → status=%d (%.1fs)",
            tax_no,
            data_type,
            billing_date_start,
            billing_date_end,
            status,
            elapsed,
        )

        if status != 200:
            # NONE_DATA is a legitimate "no invoices in this window" answer, not an error
            if raw.get("code") == "NONE_DATA":
                logger.info(
                    "syncInvoicesRealTime taxNo=%s dataType=%s [%s ~ %s] → 空结果 (NONE_DATA)",
                    tax_no,
                    data_type,
                    billing_date_start,
                    billing_date_end,
                )
                return raw, {}
            raise FapiaoError(
                raw.get("code", "Unknown"),
                raw.get("message", ""),
                raw.get("requestId"),
            )

        b64 = raw.get("data")
        if not isinstance(b64, str):
            raise FapiaoError("NoData", f"expected base64 data field, got {type(b64).__name__}")

        try:
            decoded = json.loads(base64.b64decode(b64))
        except Exception as e:
            raise FapiaoError("DecodeFailed", f"failed to base64-decode response: {e}") from e

        return raw, decoded
