"""Alembic env — loads our SQLAlchemy metadata + DB URL from project env.

Run from tax_auto/ via: `uv run alembic upgrade head`
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure tax_auto/ is on sys.path so we can import tax_auto.warehouse.*
_HERE = Path(__file__).resolve()
_TAX_AUTO_DIR = _HERE.parents[3]  # .../tax_auto/
if str(_TAX_AUTO_DIR) not in sys.path:
    sys.path.insert(0, str(_TAX_AUTO_DIR))

# Load .env from financeautomation/ (parent of tax_auto/)
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    _ENV_FILE = _TAX_AUTO_DIR.parent / ".env"
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
except ImportError:
    pass

from tax_auto.warehouse.models import Base  # noqa: E402
from tax_auto.warehouse.session import _build_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DB URL from env (overrides any value in alembic.ini)
config.set_main_option("sqlalchemy.url", _build_database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
