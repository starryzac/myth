# MVP-304：统一 audit-verify 命令实测

2026-10-04。真实入口为 `cmd.exe /d /c make.cmd audit-verify`，不是直接调用 CLI 函数或 pytest。统一 runner 实际执行 `uv run --frozen python scripts/verify_audit_chain.py`，生成原 run_id、manifest 和 `01-uv.log`。DATABASE_URL 仅通过子进程环境传入；证据没有输出 DSN 或密码。应用源码、测试、合同继续冻结，两个 manifest 的 201 个源路径/hash 与 git revision 完全相同。

| 范围 | 实际 run_id | 退出码 | manifest successful | CLI 结果 |
|---|---|---:|---|---|
| 随机临时库正例 | `20261004T084307Z-d15f418b` | 0 | true | VALID，ALL_RETAINED_EPOCHS，3/3 个保留轮次全部 VALID |
| 已迁移正式库预期负例 | `20261004T084311Z-303ce2e7` | 1 | false | NOT_VERIFIED，0 个注册轮次；子结果 LEGACY_UNAUDITED / HEAD_MISSING |

## 临时库正例

通过现有 `app.db.testing.temporary_database()` 创建随机 `bf_test_e715fd8f60184fb4b1da988cae847bdd`，由 `require_test_database()` 的精确 generated-name guard 保护。只对该库迁移到 `0006_audit_chain`，实际 `seed_demo(engine)` 三次，即初始 seed 后两次真实 reset；金融版本 mvp-301-v6、summary seed-summary-v2，三次业务 summary 完全相同。统一核验实际看到 3 个轮次、5 个事件和 816 个永久原件版本，包含两轮封存归档。此正例证明统一命令和 retained-epoch/archive 链核验，不替代另外的真实业务执行工作流证据。

engine dispose 后现有 context 的 guarded `DROP DATABASE ... WITH (FORCE)` 成功返回，临时库清理完成。没有针对正式库执行 seed、reset 或迁移。

## 正式库预期负例

配置的正式 `bounded_funds` 已由 root 非重置迁移到 0006，本任务只查询。统一命令前后 audit_epochs、audit_events、audit_subject_snapshots 都为 0。原业务事实存在但没有原审计轮次，命令正确返回非零；没有创建 genesis、补造历史或将空轮次判成 VALID。

此 runner 的 `successful=false` 是刻意保留的真实负例结果，不能改成通过，也不能与临时库的正例混为“正式历史已完整审计”。

## 零写入与快照

两次命令均在独立的 PostgreSQL REPEATABLE READ / READ ONLY 全表快照之间执行；CLI 自己也实际返回 `isolation=repeatable read`、`read_only=true`。完整读取 23 张表（22 张业务/审计表加 alembic_version），比较每一行、全部列集合、每表 hash、行数及总体 hash。只有全部相等后才归档命令证据。

| 范围 | 命令前/后全表 SHA-256（相同） |
|---|---|
| 临时库 | `e748b4a32ced68e33a6e6ad5b43d20856c2f12eec2f95c6e5fb23105b53175b4` |
| 正式库 | `281b302813a75cda170f2b20195e9719283cef699300db34741cc7c4db09b85f` |

总体 hash 覆盖本次读取的全部 23 张表；它与 root 迁移证据只覆盖原 20 表/原字段的 hash 使用不同的取值范围，不能直接混比。完整前后快照保存在 ignored `.runtime/MVP-304-audit-target/{temporary-positive,formal-negative}-{before,after}.json`。本次没有启动 pytest、coverage 或另一个全量 check。

## 可复核文件

- [逐表与总体零写入核验、archive 文件 hash 和源路径集合](MVP-304-audit-target-verification.json)
- 临时正例：[原 manifest](MVP-304-audit-target-temporary-positive-manifest.json)、[原 01-uv.log](MVP-304-audit-target-temporary-positive-01-uv.log)、[完整 CLI JSON](MVP-304-audit-target-temporary-positive-cli.json)、[实际统一 stdout/stderr](MVP-304-audit-target-temporary-positive-output.txt)
- 正式预期负例：[原 manifest](MVP-304-audit-target-formal-negative-manifest.json)、[原 01-uv.log](MVP-304-audit-target-formal-negative-01-uv.log)、[完整 CLI JSON](MVP-304-audit-target-formal-negative-cli.json)、[实际统一 stdout/stderr](MVP-304-audit-target-formal-negative-output.txt)
- [证据 helper 实际完成输出](MVP-304-audit-target-command-evidence.txt)。helper 仅在 ignored `.runtime/verify_mvp304_audit_target.py`，应用与测试源码不变。
