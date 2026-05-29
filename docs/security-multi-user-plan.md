# 上线前的安全加固 + 多用户 + Dashboard 重做方案

> 目标:把现在的 macOS 本机单用户系统迁到 Ubuntu 公网服务器,加多用户 / 角色 / 权限 / 审计 / Dashboard 增强。

---

## 一、需求重述

| 需求 | 当前 | 目标 |
|---|---|---|
| 登录方式 | 单密码 (.env 里 `WEB_PASSWORD`) | 用户名 + 密码 (bcrypt 哈希) |
| 用户管理 | 无 | 三角色 + admin 后台 CRUD |
| 权限维度 | 全部用户看全部 | 按公司 (`tax_no`) 授权 |
| 权限粒度 | 二选一(登录 / 未登录) | 系统管理 / CRUD / 查看 三档 |
| 部署 | macOS 127.0.0.1 | Ubuntu Docker 公网域名 + HTTPS |
| Dashboard | 公司卡片 + 24h 新进 + 月柱状 | + 快搜 + 预设报表 + 销方排行 + 趋势 |

---

## 二、新增数据模型 (warehouse)

3 张新表,SQLAlchemy ORM + alembic 迁移:

### 1. `users` — 用户

```sql
CREATE TABLE users (
  id                  BIGSERIAL PRIMARY KEY,
  username            VARCHAR(50) UNIQUE NOT NULL,    -- 登录名
  password_hash       TEXT NOT NULL,                  -- bcrypt(12 rounds)
  display_name        VARCHAR(100),                   -- 显示名 (前台展示)
  email               VARCHAR(255),                   -- 可选,做密码重置/告警用
  role                VARCHAR(20) NOT NULL,           -- 'admin' | 'supervisor' | 'operator'
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  must_change_password BOOLEAN NOT NULL DEFAULT FALSE,-- 首次登录强制改密
  failed_login_count  INTEGER NOT NULL DEFAULT 0,
  locked_until        TIMESTAMPTZ,                    -- 临时锁定截止时间
  last_login_at       TIMESTAMPTZ,
  last_login_ip       INET,
  password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by          BIGINT REFERENCES users(id)
);

CREATE INDEX ix_users_role ON users(role);
CREATE INDEX ix_users_active ON users(is_active);
```

### 2. `user_company_access` — 用户对哪家公司有什么粒度的权限

```sql
CREATE TABLE user_company_access (
  user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
  tax_no      VARCHAR(20) REFERENCES companies(tax_no) ON DELETE CASCADE,
  permission  VARCHAR(10) NOT NULL,         -- 'view' | 'crud'
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  granted_by  BIGINT REFERENCES users(id),
  PRIMARY KEY (user_id, tax_no)
);
```

**Admin 不需要在这张表里有记录** —— 应用层默认 admin = 全部公司全部权限。
查权限的 SQL helper 自动处理:
```sql
-- 用户能看的所有公司
SELECT DISTINCT c.tax_no
FROM companies c
WHERE c.is_self = TRUE AND (
  (SELECT role FROM users WHERE id = :uid) = 'admin'
  OR EXISTS (SELECT 1 FROM user_company_access WHERE user_id = :uid AND tax_no = c.tax_no)
);
```

### 3. `audit_log` — 审计日志

```sql
CREATE TABLE audit_log (
  id           BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  user_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
  username     VARCHAR(50),                  -- 冗余,user 删了也能溯源
  action       VARCHAR(50) NOT NULL,         -- 'login_success' | 'login_fail' | 'user_create' | 'sync_trigger' | 'invoice_delete' ...
  target_type  VARCHAR(30),                  -- 'user' | 'company' | 'invoice' | 'system'
  target_id    VARCHAR(50),                  -- 被操作对象的主键 (string 通吃 int/uuid)
  ip           INET,
  user_agent   TEXT,
  details      JSONB,                         -- 操作前后差异、错误信息等
  request_id   UUID                           -- 一次 HTTP 请求贯穿日志
);

CREATE INDEX ix_audit_ts ON audit_log(ts DESC);
CREATE INDEX ix_audit_user ON audit_log(user_id, ts DESC);
CREATE INDEX ix_audit_action ON audit_log(action);
```

### 可选第 4 张表 `password_reset_tokens` (如果做"忘记密码"功能)

