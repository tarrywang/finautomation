# Ubuntu Internet 部署指引

> 把发票仓库系统部署到一台公网 Ubuntu 22.04 服务器,使用 nginx + Let's Encrypt + Docker compose。

## 0. 前置准备

- Ubuntu 22.04 LTS (其他版本类似)
- 一个能解析到服务器 IP 的域名,例如 `fapiao.example.com`
- 开放云厂商安全组:**仅 22 (SSH) / 80 (HTTP) / 443 (HTTPS)**;**禁止暴露 5433/5432 给公网**

## 1. 系统加固(初次)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw fail2ban unattended-upgrades

# SSH:改端口、禁密码、禁 root
sudo nano /etc/ssh/sshd_config
# 至少改这几行:
#   Port 22022                  ← 改一个非标端口(把云厂商安全组也开 22022)
#   PermitRootLogin no
#   PasswordAuthentication no   ← 改前先确认你已经能用 key 登录
sudo systemctl restart ssh

# 防火墙
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22022/tcp  comment 'SSH'   # 用你 sshd_config 里的端口
sudo ufw allow 80/tcp     comment 'HTTP'
sudo ufw allow 443/tcp    comment 'HTTPS'
sudo ufw enable

# fail2ban (用默认配置足够,会自动盯 SSH 和 nginx)
sudo systemctl enable --now fail2ban

# 无人值守安全更新
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 2. 安装 Docker

```bash
# Docker 官方仓库
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 重新登录或 newgrp docker

docker compose version   # 验证 Compose v2
```

## 3. 安装 nginx + certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
```

## 4. 拉代码 + 配置

```bash
sudo mkdir -p /opt/fapiao
sudo chown $USER:$USER /opt/fapiao
cd /opt/fapiao
git clone <your-repo> .   # 或 rsync 上传

# 生成 SESSION_SECRET
python3 -c "import secrets; print(secrets.token_hex(32))"   # 复制输出

# 创建 .env(在仓库根目录)
cp .env.example .env
nano .env
# 必填:
#   PG_PASSWORD=<强随机>
#   FAPIAO_CLIENT_ID=JSFpcLPrvr
#   FAPIAO_CLIENT_SECRET=<...>
#   SESSION_SECRET=<上面生成的 64-hex>
chmod 600 .env
```

## 5. 启动 Docker 服务

```bash
cd /opt/fapiao

# 起 postgres + app
docker compose -f compose.prod.yml up -d --build

# 等 healthy
docker compose -f compose.prod.yml ps

# 应用数据库迁移(app 启动时已经自动跑,但首次手动确认一遍)
docker compose -f compose.prod.yml exec app alembic upgrade head
```

## 6. 创建初始 admin 账号

```bash
docker compose -f compose.prod.yml exec app python scripts/create_admin.py \
  --username tarry --email tarry@example.com --display-name Tarry
# 按 prompt 输入强密码(≥10 位,含字母+数字)
```

## 7. nginx + HTTPS

```bash
# 把仓库里的 nginx 模板拷过去
sudo cp deploy/nginx/fapiao_rate_limit.conf       /etc/nginx/conf.d/
sudo cp deploy/nginx/fapiao_proxy_params.inc      /etc/nginx/conf.d/
sudo cp deploy/nginx/fapiao.conf                  /etc/nginx/sites-available/

# 把 fapiao.example.com 改成你的真实域名
sudo nano /etc/nginx/sites-available/fapiao.conf

# 先临时上线一个 HTTP-only 版本(让 certbot 能验证域名)
# 删掉/注释 fapiao.conf 里的 443 server 块,只留 80 块
sudo ln -s /etc/nginx/sites-available/fapiao.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# certbot 自动申请 + 写入 443 配置 + 设置自动续约
sudo certbot --nginx -d fapiao.example.com
# 选择 "Redirect HTTP to HTTPS"

# 续约会被 cron 自动跑,验证一下:
sudo certbot renew --dry-run
```

打开 `https://fapiao.example.com`,用 admin 账号登录。

## 8. 安装定时任务(每天同步 + 每天备份)

