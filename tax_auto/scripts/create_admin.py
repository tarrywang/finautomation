"""create_admin.py — bootstrap the first admin user. Run once after initial deploy.

Idempotent: refuses to create if an admin already exists (use --reset to force-update).
Usage:
    uv run python scripts/create_admin.py
    uv run python scripts/create_admin.py --username tarry --email tarry@example.com
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from dotenv import load_dotenv  # noqa: E402

_ENV = _HERE.parents[2] / ".env"
if _ENV.exists():
    load_dotenv(_ENV)

from sqlalchemy import select  # noqa: E402

from tax_auto.warehouse.models import User  # noqa: E402
from tax_auto.warehouse.session import get_sessionmaker  # noqa: E402
from tax_auto.web.security import (  # noqa: E402
    check_password_policy,
    hash_password,
    verify_password,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--username", help="管理员用户名 (不填则交互输入)")
    p.add_argument("--email", help="邮箱(可选)")
    p.add_argument("--display-name", help="显示名(可选)")
    p.add_argument(
        "--reset",
        action="store_true",
        help="如果该用户名存在,重置密码而不是报错",
    )
    args = p.parse_args()

    print("═" * 60)
    print("发票仓库 · 创建管理员账号")
    print("═" * 60)

    username = args.username or input("用户名: ").strip()
    if not username or len(username) < 3:
        print("✗ 用户名至少 3 个字符")
        return 1
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        print("✗ 用户名仅可包含字母/数字/下划线/连字符/点")
        return 1

    SessionLocal = get_sessionmaker()
    with SessionLocal() as s:
        existing_admin = (
            s.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
            .scalars()
            .first()
        )
        same_username = s.execute(select(User).where(User.username == username)).scalars().first()

        if same_username and not args.reset:
            print(f"✗ 用户名 {username!r} 已存在;加 --reset 重置该账号密码")
            return 1
        if existing_admin and not same_username and not args.reset:
            print(
                f"✗ 已经有 admin 账号: {existing_admin.username}\n"
                f"  这个脚本只用于初次部署。如需追加 admin,请用 admin 后台 /admin/users。\n"
                f"  非要从命令行加,加 --reset 强制。"
            )
            return 1

        # Prompt password (twice)
        while True:
            pw1 = getpass.getpass("密码 (至少 10 位,字母+数字): ")
            err = check_password_policy(pw1, username)
            if err:
                print(f"  ✗ {err}")
                continue
            pw2 = getpass.getpass("再输一遍: ")
            if pw1 != pw2:
                print("  ✗ 两次不一致,重来")
                continue
            break

        if same_username:
            same_username.password_hash = hash_password(pw1)
            same_username.role = "admin"
            same_username.is_active = True
            same_username.must_change_password = False
            same_username.failed_login_count = 0
            same_username.locked_until = None
            if args.email:
                same_username.email = args.email
            if args.display_name:
                same_username.display_name = args.display_name
            s.commit()
            print(f"\n✓ 已重置 admin {username!r} 的密码")
        else:
            u = User(
                username=username,
                password_hash=hash_password(pw1),
                role="admin",
                display_name=args.display_name or username,
                email=args.email,
                is_active=True,
                must_change_password=False,
            )
            s.add(u)
            s.commit()
            print(f"\n✓ 已创建 admin {username!r} (id={u.id})")

        # Sanity check the hash works
        assert verify_password(
            pw1, s.execute(select(User.password_hash).where(User.username == username)).scalar_one()
        ), "verify roundtrip failed"

    print("\n下一步: 启动 uvicorn,访问 /login 用刚才设置的用户名密码登录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
