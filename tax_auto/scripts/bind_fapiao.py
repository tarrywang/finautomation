"""发票通 #24 binding script — register/refresh 电子税局 credentials under our client_id.

POST /api/dzsj/bindingAccount

上海中间号模式 (2026-05-28 实测,字段集从 server stepwise BindingAccountFailed 错误反推):
  必填: taxNo / username(=中间号) / sjh(=中间号) / password / bsrzjhm / bsrlx / bsrmm
  推荐: bsrxm (PDF 要求,server 未强制)
  固定: loginType="1" 新版 / dlfs="0" 中间号或账密 / bdywlx="1" 进项

PROMPTS interactively for sensitive fields via getpass — never appear in argv.

Run:
    uv run python scripts/bind_fapiao.py \\
        --client-id JSFpcLPrvr \\
        --client-secret 0ccddec66317466098b3144922de2e0e \\
        --tax-no 91310101MACNGPBT27 \\
        --zhongjianhao 13241016542
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
import uuid
from email.utils import formatdate

import httpx

BASE_URL = "https://ivs.fapiao.com/mars"
API_VERSION = "20190618"
ENDPOINT = "/api/dzsj/bindingAccount"

BSRLX_OPTIONS = {
    "1": "财务负责人",
    "2": "法定代表人",
    "3": "办税人",
    "4": "购票员",
    "5": "普通管理员",
    "7": "开票员",
    "99": "其他",
}


def build_canonical(method, full_url, headers):
    parts = [
        method.upper(),
        headers.get("Accept", ""),
        headers.get("Content-MD5", ""),
        headers.get("Content-Type", ""),
        headers.get("Date", ""),
    ]
    custom = "".join(
        f"{k}:{v}\n"
        for k, v in sorted(
            {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-mars-")}.items()
        )
    )
    return "\n".join(parts) + "\n" + custom + full_url


def sign(secret: str, sts: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), sts.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")


def prompt_required(label: str, hidden: bool = False) -> str:
    while True:
        v = (getpass.getpass(f"{label}: ") if hidden else input(f"{label}: ")).strip()
        if v:
            return v
        print("  (不能为空)")


def prompt_bsrlx() -> str:
    print("\n办税人类型 (bsrlx):")
    for code, name in BSRLX_OPTIONS.items():
        print(f"  {code:>2}  {name}")
    while True:
        v = input("代码 (默认 2 法定代表人): ").strip() or "2"
        if v in BSRLX_OPTIONS:
            return v
        print(f"  无效;请选 {list(BSRLX_OPTIONS)}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", default=os.environ.get("FAPIAO_CLIENT_ID"))
    p.add_argument("--client-secret", default=os.environ.get("FAPIAO_CLIENT_SECRET"))
    p.add_argument("--tax-no", default=os.environ.get("FAPIAO_TAX_NO"))
    p.add_argument(
        "--zhongjianhao",
        "--zjh",
        default=os.environ.get("FAPIAO_ZHONGJIANHAO"),
        help="发票通分配的中间号 (用作 username 和 sjh)",
    )
    p.add_argument(
        "--same-password", action="store_true", help="电子税局密码 与 办税人密码 是同一个,只问一次"
    )
    args = p.parse_args()

    if not (args.client_id and args.client_secret and args.tax_no and args.zhongjianhao):
        p.error("client-id / client-secret / tax-no / zhongjianhao 全部必填")

    print("将为以下税号刷新发票通的中间号绑定:")
    print(f"  taxNo:        {args.tax_no}")
    print(f"  中间号:        {args.zhongjianhao}")
    print(f"  client_id:    {args.client_id}")
    print("  region/mode:  上海 / 中间号模式 (loginType=1 新版, dlfs=0, bdywlx=1 进项)")
    print()
    print("请准备好:")
    print("  · 电子税局登录密码 (password)")
    print("  · 办税人姓名 (bsrxm)")
    print("  · 办税人证件号(身份证 18 位) (bsrzjhm)")
    print("  · 办税人类型 (bsrlx)")
    print("  · 办税人密码 (bsrmm) — 如跟电子税局密码同一个,启动加 --same-password")
    print()
    if input("准备好了开始 [回车继续 / q 退出]: ").strip().lower() == "q":
        return 0

    bsrxm = prompt_required("办税人姓名 (bsrxm)")
    bsrzjhm = prompt_required("办税人身份证号 18位 (bsrzjhm)", hidden=True)
    bsrlx = prompt_bsrlx()
    password = prompt_required("电子税局登录密码 (password)", hidden=True)
    bsrmm = password if args.same_password else prompt_required("办税人密码 (bsrmm)", hidden=True)

    body = {
        "taxNo": args.tax_no,
        "username": args.zhongjianhao,
        "password": password,
        "bsrzjhm": bsrzjhm,
        "bsrlx": bsrlx,
        "bsrxm": bsrxm,
        "bsrmm": bsrmm,
        "sjh": args.zhongjianhao,
        "loginType": "1",
        "dlfs": "0",
        "bdywlx": "1",
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    headers = {
        "Accept": "",
        "Content-MD5": base64.b64encode(hashlib.md5(body_bytes).digest()).decode(),
        "Content-Type": "application/json",
        "Date": formatdate(usegmt=True),
        "x-mars-api-version": API_VERSION,
        "x-mars-signature-nonce": str(uuid.uuid4()),
    }
    full_url = BASE_URL + ENDPOINT
    sts = build_canonical("POST", full_url, headers)
    headers["signature"] = f"mars {args.client_id}:{sign(args.client_secret, sts)}"

    masked = {
        **body,
        "password": "***",
        "bsrmm": "***",
        "bsrzjhm": f"{bsrzjhm[:4]}***{bsrzjhm[-2:]}" if len(bsrzjhm) > 6 else "***",
    }
    print(f"\n─── POST {full_url} ───")
    print(f"  body (masked): {json.dumps(masked, ensure_ascii=False)}")

    with httpx.Client(timeout=30.0) as c:
        resp = c.post(full_url, headers=headers, content=body_bytes)

    print(f"─── response · status={resp.status_code} ───")
    text = resp.text
    try:
        parsed = json.loads(text)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(text[:4000])
        parsed = {}

    code = parsed.get("code") if isinstance(parsed, dict) else None
    msg = parsed.get("message", "") if isinstance(parsed, dict) else ""
    print()
    if resp.status_code == 200 and not code:
        print(f"RESULT: ✅ 绑定请求已接受 (requestId={parsed.get('requestId')})")
        print("        等 1-2 分钟后,跑 probe_fapiao.py 看 syncInvoicesRealTime 是不是真拉到发票")
    elif code == "BindingAccountFailed":
        print(f"RESULT: ❌ 绑定失败 — {msg}")
    else:
        print(f"RESULT: ❓ status={resp.status_code}, code={code!r}, msg={msg!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
