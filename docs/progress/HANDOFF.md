# 开发交接（持续更新）

目标：依据两份计划先完成初版，再推进完整版，直到所有功能、实验和交付物逐项验证。不得将工程骨架视为初版完成；当前目标 active。

## 当前里程碑

- M0（MVP-001—003）完成并通过每任务 make check，Git 已保存基础里程碑。
- MVP-101 完成，16 表、SQLAlchemy/Alembic、PostgreSQL 往返迁移及整体质量门通过。
- 最小未完成任务：MVP-102，60 天可重复模拟种子。
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

最新 check run `20261003T161343Z-a44e30c2`：17 后端、4 前端、1 Edge E2E，通过。演示数据库已在 migrate run `20261003T161446Z-7349ad65` 升级，尚无种子。清单已导出到 `docs/progress/evidence/MVP-101-*-manifest.json`。

继续前检查 Git、当前运行进程与数据库，不据本文声称服务仍运行。下一任务 MVP-102 种子数据，每任务保留红绿证据及真实限制并运行 make check。初始迁移已接受，不得回改；结构演进新增修订。