```sql
CREATE TABLE password_reset_tokens (
  token_hash    TEXT PRIMARY KEY,             -- SHA-256(token); 原 token 只发给用户邮箱
  user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at    TIMESTAMPTZ NOT NULL,
  used_at       TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 三、权限矩阵 (Role × Resource × Action)

完整列出每个 URL/动作的权限要求:

| URL / 动作 | admin | supervisor | operator | 说明 |
|---|---|---|---|---|
| `GET /login` | ✓ | ✓ | ✓ | 公开 |
| `POST /login` | ✓ | ✓ | ✓ | 公开 |
| `GET /logout` | ✓ | ✓ | ✓ | 已登录 |
| `GET /profile` (自己) | ✓ | ✓ | ✓ | 改自己密码、看自己权限 |
| `POST /profile/password` | ✓ | ✓ | ✓ | 自助改密 |
| `GET /` 总览 | 全数据 | 仅授权公司 | 仅授权公司 | 数据自动按 scope 过滤 |
| `GET /invoices` 列表 | 全 | 授权公司 | 授权公司 | 后端强制注入 `WHERE tax_no IN (user scope)` |
| `GET /invoices/{id}` 详情 | 全 | 授权 | 授权 | 404 而不是 403 (防资源枚举) |
| `GET /invoices/export.csv` | 全 | 授权 | 授权 | scope 同上 |
| `POST /invoices/{id}/notes` (改备注) | 全 | crud-授权公司 | ✗ | operator 只读 |
| `DELETE /invoices/{id}` | 全 | crud-授权公司 | ✗ | 物理删除(同步会重新拉回,要慎) |
| `POST /sync/trigger` 立即同步 | 全 | crud-授权公司 | ✗ | 触发 syncInvoicesRealTime |
| `GET /sync/runs` 同步历史 | 全 | 授权公司 | 授权公司 | 看自己公司的运行记录 |
| `GET /admin/users` 列表 | ✓ | ✗ | ✗ | 系统管理 |
| `POST /admin/users` 新增 | ✓ | ✗ | ✗ | 创建用户 |
| `PATCH /admin/users/{id}` 改 | ✓ | ✗ | ✗ | 改角色 / 启停 / 重置密码 |
| `GET /admin/users/{id}/scope` | ✓ | ✗ | ✗ | 看某用户的公司授权 |
| `POST /admin/users/{id}/scope` | ✓ | ✗ | ✗ | 加授权 |
| `DELETE /admin/users/{id}/scope/{tax_no}` | ✓ | ✗ | ✗ | 撤授权 |
| `GET /admin/audit` 审计日志 | ✓ | ✗ | ✗ | 全部日志 |
| `GET /admin/companies` 公司主数据 | ✓ | ✗ | ✗ | 改 alias / is_self |
| `GET /admin/sync/config` | ✓ | ✗ | ✗ | 改 fapiao 凭证 (将来) |

**实现方法**:写两层装饰器/依赖:

```python
# 层 1: 角色守卫
def require_role(*roles: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles: raise HTTPException(403)
        return user
    return dep

# 层 2: scope 过滤 (注入 SQL where 子句)
def get_user_scope_taxnos(user: User, db: Session) -> set[str] | None:
    """返回用户能访问的 tax_no 集合;admin 返回 None 表示"全部"。"""
    if user.role == "admin":
        return None
    rows = db.execute(
        select(UserCompanyAccess.tax_no).where(UserCompanyAccess.user_id == user.id)
    ).scalars().all()
    return set(rows)

# 用法:
@router.get("/invoices")
def list_invoices(
    user: Annotated[User, Depends(require_role("admin", "supervisor", "operator"))],
    db: Session = Depends(get_db),
):
    scope = get_user_scope_taxnos(user, db)
    q = select(Invoice)
    if scope is not None:
        q = q.where(Invoice.tax_no.in_(scope))
    ...
```

---

## 四、认证与会话设计

### 登录流程

```
POST /login (form: username, password)
  ↓
  1. 限速: 同 IP 5分钟内最多 10 次 (in-memory 或 Postgres)
  2. SELECT user WHERE username = ? AND is_active = TRUE
  3. 若 user.locked_until > NOW() → 拒绝,显示"账户已锁,X 分钟后重试"
  4. bcrypt.verify(password, user.password_hash)
     ├─ 成功 → reset failed_login_count, set last_login_at/ip, session["uid"] = user.id, 302 to next
     └─ 失败 → failed_login_count += 1
         ├─ 若达 5 次 → locked_until = NOW() + 30min, audit_log("login_locked")
         └─ 302 /login?error=1
  5. audit_log("login_success" / "login_fail")
```

### 密码策略

- bcrypt 12 rounds (passlib[bcrypt])
- 长度 ≥ 10
- 必须含字母 + 数字 (至少)
- 不允许等于 username
- 首次登录必改密 (`must_change_password=True` 直接跳 `/profile/password`)
- 密码 90 天过期 → 提示改 (不强制 logout)

### Session

| 配置 | 值 |
|---|---|
| 实现 | starlette `SessionMiddleware` (signed cookie) |
| TTL | 8 小时 (`max_age=28800`) |
| Cookie 属性 | `HttpOnly` + `Secure` (HTTPS 下) + `SameSite=Lax` |
| 内容 | `{uid, login_ts}` (再多放别的就该用 server-side session 了) |
| Secret 轮换 | 每月 `SESSION_SECRET` 换 → 所有人重登 (可接受) |

### CSRF

- starlette SessionMiddleware 提供基础;额外加 CSRF token middleware
- 所有 POST/PATCH/DELETE 必须带 `csrf_token` (hidden form field 或 header)
- token = HMAC(session_id, secret); 服务端 compare_digest

实现:
- 使用 `starlette-csrf` 或自己写一个简单 middleware

### 用户名/密码哈希

```python
from passlib.context import CryptContext
pwd = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)
hash = pwd.hash(password)   # 写入 users.password_hash
ok   = pwd.verify(password, hash)
```

---

## 五、Dashboard 增强

### 当前 Dashboard

- 24 小时新进
- 公司卡片
- 月柱状

### 拟新增

| 功能 | 谁能看 | 实现位置 |
|---|---|---|
| **顶部快搜** 输入数电号码 / 销方关键词,Enter 直跳 invoices list | 所有 | dashboard 顶部 |
| **预设报表 chips**: `本月进项` `本月销项` `本周新增` `异常发票` `大额(≥¥5万)` | 所有 | dashboard,点 chip 跳 invoices list 带 query |
| **销方 Top 10** (按价税合计) | 所有(scope 内) | 卡片网格 + bar chart |
| **税率分布** (3% / 6% / 9% / 13% / 0%) | 所有 | 饼图 |
| **进项 / 销项 月度趋势线** (近 12 月) | 所有 | line chart |
| **同步健康度** (近 7 天的 sync_runs 成功 / 失败状态格) | admin + supervisor | heatmap-like 7 列 |
| **新进对手方** (近 7 天首次出现的销方) | 所有 | list |
| **审计日志最近 20 条** | admin | list |
| **我管理的公司** 一行说明用户当前 scope (admin 显示"全部") | 所有 | 顶部状态条 |
| **大额 / 状态异常 提醒** (作废 / 红冲 / 异常) | 所有 | 红色 badge |

### 路由

```
/                        ← 现有 dashboard + 上述新内容
/dashboard/report/{name} ← 预设报表跳转 (重定向到 invoices?...)
/admin/audit             ← 审计日志页 (仅 admin)
/admin/users             ← 用户管理页 (仅 admin)
/profile                 ← 个人设置 (改密)
```

---

## 六、公网部署安全加固清单

### 网络层

- [ ] **HTTPS 强制**:Caddy 自动签 Let's Encrypt;HTTP 自动 301 → HTTPS
- [ ] **HTTP/2 + TLS 1.2+** 才接;1.0/1.1 关掉
- [ ] **Postgres 不暴露公网**:Docker compose 不写 `ports:`,只用 `networks:` 让 app 内部访问
- [ ] **App 监听 127.0.0.1**,只能由 Caddy 反代访问
- [ ] **防火墙** (`ufw`):只开 22 (SSH)、80 (Caddy)、443 (Caddy);别的全 deny in
- [ ] **SSH** 用 key,禁 root 登录、禁密码登录、改 22→ 非标端口
- [ ] **fail2ban** 监视 SSH + Caddy access log,封 brute 攻击 IP

### 应用层

- [ ] **CSRF 保护** 所有写操作
- [ ] **Rate limit**: 登录 (5 失败/30 min 锁), 普通 API (100 req/min/user)
- [ ] **HTTP security headers** (in Caddy 或 FastAPI middleware):
  ```
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Content-Security-Policy: default-src 'self'; ...
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: ...
  ```
- [ ] **Session cookie**: HttpOnly + Secure + SameSite=Lax (或 Strict)
- [ ] **密码哈希** bcrypt 12 rounds (passlib)
- [ ] **审计日志**: 登录、用户增删改、权限授予/撤销、同步触发、发票删除
- [ ] **错误页**: 生产模式关 traceback 暴露
- [ ] **依赖审计**: 每周 `uv pip audit` (有问题升级)
- [ ] **secret 管理**: `.env` chmod 600 + 不入 git;长期换 Docker secrets 或外部 KMS

### 数据层

- [ ] **数据库密码** 强随机 (32+ char)
- [ ] **Postgres 备份**: 每天 03:00 `pg_dump -Fc`,保留 30 天;每周一份送到异地 (S3 / 阿里 OSS)
- [ ] **备份加密** (gpg / age) 后再传输
- [ ] **应用 DB 用户最小权限** (生产用 `fapiao_app` 只有 SELECT/INSERT/UPDATE/DELETE,不能 DROP)

### 运维

- [ ] **监控**: Caddy access log + app log → loki / journald
- [ ] **告警**: 同步失败 / 5xx 错率 > 1% / 磁盘 > 80% / 登录失败暴增
- [ ] **日志保留**: app 90 天,审计 365 天 (税务相关一般要存)
- [ ] **自动安全更新**: `unattended-upgrades` for Ubuntu base
- [ ] **CI / 部署**: 不直接 ssh 部署,用 `git pull` + `docker compose up -d` 单脚本

---

## 七、Ubuntu Docker 部署架构

```
                          Internet
                              │
                              ▼
                    ┌─────────────────────┐
                    │  Cloudflare (可选)   │  ← DDoS / WAF / DNS
                    └──────────┬──────────┘
                               │ 443 HTTPS
                               ▼
            ┌─────────────────────────────────────┐
            │      Ubuntu 22.04 LTS Server         │
            │                                       │
            │  ┌──────────────────────────────┐    │
            │  │ Caddy (host network)         │    │  ← Let's Encrypt 自动续证
            │  │  fapiao.example.com → :8000  │    │  ← reverse proxy + HTTPS
            │  └──────────────┬───────────────┘    │
            │                 │                     │
            │  ┌──────────────▼───────────────┐    │
            │  │ Docker compose · 内部 network │    │
            │  │  ┌────────────────────────┐   │    │
            │  │  │ fapiao-app             │   │    │
            │  │  │ uvicorn :8000 (internal)│  │    │
            │  │  │ 不映射 host 端口        │   │    │
            │  │  └─────────┬──────────────┘   │    │
            │  │            │                  │    │
            │  │  ┌─────────▼──────────────┐   │    │
            │  │  │ fapiao-postgres        │   │    │
            │  │  │ :5432 (仅 compose net) │   │    │
            │  │  │ volume: ./_data/pg     │   │    │
            │  │  └────────────────────────┘   │    │
            │  └──────────────────────────────┘    │
            │                                       │
            │  /etc/systemd/timers/                 │
            │   ├─ fapiao-sync-daily.timer (03:00) │
            │   ├─ fapiao-backup.timer    (04:00) │
            │   └─ fapiao-prune-log.timer (05:00) │
            └─────────────────────────────────────┘
