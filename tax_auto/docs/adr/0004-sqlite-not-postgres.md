# ADR-0004 · 数据层用 SQLite,不上 Postgres

- **Status**: Accepted
- **Date**: 2026-05-26
- **Deciders**: Tarry

## Context

需要持久化:客户/session 状态/任务/run/发票元数据/审计日志。可选:

A. **SQLite**(WAL 模式,单文件 `runtime/metadata.db`)
B. **Postgres**(本地 Docker 或托管 RDS)

## Decision

**v1 用 SQLite,直到一个或多个具体边界被突破再迁移。**

迁移触发条件(任一满足):
1. 客户数 > 30
2. 单日 run 数 > 100
3. 多机部署(Mac mini 主备)
4. 需要外部读(Web UI 跨进程并发写)

## Rationale

| 维度 | SQLite | Postgres |
|---|---|---|
| 部署 | 零配置 | 启 Docker / 配账号 / 配防火墙 |
| 备份 | 文件 cp | pg_dump + 调度 |
| 并发写 | 单进程足够(WAL 支持多读 1 写) | 真并发 |
| 工具链 | `sqlite3` CLI 现成 | psql + 学习 |
| 与 Mac mini 单机模型契合度 | ✓ 完美 | 过度 |

任务量画像:
- 3–10 客户
- 每客户每月 1–4 次任务
- 平均每月 < 50 runs 总量
- 单台 Mac mini 跑

**核心论点**:SQLite WAL 在我们当前任务规模下的写性能已经远超需求,
而 Postgres 带来的"真并发"对单进程串行 worker 没有价值。
过度工程化的代价是:第一周引入 SQL migration、第二周纠结连接池配置、
第三周维护 Docker compose——所有事情都不直接产生发票。

## Consequences

### 正面
- v1 部署只需要 `uv sync` + `tax-auto db init`,无外部服务依赖
- 备份就是 `cp runtime/metadata.db`,Time Machine 自动覆盖
- 单元测试每个用例可以 `tmp_path` 拿全新 DB,隔离性极好
- 整个 `storage/` 模块代码量小、可读性高

### 负面
- 真上量后必须迁移(注定的事,提前认知)
- 不能多进程同时写(目前架构本就单进程串行,不构成问题)
- SQLModel 写法跨 SQLite/Postgres 一致,迁移本身风险低

## Implementation

- [tax_auto/storage/db.py](../../tax_auto/storage/db.py) — engine + WAL pragmas
- [tax_auto/storage/models.py](../../tax_auto/storage/models.py) — 6 张表 SQLModel
- `INTEGER amount_cents`(见 ADR-0006)在 SQLite 上是 INTEGER affinity,迁
  Postgres 时改成 BIGINT 即可
- 迁移时机到了再引入 alembic,目前 `SQLModel.metadata.create_all` 够用

## References

- [SQLite WAL mode](https://www.sqlite.org/wal.html) — write 单实例不阻塞 reads
- 架构文档 §4.1
