# ADR-0007 · 通知用邮件,不用飞书 webhook

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry
- **Supersedes**: 架构文档原 §12 的飞书 webhook 方案

## Context

原计划用飞书自定义机器人 webhook(`open.feishu.cn/.../bot/v2/hook/...`)发
INFO/WARN/CRITICAL 三级通知。用户决定弃用飞书,改用邮件,主要原因:

1. 没有现成的飞书租户/群,临时申请 + 配机器人增加引入成本
2. 已有阿里企业邮箱(`tarrywang@tarryai.com`),零额外配置
3. 邮件天然有归档,日后追溯问题更容易
4. 邮件能多端收(电脑/手机/iPad 都自动同步),不绑定单一 App

## Decision

**通知通道唯一用 SMTP 邮件。**

- 发件:阿里云企业邮箱 `smtp.qiye.aliyun.com:465` (SSL)
- 收件:同发件邮箱(自发自收)
- 密码:macOS Keychain (`security add-generic-password -s tax_auto -a smtp_password`)
- 三级语义(`INFO` / `WARN` / `CRITICAL`)保留,只是统统走 SMTP——
  CRITICAL 加 macOS 本地通知做"双保险"

## Rationale

| 维度 | 飞书 webhook | 邮件 SMTP |
|---|---|---|
| 初始配置 | 申请群 + 加机器人 + 配 webhook URL | 已有企业邮箱,零额外步骤 |
| 多端覆盖 | 飞书 App 装在哪算哪 | 全自动同步,任何邮件 App 可读 |
| 速达 | 秒级 | 5-30s(企业邮箱) |
| 富文本 | 卡片消息支持好 | 纯文本/HTML 都可 |
| 归档 / 检索 | 群消息容易刷掉 | 邮箱永久,关键词搜索 |
| 附件 | 飞书要走文件上传接口 | EmailMessage.add_attachment() 一行 |
| 限流 | 飞书机器人有 100次/分钟 | SMTP 自带,我们再加 sliding 1h |
| 集成成本 | 学一套 webhook + 卡片 schema | stdlib `smtplib` 现成 |

**核心论点**:速度差几秒在我们这种"每月跑一次"的场景里不痛不痒;
归档检索能力对未来追溯事故有长尾价值,邮件是更可持续的选择。

## Consequences

### 正面
- 零外部账号依赖:Anthropic key + 阿里企业邮箱即可
- 自发自收减少 NOTIFY_TO 配置;每个客户的通知都直送收件箱
- 附件天然支持:CRITICAL 邮件可以直接附 trace.zip 或截图
- 跨设备:tarrywang@tarryai.com 在 Mac / 手机 / iPad 同步

### 负面
- 速度比 webhook 慢几秒(可接受)
- 没有"@群里的人"功能(只发自己,不影响)
- 富格式弱(目前模板都是纯文本,影响不大)

### 中性
- 这是个保留可逆性的决策:`notify/email.py` 是单一通道,以后想加
  飞书/钉钉,在 `notify/templates.py` 之上叠 fan-out 即可,核心代码不动

## Implementation

- [tax_auto/notify/email.py](../../tax_auto/notify/email.py)
  `send_email()` 支持 SSL/STARTTLS、Keychain 密码、sliding 1h 限流、附件
- [tax_auto/notify/templates.py](../../tax_auto/notify/templates.py)
  纯文本模板:`face_verify_*` / `run_summary_*` / `session_expiring_*`
- [tax_auto/notify/macos.py](../../tax_auto/notify/macos.py) `notify()`
  仅 CRITICAL 用,osascript 弹本机通知
- [tax_auto/config.py](../../tax_auto/config.py) 字段:
  `smtp_host`, `smtp_port`, `smtp_user`, `smtp_use_ssl`,
  `notify_from`, `notify_to`, `notify_rate_limit_per_hour`,
  `smtp_password: SecretStr` (Keychain)
- [tests/unit/test_notify.py](../../tests/unit/test_notify.py)
  覆盖模板渲染 + 限流逻辑(8 个测试)

## References

- 用户决策对话(2026-05-26)
- 架构文档 §12(已同步更新)
- [tasks/lessons.md](../../tasks/lessons.md) Lark→Email 替换记录
