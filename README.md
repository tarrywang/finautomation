# 发票仓库 (Fapiao Warehouse)

> 把发票通(`ivs.fapiao.com`)的进销项发票拉到本地 Postgres,通过网页端按时间 / 公司 / 多维度查询。
> macOS 单机部署,Docker 起 Postgres,Python 起 ETL + 网页,launchd 跑定时任务。

---

## 系统全貌

```
┌────────────────────────────────────────────────────────────────────────────┐
│  外部                                                                       │
│  ┌──────────────────────┐                                                   │
│  │ 发票通 (mars API)     │  ← HTTPS + HMAC-SHA256 签名                       │
│  │ ivs.fapiao.com       │                                                   │
│  └──────────┬───────────┘                                                   │
└─────────────┼───────────────────────────────────────────────────────────────┘
              │ syncInvoicesRealTime (POST,中间号模式)
              ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  本机 (Mac)                                                                 │
│                                                                             │
│  scripts/sync_daily.py  ─── launchd 每天 03:00 触发 (滚动 14 天)             │
│  scripts/sync_invoices.py  ─── 手动单次同步                                  │
│         │                                                                   │
│         │  解码 base64 → 解析 → upsert                                      │
│         ▼                                                                   │
│  ┌──────────────────────────────────────────────────────────┐               │
│  │ Postgres 16 (Docker · 127.0.0.1:5433)                    │               │
│  │   companies / sync_runs / raw_payloads /                  │               │
│  │   invoices / invoice_items                                │               │
│  └──────────────────────────────────────────────────────────┘               │
│         ▲                                                                   │
│         │ SQLAlchemy 2.0 ORM                                                │
│         │                                                                   │
│  ┌──────┴───────────────────────────────────────────────────┐               │
│  │ FastAPI + Jinja + HTMX + Tailwind CDN                    │               │
│  │ uvicorn  127.0.0.1:8000                                  │               │
│  │   / 总览 · /invoices 列表+9 维筛选 · /invoices/{id} 详情  │               │
│  │   登录页 /login · 退出 /logout                            │               │
│  └──────────────────────────────────────────────────────────┘               │
└────────────────────────────────────────────────────────────────────────────┘
```

**关键设计选择**

