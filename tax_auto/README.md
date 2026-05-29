# tax_auto

Self-built invoice automation for 上海电子税务局.  
Playwright deterministic core + Claude vision fallback.

## Prerequisites

- **Google Chrome installed** (Playwright drives system Chrome via
  `channel="chrome"` — see [ADR-0002](docs/adr/0002-system-chrome-not-playwright-chromium.md))
- macOS 13+ (Linux/Windows untested for v1)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Quick start

```bash
# 1. Install (uv handles Python 3.12 + deps + venv)
uv sync

# 2. Verify environment (Chrome must be launchable)
uv run tax-auto doctor

# 3. Store Anthropic API key in macOS Keychain (one-time)
security add-generic-password -s tax_auto -a anthropic_api_key -w 'sk-ant-...'
security add-generic-password -s tax_auto -a lark_webhook_url   -w 'https://...'

# 4. Copy env template (non-secret tunables)
cp .env.example .env

# 5. Bootstrap DB + first customer login
uv run tax-auto db init
uv run tax-auto login --tax-id 91310000XXXXXXXXXX

# 6. Pull invoices
uv run tax-auto fetch list --tax-id 91310000XXXXXXXXXX --from 2026-04-01 --to 2026-04-30
uv run tax-auto fetch run  --tax-id 91310000XXXXXXXXXX --month 2026-04
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design, data model, state machines
- [tasks/todo.md](tasks/todo.md) — 6-week build roadmap
- [RUNBOOK.md](RUNBOOK.md) — operations: session refresh, version bumps, new customers

## License

Proprietary · TarryAI · 2026
