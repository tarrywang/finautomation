# ADR-0005 · 同税号串行,跨税号最多 2 并发

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry

## Context

跑多个客户时,理论上可以全并发以加速。但电子税务局对**同一账号**的并发会话
有风控,在多设备同时操作时容易被踢下线 / 触发二次扫脸。

## Decision

**并发模型双层约束**:

1. **同一 `tax_id` 任何时刻只允许 1 个 worker 跑**
   - 通过 `sessions.status='LOCKED'`(DB 层)+ `fcntl.flock(...)` 文件锁(进程层)双保险
   - 第二个 worker 启动时立即抛 `SessionLocked`
2. **跨 `tax_id` 最多 2 个 worker 并发**
   - `settings.max_concurrent_workers` 默认 2,可调
   - 客户之间至少间隔 60s(`scheduler/scheduler.py` 的 polite delay)

## Rationale

| 选择 | 单 tax_id 串行 | 跨 tax_id 并发 |
|---|---|---|
| 风控规避 | ✓ 关键 | ✓ 间接 |
| 单机资源 | 不冲突 | Mac mini 跑 2 个 Chromium 完全 OK,4 个就吃力 |
| 行为像真人 | 是 | 慢慢做事,不像 bot |

**核心论点**:税务局对"自动化"的检测主要看**同账号的并发**和**操作节奏**,
而不是"我从 IP X 同时操作了 A 和 B 两个不同账号"——后者在代账行业很正常
(一个会计同时打开多个客户的电子税务局)。

## Consequences

### 正面
- 风控水位低:多客户跑不会互相拖累
- 单进程崩了不影响其他客户(每个 worker 独立 fcntl.lock)
- 失败客户进 30 分钟冷却(`cooldown_after_failure_min`),不重复触发风控

### 负面
- N 个客户串行,总耗时 ≥ N × 单次 ~3min(对小客户群没问题)
- 不能"周日深夜一次性跑完",得分散

## Implementation

- [tax_auto/core/session.py](../../tax_auto/core/session.py) `open_session()`
  双保险锁:`sessions.status='LOCKED'` + `fcntl.flock(LOCK_EX|LOCK_NB)`
- [tax_auto/scheduler/scheduler.py](../../tax_auto/scheduler/scheduler.py)
  `run_for_month()` 按客户串行迭代,中间 sleep 60s
- [tax_auto/config.py](../../tax_auto/config.py)
  `max_concurrent_workers: int = 2` · `cooldown_after_failure_min: int = 30`

## 未来当我们想要真正并发

到那一天(>20 客户),做法:
- 跨税号:用 `asyncio.gather` 或简单的 `multiprocessing.Pool(workers=2)`
- 同税号:绝对不放开,这是合规底线

## References

- 架构文档 §9
- HTML build plan §"不要并发"避坑条
