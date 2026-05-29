"""发票通 connectivity probe — verifies HMAC signing + credentials against a live endpoint.

Algorithm pinned 2026-05-27 via empirical match against ivs.fapiao.com:
  - signature carried in header literally named `signature` (NOT `Authorization`)
  - canonicalizedResource uses the FULL URL with scheme: `https://ivs.fapiao.com/mars<path>?<sorted_query>`
  - stringToSign = METHOD\\n + Accept\\n + Content-MD5\\n + Content-Type\\n + Date\\n
                   + (each x-mars-*:value\\n sorted by key, lowercase) + canonical_resource
  - signature = base64(HMAC-SHA256(client_secret_utf8, string_to_sign_utf8))

Default endpoint is POST /api/collect/syncInvoicesRealTime (per PDF 4.1) — confirmed reachable.
Override with --endpoint and --method for other APIs.

Source spec:
  rawdata/API接入/1.api接入.pdf  (signing rules)
  rawdata/API接入/4.1.api发票实时归集接入.pdf  (endpoint + params)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from email.utils import formatdate
from urllib.parse import quote

import httpx

BASE_URL = "https://ivs.fapiao.com/mars"
API_VERSION = "20190618"


def build_canonical_string(
    method: str,
    full_url_no_query: str,
    headers: dict[str, str],
    query: dict[str, str],
) -> str:
    parts = [
        method.upper(),
        headers.get("Accept", ""),
        headers.get("Content-MD5", ""),
        headers.get("Content-Type", ""),
        headers.get("Date", ""),
    ]
    http_header_str = "\n".join(parts) + "\n"

    x_mars = {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-mars-")}
    custom_header_str = "".join(f"{k}:{v}\n" for k, v in sorted(x_mars.items()))

    resource = full_url_no_query
    if query:
        q_str = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in sorted(query.items()))
        resource = f"{full_url_no_query}?{q_str}"

    return http_header_str + custom_header_str + resource


def sign_request(client_secret: str, string_to_sign: str) -> str:
    digest = hmac.new(
        client_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def interpret(status: int, body_text: str) -> str:
    try:
        body = json.loads(body_text)
    except json.JSONDecodeError:
        return f"⚠️  non-JSON body (status={status}) — possible network/proxy issue"

    code = body.get("errorCode") or body.get("code") or body.get("error")
    msg = body.get("errorMsg") or body.get("message") or body.get("msg") or ""
    data = body.get("data") or body.get("rows") or body.get("List")

    if status == 200 and isinstance(data, str) and len(data) > 100 and not code:
        # syncInvoicesRealTime success: data is base64(json) blob
        try:
            decoded = json.loads(base64.b64decode(data))
            total_rows = sum(len(v) for v in decoded.values() if isinstance(v, list))
            sections = ", ".join(f"{k}={len(v)}" for k, v in decoded.items() if isinstance(v, list))
            return f"✅ SUCCESS — got base64-encoded data ({total_rows} rows total: {sections})"
        except Exception:
            return f"✅ likely SUCCESS — got base64 data of len {len(data)}, but decode failed"
    if status == 200 and isinstance(data, list) and data:
        return f"✅ SUCCESS — got {len(data)} invoice(s)"
    if status == 200 and (not data) and not code:
        return "✅ AUTH OK — empty result set"
    if code in ("NoDataFound", "InvoiceDataNoFound", "NONE_DATA"):
        return f"✅ AUTH OK — {code}: {msg}"
    if code == "ClientNoAuth":
        return f"⚠️  AUTH OK but binding missing — ClientNoAuth: {msg}"
    if code == "SignatureDoesNotMatch":
        return "❌ SIGNING WRONG — SignatureDoesNotMatch"
    if code == "UnmatchedApi":
        return f"⚠️  signing OK, endpoint not in your scope — UnmatchedApi: {msg}"
    if code == "SystemException" and "不存在" in msg and "请登记" in msg:
        return (
            "⚠️  signing + API OK, but taxNo is NOT in 发票通 registry at all — "
            "call #24 /api/dzsj/bindingAccount to create a fresh binding"
        )
    if code == "SystemException" and "未查询到您与该企业的关联关系" in msg:
        return (
            "⚠️  signing + API OK; 发票通 HAS a binding record for this taxNo, "
            "but the stored 办税人 credentials are wrong/stale — "
            "call #24 /api/dzsj/bindingAccount again with CURRENT 办税人 info to overwrite"
        )
    if code == "UnboundAuthCode":
        return (
            "ℹ️  UnboundAuthCode: 未绑定证书受理权限 — 注意: 中间号模式套餐里 onlineStatus 永远报这个,"
            "不代表真的不能用。直接跑 syncInvoicesRealTime 才是有效性的真实判断。"
        )
    if code in ("InvalidClientId", "ClientDisabled"):
        return f"❌ CREDENTIALS BAD — {code}: contact 发票通 商务"
    if code == "RequestTimeTooSkewed":
        return "❌ CLOCK SKEW — local clock off by >15min; `sudo sntp -sS time.apple.com`"
    return f"❓ UNEXPECTED — status={status}, code={code!r}, msg={msg!r}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", default=os.environ.get("FAPIAO_CLIENT_ID"))
    p.add_argument("--client-secret", default=os.environ.get("FAPIAO_CLIENT_SECRET"))
    p.add_argument("--tax-no", default=os.environ.get("FAPIAO_TAX_NO"))
    p.add_argument("--endpoint", default="/api/collect/syncInvoicesRealTime")
    p.add_argument("--method", default="POST", choices=("GET", "POST"))
    p.add_argument("--from-date", default="2026-04-01")
    p.add_argument("--to-date", default="2026-04-30")
    p.add_argument("--data-type", default="1", help='"1" 进项 / "2" 销项')
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument(
        "--params-json",
        default=None,
        help="override default param set; JSON dict, e.g. '{\"taxNo\":\"...\"}'",
    )
    args = p.parse_args()

    if not (args.client_id and args.client_secret and args.tax_no):
        p.error("client-id / client-secret / tax-no all required (CLI or env)")

    if args.params_json:
        params: dict[str, object] = json.loads(args.params_json)
        params.setdefault("taxNo", args.tax_no)
    else:
        params = {
            "taxNo": args.tax_no,
            "dataType": args.data_type,
            "startBillingDate": args.from_date,
            "endBillingDate": args.to_date,
            "pageSize": args.page_size,
        }
    if args.method == "GET":
        query = {k: str(v) for k, v in params.items()}
        body_bytes = b""
    else:
        query = {}
        body_bytes = json.dumps(params, ensure_ascii=False).encode("utf-8")

    content_md5 = (
        base64.b64encode(hashlib.md5(body_bytes).digest()).decode("ascii")
        if body_bytes else ""
    )
    headers = {
        "Accept": "",
        "Content-MD5": content_md5,
        "Content-Type": "application/json",
        "Date": formatdate(timeval=None, usegmt=True),
        "x-mars-api-version": API_VERSION,
        "x-mars-signature-nonce": str(uuid.uuid4()),
    }

    canonical_url = BASE_URL + args.endpoint
    string_to_sign = build_canonical_string(args.method, canonical_url, headers, query)
    signature = sign_request(args.client_secret, string_to_sign)
    headers["signature"] = f"mars {args.client_id}:{signature}"

    print("─── stringToSign ───")
    print(repr(string_to_sign))
    print()
    print(f"─── request: {args.method} {canonical_url} ───")
    if body_bytes:
        print(f"  body ({len(body_bytes)}B): {body_bytes.decode('utf-8')}")

    with httpx.Client(timeout=30.0) as client:
        if args.method == "GET":
            resp = client.get(canonical_url, headers=headers, params=query)
        else:
            resp = client.post(canonical_url, headers=headers, content=body_bytes)

    print(f"─── response · status={resp.status_code} ───")
    body = resp.text
    try:
        print(json.dumps(json.loads(body), ensure_ascii=False, indent=2)[:4000])
    except json.JSONDecodeError:
        print(body[:4000])

    print()
    print("RESULT:", interpret(resp.status_code, body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
