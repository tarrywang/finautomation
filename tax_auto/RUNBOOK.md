# RUNBOOK · tax_auto 运维手册

> 给"几个月后忘了细节的自己 + 接手的同事"用。每条都基于真实事故场景。

---

## 0. 心智模型

- **单点故障源**:登录态(cookies)、selector(对应电子税务局当前版本)、Anthropic API、SMTP
- **绝大多数运维问题** = 二者之一:`session 过期` 或 `电子税务局改版`
- 出问题别慌:看 trace → 看 lessons.md → 看相关 ADR → 改完写 lessons

---

## 1. 日常使用

### 跑某个月的发票(全部客户)
```bash
uv run tax-auto run --month 2026-04
```

### 跑某个月的发票(单个客户)
```bash
uv run tax-auto run --month 2026-04 --tax-id 91310000XXXXXXXXXX
```

### 看本月运行情况
```bash
uv run tax-auto metrics
cat runtime/metrics/daily.json | jq '.[-7:]'   # 最近 7 天
```

### 回放某次失败
```bash
uv run tax-auto replay 01HXKMXXXXXXXXXX
# 自动打开 Playwright trace viewer
```

---

## 2. 故障处置

### 2.1 session 过期(最常见)

**症状**:邮件 `🔑 91310... session 即将过期` 或运行报 `SessionExpired`

**修复**(2 分钟):
```bash
uv run tax-auto login --tax-id 91310000XXXXXXXXXX
# Chrome 自动弹出 → 你完成账密 + 扫脸 → 脚本检测到登录成功
```

完成后 `tax-auto sessions` 验证状态变 `FRESH` 或 `VALID`。

### 2.2 二次扫脸触发

**症状**:邮件 `⚠️ 91310... 触发二次扫脸,等待人工`

**修复**:
1. 邮件里有截图,确认是真的扫脸弹窗(不是其他)
2. 直接走到 Mac mini 前,Chrome 窗口里完成扫脸
3. 脚本会在 180s 内自动检测并继续
4. 如果你不在现场,默认 180s 后流程 FAIL — 第二天重跑即可

**预防**:不要短时间内对同一客户做多次操作;请求间隔 >5 分钟。

### 2.3 电子税务局改版(selector 失效)

**症状**:trace 里看到某步骤 `SelectorMissError`,vision fallback 也失败(`HumanRequired`)

**诊断**:
```bash
uv run tax-auto replay <run_id>   # 在 trace viewer 看具体哪一步失败
```

**修复**:
1. 用 Chrome DevTools 手动跑一遍那一步,找到新的 selector
2. 改 `tax_auto/flow/selectors.py` 对应步骤的 list,新 selector 放第一位
3. 写 lessons.md 记录变更
4. 重跑验证

**注意**:不要直接删旧 selector,留作回退候选。

### 2.4 Anthropic API 出问题

**症状**:vision fallback 日志全是 `parse failed` 或 5xx

**临时方案**:
- 看 `runtime/traces/<run_id>/llm_calls.jsonl` 确认是 API 问题还是 prompt 问题
- API 问题:等几小时再跑(Anthropic 通常 1-2h 恢复)
- prompt 问题:看 raw_text,改 prompts.py

**永久方案**(数据敏感客户):
切到 GLM/DeepSeek。`tax-auto customer add --china-llm ...`(代码已留口,W2+ 实现)

### 2.5 ZIP 下载超时 / 文件数不对

**症状**:`ExportTimeout` 或 `DataIntegrityError`

**原因**:
- 税务局后台导出慢(高峰期)→ 调大 `TIMEOUT_EXPORT_POLL_S` 环境变量
- 上月发票超过单次导出上限(2000 张?具体看政策)→ 拆分日期范围跑

**修复**:
```bash
uv run tax-auto fetch run --tax-id ... --month 2026-04   # 重跑
# 或者拆周
uv run tax-auto fetch list --tax-id ... --from 2026-04-01 --to 2026-04-15
uv run tax-auto fetch list --tax-id ... --from 2026-04-16 --to 2026-04-30
```

---

## 3. 部署 / 调度

### 3.1 安装定时任务
```bash
./scripts/install_launchd.sh
# 默认:每月 5 号 18:00 跑上月发票
launchctl list | grep tax_auto   # 验证
```

### 3.2 重新安装(改时间或参数)
```bash
launchctl unload ~/Library/LaunchAgents/ai.tarry.tax_auto.monthly.plist
./scripts/install_launchd.sh
```

### 3.3 手动触发一次
```bash
launchctl start ai.tarry.tax_auto.monthly
tail -F runtime/logs/launchd.out.log
```

### 3.4 关闭定时任务
```bash
launchctl unload ~/Library/LaunchAgents/ai.tarry.tax_auto.monthly.plist
```

---

## 4. 新客户加入

```bash
# 1. 注册客户
uv run tax-auto customer add \
    --tax-id 91310000YYYYYYYYYY \
    --alias "悦舍餐饮" \
    --notify-level CRITICAL

# 2. 完成首次登录
uv run tax-auto login --tax-id 91310000YYYYYYYYYY

# 3. 干跑确认通路
uv run tax-auto fetch list --tax-id 91310000YYYYYYYYYY --from 2026-04-01 --to 2026-04-30

# 4. 正式跑一次
uv run tax-auto fetch run --tax-id 91310000YYYYYYYYYY --month 2026-04
```

---

## 5. 改密钥

```bash
# Anthropic key 轮换
security delete-generic-password -s tax_auto -a anthropic_api_key 2>/dev/null
security add-generic-password    -s tax_auto -a anthropic_api_key -w 'sk-ant-NEW'

# SMTP 密码
security delete-generic-password -s tax_auto -a smtp_password 2>/dev/null
security add-generic-password    -s tax_auto -a smtp_password -w 'NEW'

uv run tax-auto doctor   # 验证
```

---

## 6. 备份恢复

**关键资产**(必须备份):
- `runtime/metadata.db`(发票元数据,审计 3 年)
- `runtime/output/invoices/`(发票文件本体)

**不要备份**:
- `runtime/session/`(cookies,泄露 = 别人能用你身份)
- `.env`(明文配置,通过 Keychain 重建即可)

**建议**:Time Machine 设置排除 `session/` 目录。

```bash
# 备份命令
tar -czf tax_auto_backup_$(date +%Y%m%d).tar.gz \
    --exclude='runtime/session' \
    --exclude='runtime/logs' \
    runtime/
```

---

## 7. 升级 / 维护

### 7.1 升级 Playwright
```bash
uv lock --upgrade-package playwright
uv sync
uv run tax-auto doctor    # 验证 Chrome 仍可启动
```

### 7.2 升级所有依赖
```bash
uv lock --upgrade
uv sync
uv run pytest             # 跑全部单测
uv run tax-auto doctor
```

### 7.3 清理旧 trace
```bash
# 保留最近 30 天的 trace,清理更早的
find runtime/traces -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

---

## 8. 紧急退出场

如果一切都炸了,关闭一切:
```bash
launchctl unload ~/Library/LaunchAgents/ai.tarry.tax_auto.monthly.plist
pkill -f tax-auto
pkill -f "Google Chrome.*tax_auto"
```

然后看 `runtime/logs/tax_auto.log` 最后 100 行找根因。

---

## 9. 联系/求助

- **代码层 bug**: 写 ADR + lessons.md,然后改
- **政策层问题**(如电子税务局规则变了): 找会计师 / 税务师确认,不要瞎猜
- **Anthropic 账号问题**: https://console.anthropic.com/settings/billing