```bash
sudo mkdir -p /var/log/fapiao /var/backups/fapiao

sudo cp deploy/systemd/fapiao-sync-daily.* /etc/systemd/system/
sudo cp deploy/systemd/fapiao-backup.*    /etc/systemd/system/

sudo systemctl daemon-reload

# 同步 03:00 / 备份 04:00
sudo systemctl enable --now fapiao-sync-daily.timer fapiao-backup.timer

# 检查
systemctl list-timers | grep fapiao
sudo journalctl -u fapiao-sync-daily.service -n 50
```

## 9. 创建业务用户

通过网页 admin 后台:`https://fapiao.example.com/admin/users`

1. 新建 → 主管 charlie,先建账号
2. 公司授权 → 选公司 + 选权限(只查 / 可改)
3. 把账号密码安全地发给业务用户(初始密码必须现场改)

## 10. 日常运维

| 操作 | 命令 |
|---|---|
| 看日志 | `sudo journalctl -u fapiao-sync-daily.service -f` 或 `tail -F /var/log/nginx/fapiao.access.log` |
| 重启 app | `cd /opt/fapiao && docker compose -f compose.prod.yml restart app` |
| 更新代码 | `cd /opt/fapiao && git pull && docker compose -f compose.prod.yml up -d --build app` |
| 手动同步 | `docker compose -f compose.prod.yml exec app python scripts/sync_daily.py` |
| 手动备份 | `sudo systemctl start fapiao-backup.service` |
| 看活动会话 | `docker compose -f compose.prod.yml exec postgres psql -U fapiao -d fapiao -c "SELECT username, last_login_at, last_login_ip FROM users ORDER BY last_login_at DESC NULLS LAST;"` |
| 解锁某账号 | 用 admin 后台 → 编辑用户 → "🔓 解锁账号" 按钮 |
| 重置某用户密码 | 用 admin 后台 → 编辑用户 → 填新密码 → 勾"首次登录强制改密"→ 保存 |
| 紧急下线 | `docker compose -f compose.prod.yml stop app` |

## 11. 灾难恢复(从备份还原)

```bash
# 先停 app
docker compose -f compose.prod.yml stop app

# 清空 DB,导入
docker compose -f compose.prod.yml exec -T postgres psql -U fapiao -d fapiao -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose -f compose.prod.yml exec -T postgres pg_restore -U fapiao -d fapiao < /var/backups/fapiao/fapiao_20260601_0400.dump

# 跑迁移并启动
docker compose -f compose.prod.yml exec app alembic upgrade head
docker compose -f compose.prod.yml start app
```

## 12. 安全清单

- [ ] `.env` chmod 600,不入 git
- [ ] SSH 改非标端口、禁密码、禁 root
- [ ] ufw 只开 SSH/80/443
- [ ] Postgres `ports:` 在 compose.prod.yml 里**没有**(只内部网络)
- [ ] App 只绑 `127.0.0.1:8000`(让 nginx 反代)
- [ ] HTTPS 证书自动续约 cron 已经在跑
- [ ] WEB_PASSWORD 不再使用(用 users 表)
- [ ] admin 账号密码 ≥ 14 位强随机
- [ ] 备份在跑,且每周抽查一次能 restore 出来
- [ ] 服务器 IP 没出现在 GitHub / 文档里被泄露

---

## 故障排查

| 症状 | 排查 |
|---|---|
| 网页 502 | `docker compose -f compose.prod.yml ps`;`docker compose logs app --tail 50` |
| 502 + nginx 日志 `upstream timeout` | App 启动慢/卡死,看应用日志 |
| 502 + nginx 日志 `connect() failed` | App 容器挂了,重启;若端口冲突,改 `127.0.0.1:8000` 那行 |
| 登录后立刻又跳 /login | session cookie 没设 — 看是否漏了 `WEB_HTTPS=1`,或域名/scheme 配错 |
| 登录后页面 5xx | `docker compose logs app` 看 traceback |
| sync_daily.timer 一直不跑 | `systemctl status fapiao-sync-daily.timer`;`systemctl list-timers --all` |
| Postgres 占内存高 | 给 compose 加 `mem_limit: 1g` 或在 postgres.conf 里调 `shared_buffers` |