- 单一 client_id (`JSFpcLPrvr`) 覆盖多家自家公司(通过 `is_self=True` 标记)
- 中间号模式(不是软证书模式) —— 见 [`memory/fapiao-tong-zhongjianhao.md`](https://...)
- 同步通过 `(tax_no, data_type, sdfphm)` upsert,可重跑、可加密
- 全部跑 127.0.0.1,**外网访问不到** → 密码登录 + Postgres 绑 127.0.0.1
- `tax_auto` SQLite 操作元数据(原 Playwright 路径)和 `fapiao` Postgres 仓库**并存**互不干扰

---

## 第一次部署 / 新机器

### 前置

- macOS 13+
- Docker Desktop 已启动
- [uv](https://docs.astral.sh/uv/) 已装(`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 启动步骤

```bash
cd financeautomation

# 1. 准备配置
cp .env.example .env
# 编辑 .env:
#   - 生成 SESSION_SECRET (64 位 hex):
#     python3 -c "import secrets; print(secrets.token_hex(32))"
#   - 设置 WEB_PASSWORD
#   - 填 FAPIAO_CLIENT_ID / FAPIAO_CLIENT_SECRET
chmod 600 .env

# 2. 起 Postgres (后台跑)
docker compose up -d postgres
# 等 healthy:
docker inspect fapiao-postgres --format='{{.State.Health.Status}}'

# 3. 装 Python 依赖
cd tax_auto
uv sync

# 4. 建表 (alembic 迁移)
uv run alembic upgrade head

# 5. 验证 schema
docker exec fapiao-postgres psql -U fapiao -d fapiao -c "\dt"
# 应该看到: companies / invoices / invoice_items / raw_payloads / sync_runs / alembic_version

# 6. 首次手动同步一家公司 (做出 self_company 记录,后面 daily 才能识别)
uv run python scripts/sync_invoices.py --tax-no 91310101MACNGPBT27 --month 2026-04
uv run python scripts/sync_invoices.py --tax-no 91310101MA7GMP7N5X --month 2026-04

# 7. 起网页
uv run uvicorn tax_auto.web.main:app --host 127.0.0.1 --port 8000 --reload
# 浏览器开 http://127.0.0.1:8000 → 用 WEB_PASSWORD 登录

# 8. 安装定时任务 (每天 03:00 滚动拉 14 天)
bash scripts/install_fapiao_daily.sh
launchctl list | grep fapiao         # 验证已加载
```

---

## 日常操作

### 看发票

浏览器 `http://127.0.0.1:8000`,密码登录:

| 路径 | 干什么 |
|---|---|
| `/` | 总览 + 每家公司卡片 + 最近 24 小时新进发票 + 月份柱状图 |
| `/invoices` | 完整列表,左侧 9 维筛选(税号 / 进销 / 日期 / 品种 / 状态 / 对手方 / 金额 / 业务类型 / 抵扣) |
| `/invoices?since_hours=24` | 直达"最近 24 小时入库"快捷链接 |
| `/invoices/{id}` | 单张发票头 + 商品行 + raw JSON |
| `/invoices/export.csv?…` | 当前筛选导出 CSV |

### 手动重跑同步

```bash
cd financeautomation/tax_auto

# 单家公司单月
uv run python scripts/sync_invoices.py --tax-no <税号> --month 2026-05 --data-type 1

# 自定义日期(<= 30 天)
uv run python scripts/sync_invoices.py --tax-no <税号> --from 2026-05-01 --to 2026-05-15

# 销项
uv run python scripts/sync_invoices.py --tax-no <税号> --month 2026-05 --data-type 2

# 一次拉所有自家公司 × 进+销项 × 最近 14 天 (=launchd 跑的那个)
uv run python scripts/sync_daily.py
```

幂等的 —— 重跑只会更新已存在的发票字段、补新发票,不会重复。

### 看定时任务运行情况

```bash
# 最近的 launchd 日志
tail -F tax_auto/runtime/logs/sync_daily.out.log
tail -F tax_auto/runtime/logs/sync_daily.err.log

# 直接看 sync_runs 表
docker exec -it fapiao-postgres psql -U fapiao -d fapiao -c \
  "SELECT id, tax_no, data_type, period_start, period_end, status, invoice_count, started_at \
   FROM sync_runs ORDER BY started_at DESC LIMIT 20;"
```

### 立刻触发一次 daily 同步(不等 03:00)

```bash
launchctl start ai.tarry.fapiao.daily
```

### 临时停 / 改时间

```bash
launchctl unload ~/Library/LaunchAgents/ai.tarry.fapiao.daily.plist   # 停
# 改时间 → 编辑 tax_auto/scheduler/fapiao_daily.plist.template 里的 Hour/Minute,然后:
bash scripts/install_fapiao_daily.sh                                  # 重装
```

### 加新的自家公司

```bash
# 1. 先跑一次手动同步,会自动写入 companies 表并设 is_self=True
cd financeautomation/tax_auto
uv run python scripts/sync_invoices.py --tax-no <新税号> --month 2026-05 --data-type 1

# 2. 之后 sync_daily.py 会自动把它也带进每日轮询
```

### 网页改密码

```bash
nano /Users/tarrywang/Library/CloudStorage/OneDrive-*/financeautomation/.env
# 修改 WEB_PASSWORD
# 重启 uvicorn:
lsof -ti:8000 | xargs kill -9
cd financeautomation/tax_auto
uv run uvicorn tax_auto.web.main:app --host 127.0.0.1 --port 8000 --reload &
# 注意:改 SESSION_SECRET 会把所有人踢出去,但 WEB_PASSWORD 不会(已登录的 cookie 还有效)
```

---

## 配置 / 环境变量 (.env)

| 变量 | 例 | 说明 |
|---|---|---|
| `PG_PASSWORD` | `fapiao_dev_only` | Postgres 密码,compose.yml 用 |
| `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | 127.0.0.1 / 5433 / fapiao / fapiao | DB 连接信息 |
| `FAPIAO_CLIENT_ID` | `JSFpcLPrvr` | 发票通 client_id |
| `FAPIAO_CLIENT_SECRET` | `0ccddec…` | 发票通 client_secret |
| `WEB_HOST` / `WEB_PORT` | 127.0.0.1 / 8000 | 网页 |
| `WEB_PASSWORD` | (你定) | 登录密码 |
| `SESSION_SECRET` | (64 hex) | session cookie 签名密钥,丢/换 = 所有人重登 |

`.env` 一定 `chmod 600` 且加 `.gitignore`(已设)。

---

## 数据模型

| 表 | 主键 / 唯一键 | 干什么 |
|---|---|---|
| `companies` | `tax_no` | 自家公司 + 所有对手方,`is_self=True` 标记自家 |
| `sync_runs` | `id` (auto) | 每次同步调用记录,看 `status` / `invoice_count` / `request_id` |
| `raw_payloads` | `id` (auto) | 原始 base64 解码后 JSON 归档,以防字段映射漏 |
| `invoices` | `id` (auto); 唯一 `(tax_no, data_type, sdfphm)` | 发票头 |
| `invoice_items` | `id`; 唯一 `(invoice_id, row_no)` | 发票商品行(来自 XXHZB) |

字段映射详见 [`tax_auto/tax_auto/warehouse/models.py`](tax_auto/tax_auto/warehouse/models.py)。
所有 JSONB `raw_data` 列保留发票通原始字段,字段映射变了不用重拉。

字段命名沿用发票通 JSON 的拼音缩写(`SDFPHM` / `KPRQ` / `JSHJ` / …),方便对照原始响应。

### 加字段/改字段

```bash
cd tax_auto
# 1. 改 tax_auto/warehouse/models.py
# 2. 生成迁移
uv run alembic revision --autogenerate -m "add foo column"
# 3. 检查 versions/ 下新生成的迁移脚本对不对
# 4. apply
uv run alembic upgrade head
```

---

## 文件地图

```
financeautomation/
├── README.md                              ← 你正在看的文件
├── compose.yml                            ← Postgres 容器定义
├── .env                                   ← 配置 (gitignored, 600)
├── .env.example
├── _data/fapiao_pg/                       ← Postgres 数据卷 (gitignored)
├── rawdata/                               ← 发票通官方 PDF 文档
│   └── API接入/{1,2,4,4.1,7,15,24,25}.pdf
├── tax_auto/                              ← 主 Python 包 (uv 项目)
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── scripts/
│   │   ├── probe_fapiao.py                ← 烟雾测试 / 诊断单调
│   │   ├── bind_fapiao.py                 ← #24 bindingAccount 交互式调用
│   │   ├── sync_invoices.py               ← 单次手动同步 CLI
│   │   ├── sync_daily.py                  ← launchd 每天 03:00 调它
│   │   └── install_fapiao_daily.sh        ← 装/重装定时任务
│   ├── runtime/
│   │   └── logs/sync_daily.{out,err}.log  ← launchd 日志
│   └── tax_auto/
│       ├── fapiao/                        ← 发票通客户端
│       │   ├── sign.py                    ← HMAC-SHA256 签名算法
│       │   ├── client.py                  ← httpx 客户端
│       │   ├── parser.py                  ← base64 JSON → 扁平 invoices/items
│       │   └── ingest.py                  ← upsert 到 Postgres
│       ├── warehouse/                     ← Postgres 仓库
│       │   ├── models.py                  ← SQLAlchemy 2.0 ORM
│       │   ├── session.py                 ← engine + sessionmaker
│       │   └── migrations/                ← alembic 迁移
│       ├── web/                           ← FastAPI 网页
│       │   ├── main.py                    ← app 入口
│       │   ├── auth.py                    ← 登录/退出 + middleware
│       │   ├── deps.py                    ← get_db / templates
│       │   ├── routes/
│       │   │   ├── dashboard.py           ← /
│       │   │   └── invoices.py            ← /invoices 列表/详情/CSV
│       │   └── templates/
│       │       ├── base.html
│       │       ├── login.html
│       │       ├── dashboard.html
│       │       ├── invoices_list.html
│       │       └── invoice_detail.html
│       └── scheduler/
│           └── fapiao_daily.plist.template ← launchd 模板
```

---

## 安全模型

**威胁面:** 单用户内网工具,网络面 = 127.0.0.1 only。

| 层 | 措施 |
|---|---|
| **网络** | Postgres `127.0.0.1:5433` / uvicorn `127.0.0.1:8000`(`compose.yml` + uvicorn 参数限定) — 外网根本访问不到 |
| **凭证** | `.env` `chmod 600` + `.gitignore` |
| **网页** | 密码登录(`hmac.compare_digest` 常量时间) + signed session cookie(`SESSION_SECRET` 256-bit) + `SameSite=Lax` |
| **下游 API** | `client_secret` 仅出现在 `.env` + 内存,不打日志 |
| **重定向** | `?next=` 白名单(必须 `/` 开头且非 `//`),防钓鱼 |

**如果未来要暴露到公网,至少要补:**

1. HTTPS(反代到 nginx/caddy)
2. 多用户 + 角色权限
3. 限速登录 endpoint(防爆破)
4. `WEB_PASSWORD` 改 bcrypt/argon2 哈希存储
5. `WEB_PASSWORD` 改强随机 + 启动 MFA
6. Postgres 改成绑全网卡 + 强密码 + TLS

---

## 故障排查

### 网页不开 / 5xx

```bash
# uvicorn 还活着?
lsof -i:8000

# 看日志
tail -F /tmp/uvi.log    # 如果之前用 nohup/&重定向到这里

# 重启
lsof -ti:8000 | xargs kill -9
cd financeautomation/tax_auto
uv run uvicorn tax_auto.web.main:app --host 127.0.0.1 --port 8000 --reload &
```

### Postgres 连不上

```bash
docker compose ps          # 应当看到 fapiao-postgres healthy
docker compose logs postgres --tail=50
docker compose up -d postgres
```

### 同步报 `SystemException: 未查询到您与该企业的关联关系信息`

中间号在发票通侧还没绑定到电子税务局。问发票通后台。

### 同步报 `UnboundAuthCode`

`onlineStatus` 在中间号模式下永远报这个,**忽略**。直接看 `syncInvoicesRealTime` 是否能返回数据。

### 表单提交报 422 `date_from_datetime_parsing`

空字符串没被处理成 None。看 `tax_auto/web/routes/invoices.py` 的 `_opt_date` / `_opt_float`,4 个可选参数 (`kprq_from` / `kprq_to` / `jshj_min` / `jshj_max`) 必须用 `str | None` 而不是 `date | None` / `float | None`。

### 忘了密码

```bash
nano financeautomation/.env   # 改 WEB_PASSWORD
lsof -ti:8000 | xargs kill -9
cd financeautomation/tax_auto
uv run uvicorn tax_auto.web.main:app --host 127.0.0.1 --port 8000 --reload &
```

---

## 关键 lessons / 知识沉淀

参见 [`~/.claude/projects/-Users-tarrywang-…financeautomation/memory/`](.) :

- **`fapiao-tong-signing.md`** —— HMAC-SHA256 签名算法实测细节(`signature` header、canonical URL 含 scheme 等)
- **`fapiao-tong-zhongjianhao.md`** —— 上海中间号模式 3 步流程 + 字段集
- **`fapiao-tong-zhengshu-flow.md`** —— 早期误以为是软证书路径的分析(已弃)

---

## 备份

**必备份:** `_data/fapiao_pg/`(Postgres 数据卷)

```bash
# 备份
docker exec fapiao-postgres pg_dump -U fapiao -d fapiao -Fc \
  > backups/fapiao-$(date +%Y%m%d).dump

# 恢复
docker exec -i fapiao-postgres pg_restore -U fapiao -d fapiao --clean \
  < backups/fapiao-20260528.dump
```

**不要备份:**

- `.env`(明文凭证)
- `tax_auto/runtime/logs/`

建议把 backups/ 排除出 iCloud / Time Machine,或单独加密。

---

## License

Proprietary · TarryAI · 2026