```

### Docker compose 改造

```yaml
services:
  app:
    build: ./tax_auto
    container_name: fapiao-app
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
    networks: [internal]
    # 注意:不写 ports,只让 Caddy (host network) 通过 internal 网络访问

  postgres:
    image: postgres:16
    container_name: fapiao-postgres
    restart: unless-stopped
    environment: { ... }
    volumes:
      - ./_data/fapiao_pg:/var/lib/postgresql/data
    networks: [internal]
    # 也不写 ports!Postgres 永不暴露公网

  caddy:
    image: caddy:2
    container_name: fapiao-caddy
    restart: unless-stopped
    network_mode: host        # 直接用宿主机端口,避免 Docker NAT 麻烦
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile
      - ./_data/caddy:/data

networks:
  internal:
    driver: bridge
```

### Caddyfile

```
fapiao.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000   # 或 fapiao-app:8000 (取决于网络配置)

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
    }

    log {
        output file /var/log/caddy/fapiao.log
        format json
    }
}
```

### 同步定时任务从 launchd → systemd timer

`/etc/systemd/system/fapiao-sync-daily.service`:
```ini
[Service]
Type=oneshot
WorkingDirectory=/opt/fapiao
ExecStart=/usr/bin/docker compose exec -T app python scripts/sync_daily.py --days 14
StandardOutput=append:/var/log/fapiao/sync_daily.log
StandardError=append:/var/log/fapiao/sync_daily.err
```

`/etc/systemd/system/fapiao-sync-daily.timer`:
```ini
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

