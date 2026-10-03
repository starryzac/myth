# 钱途有界 · BoundedFunds

面向青年的可审计分级自主资金 Agent。全部账户、产品与动作均为合成模拟。

当前 M0 工程基础和 M1 数据与策略已通过验收，正在实现 M2 资金算法；初版整体尚未完成。任务状态与验证证据见 [开发状态](docs/progress/STATUS.md)，完整范围见 [92 项需求追踪](docs/spec/requirements-traceability.md)。两份计划原文保存在本目录。

已完成 16 表迁移和 60 天演示事实，`make seed` 可重复导入 129 条模拟流水、3 张账单及 T0/T1/30 天定存产品。四个只读查询接口见 [账户事实 API](docs/architecture/account-facts-api.md)。

策略已支持明确确认、追加版本、暂停、撤销、到期及旧动作失效；[策略 API](docs/architecture/policy-api.md)。`make policy-refresh` 可落库刷新时间状态，授权检查不依赖刷新是否执行。历史模式发现和离线自然语言编译仅生成可复核候选，默认关闭外部 LLM。资金算法和业务界面按后续任务推进。

[生活准备金估算](docs/architecture/living-reserve-api.md) 已实现：验证完整历史与分类证据后，按重叠窗口的精确分位数返回建议；历史不足或来源冲突时不给精确建议。估算全程只读，确认前不产生策略权限。

## 环境与启动

Python 3.12、uv、Node.js 24、pnpm 11、Docker Desktop（Linux containers）、Microsoft Edge（真实浏览器测试）。本地 PostgreSQL 16 使用 54329 端口，API 8000，Web 5173。

```powershell
# Windows PowerShell，在本仓库目录执行
.\make.cmd bootstrap
.\make.cmd dev
```

Linux/macOS 使用 `make bootstrap`、`make dev`。两者调用同一个 `scripts/tasks.py`，也可直接 `python scripts/tasks.py <target>`。

打开 http://127.0.0.1:5173。API 健康检查 http://127.0.0.1:8000/api/v1/health。

## 质量命令

`lint`、`typecheck`、`unit`、`test`、`e2e`、`check` 对当前实现执行实际验证。M0 的 `check` 为 lint + typecheck + test + e2e；完整版本将按完整计划扩展为全部九项质量门。

`make types` 从实际 API 同步 OpenAPI 和前端类型；`typecheck` 拒绝过期的合同。`python scripts/verify_quality_gates.py` 与 `python scripts/verify_contract_gate.py` 可复验负向检查。

每次统一命令打印 `run_id`，原始子命令日志与退出状态保存在 `.runtime/quality/<run_id>/`。`test`/`integration` 会启动本项目 PostgreSQL；迁移集成测试只创建和销毁随机 `bf_test_*` 数据库，不回滚演示库。

`seed`/`demo-reset` 会启动本项目数据库、升级到当前迁移并事务性重置专属合成演示用户，保留其他用户；当前为 M1 种子重置，完整竞赛演示待 MVP-404。`export-evidence`、`audit-verify`、`security-check`、`evidence-check`、`build-proposal` 的命令入口预留，关联任务实现前明确失败，不能用于宣称初版完成。

首次建立锁文件由维护者运行 `uv sync` 和 `pnpm install`；常规 bootstrap 使用 frozen 锁文件，避免安装时漂移。`.env` 只包含本地模拟配置，不覆盖已存在环境文件。

## 原则

未来未到账收入不计入当前自主资金。LLM 仅生成候选策略，用户确认后才形成权限。金额使用整数分，策略版本与审计记录必须可追溯。不得连接真实银行或真实资金接口。
