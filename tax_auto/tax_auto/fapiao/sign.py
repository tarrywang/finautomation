"""HMAC-SHA256 canonical-string signing for 发票通 mars API.

Algorithm pinned 2026-05-27 via empirical match against ivs.fapiao.com:
- Header carrying auth: `signature` (NOT Authorization)
- canonicalizedResource: full URL with scheme `https://ivs.fapiao.com/mars<path>?<sorted_query>`
- stringToSign:
    METHOD\\n + Accept\\n + Content-MD5\\n + Content-Type\\n + Date\\n
    + (x-mars-<k>:<v>\\n sorted by k lowercased)
    + canonical_resource
- signature = base64(HMAC-SHA256(client_secret_utf8, stringToSign_utf8))

Reference: rawdata/API接入/1.api接入.pdf §三 签名机制
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote


def build_canonical_string(
    method: str,
    full_url_no_query: str,
    headers: dict[str, str],
    query: dict[str, str] | None = None,
) -> str:
    """Build the stringToSign per 发票通 spec.

    Args:
        method: HTTP method, e.g. "GET", "POST"
        full_url_no_query: full URL with scheme/host/path, no query string
            e.g. "https://ivs.fapiao.com/mars/api/collect/syncInvoicesRealTime"
        headers: HTTP headers; only Accept / Content-MD5 / Content-Type / Date
            and x-mars-* keys are read. Others ignored.
        query: optional GET query params; sorted by key, URL-encoded.
    """
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


def sign(client_secret: str, string_to_sign: str) -> str:
    """Compute base64(HMAC-SHA256(client_secret, stringToSign))."""
    digest = hmac.new(
        client_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_auth_value(client_id: str, signature: str) -> str:
    """Format the value for the `signature` header: 'mars <clientId>:<sig>'."""
    return f"mars {client_id}:{signature}"