---

## 八、迁移路径(现状 → 多用户)

1. **alembic 加迁移** 建 `users` / `user_company_access` / `audit_log`
2. **写 bootstrap CLI**: `uv run python scripts/create_admin.py --username tarry --email ...`
   - prompt 密码 (getpass)
   - bcrypt 哈希
   - 写入 `users` 表 role=admin
   - 提示"WEB_PASSWORD 变量已废弃,可从 .env 删除"
3. **改 auth.py**:把 `_expected_password` 删了,换成 `verify_user(username, password) -> User | None`
4. **改全部 routes**:加 `Depends(get_current_user)` + 必要时 `Depends(require_role(...))`
5. **改 invoices route**:注入 scope WHERE 子句
6. **写 admin 路由** `/admin/users` `/admin/users/{id}/scope` `/admin/audit`
7. **更新 base.html**:nav 显示用户名,加 "我的" 入口
8. **测试 case**:
   - 创 1 个 supervisor 用户,授权璞妍五季 crud,**不**授权璞妍四季
   - 这个用户登录后,/invoices 只能看到五季的,看不到四季的
   - 直接访问 `/invoices/{four_id}` 应该 404 而不是 200
   - admin 用户看到全部

---

## 九、实施分期

### Phase 1 — 上线必需(必做,~3-4 天工作量)

