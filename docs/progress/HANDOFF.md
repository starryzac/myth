# 开发交接（持续更新）

目标：依据两份计划先完成初版，再推进完整版，直到所有功能、实验和交付物逐项验证。不得将工程骨架视为初版完成；当前目标 active。

## 当前里程碑

- M0（MVP-001—003）完成并通过每任务 make check，Git 已保存基础里程碑。
- MVP-101 完成，16 表、SQLAlchemy/Alembic、PostgreSQL 往返迁移及整体质量门通过。
- MVP-102 完成，60 天可重复种子和四个事实查询 API 已通过，实际演示库已导入。
- MVP-103 完成，策略生命周期、严格 DSL、不可变版本、引用和证据校验、旧动作失效及在途保护通过。
- MVP-104 完成，历史候选发现、幂等修订、失效、原始来源快照检查、确认隔离通过。
- 最小未完成任务：MVP-105，自然语言策略编译。当前完成源码已通过最终 check；提交号以 Git log 为准。后续按纯规则编译器、持久草稿/候选服务和 HTTP 分工推进，不跨任务改核心模型。
- 后续完整范围见 `docs/spec/requirements-traceability.md`（25 MVP + 67 FULL）与 `acceptance-checklist.md`。

## 环境和精确命令

工作目录：`F:\学校活动\工行杯\钱途有界\bounded-funds`。PowerShell 使用 `.\make.cmd <target>`；GNU Make 同样调用 `scripts/tasks.py`。

```powershell
Set-Location -LiteralPath 'F:\学校活动\工行杯\钱途有界\bounded-funds'
git status --short
.\make.cmd bootstrap
.\make.cmd dev
# 另一终端：
.\make.cmd check
```

PostgreSQL 16 Docker Compose 项目 bounded-funds，端口 54329，独立数据卷。API/Web 为 8000/5173，E2E 自启当前代码到 18000/15173，拒绝复用现有服务。浏览器默认系统 Edge。

启动 Docker Desktop 后已实际通过容器健康检查。沙盒内连接 Docker/依赖源或启动浏览器可能受限，需正常权限重试；不能把权限错误作为测试通过。

## 证据

`docs/progress/evidence/MVP-001-check.txt`、`MVP-002-check.txt`、`MVP-003-check.txt` 均保存实际 exit 0 质量门日志。MVP-002 两负向 lint 探针、MVP-003 两合同漂移探针通过。

统一命令现在会生成 `.runtime/quality/<run_id>/manifest.json` 与每个子命令日志；运行 ID 使用 UTC。首次 UTF-8 输出错误已修复，run `20261003T155404Z-8e3195e2` typecheck 成功。`.runtime` 非提交数据，若要作为正式证据应导出并记录。

## 未完成与边界

业务策略、资金边界、执行账本、恢复、审计、业务UI、冻结集/实验/材料均待实施。真人研究尚未开展；不得伪造记录。真实银行接入不在范围。禁止 LibreOffice。

临时内容：项目内 `.venv`、`.uv-cache`、`.pnpm-store`、node_modules、.runtime、测试报告和构建目录；Docker 独立卷用于项目模拟数据，未经核对不删除。此前 pytest 沙盒产生的 `pytest-cache-files-*` 权限目录已忽略，不影响代码；不做无关清理。

最新 check run `20261003T173729Z-a3c17e60`：271 后端、4 前端、1 Edge E2E，通过，运行前后源码 SHA256 一致。实际库已升级 0002；最近 seed run `20261003T170618Z-491fbb5c` 的 SHA256 与 MVP-102 相同，MVP-104 无种子变更。证据见 `docs/progress/evidence/MVP-104-*`。

继续前检查 Git、当前运行进程与数据库，不据本文声称服务仍运行。下一任务 MVP-105 自然语言编译，每任务保留红绿证据及真实限制并运行 make check。已接受迁移不得回改；结构演进新增修订。

## 后续必须延续的边界

- 五个 MVP 配置模板仅做结构校验；服务再核对同用户对象引用、事实内容哈希和确认绑定。未来 FULL 十二模板尚未实施。
- 0002 拒绝 PolicyVersion UPDATE；允许受控 demo DELETE。配置 digest 不是全版本密码学链，不应替代后续只追加审计。
- 生命周期模块当前语句覆盖率 91%，整体 97%；MVP-501 核心 ≥95% 与性质样本门仍待后续实测，不得提前宣称通过。
- UNKNOWN、SUBMITTED、FAILED 但最新回执未知/部分执行等，只保留并列需复核；未实现实际对账与恢复，不可改键重付。后续执行器须在事务中联合重验策略、资金、资产和回执。
- 确认/修改的幂等重放返回原始命令结果，不是实时状态。UI 以后须在命令后 GET 刷新当前状态；不可凭重放响应中的历史 ACTIVE 执行动作。
- 当前种子没有策略、目标或动作；MVP-104 应仅发现 PROPOSED（本种子预期房租与信用卡两个），确认前无持续授权。账单按到期月份识别账期，不按 statement_date 月份合并（9/1 与 9/30 是不同账期）。