- [ ] 数据模型 + 迁移: `users` / `user_company_access` / `audit_log`
- [ ] passlib bcrypt + create_admin.py
- [ ] 登录改用户名密码 + 失败计数 + 锁定
- [ ] 角色守卫 + scope 过滤注入
- [ ] CSRF middleware
- [ ] Caddy + HTTPS 反代
- [ ] Docker compose 改造 (Postgres 内部,App 不暴露)
- [ ] 个人改密页

### Phase 2 — 运营增强(2-3 天)

- [ ] Admin 后台: 用户增删改、角色调整、scope 授权
- [ ] 审计日志页 + 关键事件埋点
- [ ] Dashboard 新增 widget: 销方 Top 10 / 税率分布 / 同步健康度 / 趋势
- [ ] 预设报表 chips
- [ ] 顶部快搜
- [ ] systemd timer 替代 launchd

### Phase 3 — 高阶安全 + 监控(后续)

- [ ] 2FA (TOTP / Authy)
- [ ] 邮件:密码重置 / 异常登录告警
- [ ] Loki + Grafana 看板
- [ ] 自动备份到异地
- [ ] WAF (Cloudflare 或自建 ModSecurity)
- [ ] 依赖扫描 + 定期 patch

---

## 十、需要你确认的关键决策

1. **角色细节**
   - "主管 (supervisor)" 能不能管理本公司的 operator (即:能给业务员授权他自己有的公司)?**(推荐:不能,只 admin 能管理用户)**

2. **数据可删性**
   - 发票 / 同步记录是否允许 supervisor 物理删除? **(推荐:不允许,做"标记隐藏"软删,审计更友好)**

3. **首次部署用户怎么建**
   - A. SSH 上去跑 `create_admin.py` 命令行?
   - B. 第一次访问网页时自动进入"创建初始 admin"页?
   - **(推荐 A,纯命令行,避免初始化页被外人抢)**

4. **密码忘了怎么找回**
   - A. 不做,admin 后台直接重置
   - B. 做邮件重置(需要 SMTP 凭证)
   - **(推荐 A,简单到极致;有 SMTP 就一并做 B)**

5. **登录失败锁定**
   - 失败 5 次 → 锁 30 min 默认值是否合适? 或 5 次 → 永久锁(必须 admin 解锁)?

6. **会话 TTL**
   - 8 小时 (一个工作日)? 24 小时? 7 天?

7. **域名 & HTTPS**
   - 用 Cloudflare 还是直连? 用 Caddy 还是 nginx?
   - **(推荐:无 CDN,Caddy 自动 Let's Encrypt,最省心)**

8. **审计粒度**
   - 仅记重要事件 (登录、用户管理、删除)? 还是 INFO 级也记 (每次列表查询都记)?
   - **(推荐:重要事件;查询太频繁会污染日志)**

9. **Phase 1 上线时是否上 Dashboard 增强?**
   - 是 → 工期 +2 天
   - 否 → 先有用户/权限再迭代 Dashboard
   - **(推荐:Phase 1 不带 Dashboard 增强,先把权限上去再说)**

10. **是否需要 API key (除了网页登录之外)?**
    - 若以后 sync_daily.py 在容器内调内部 API,需要 service account
    - **(推荐:第一版不做,sync_daily.py 直接读 DB 不走 API)**
