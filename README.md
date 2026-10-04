# 钱途有界 · BoundedFunds

面向青年的可审计分级自主资金 Agent。全部账户、产品与动作均为合成模拟。

当前完成18/92项：[MVP-401资金边界首页](docs/progress/MVP-401.md)已定向验收，真实Edge十阶段、资金/UNKNOWN与保留数据迁移通过。初版整体及完整版均未完成，目标持续ACTIVE；下一项MVP-402策略中心与目标页。全量留初版与完整版两个验收节点。见[开发状态](docs/progress/STATUS.md)与[92项追踪](docs/spec/requirements-traceability.md)。

正式库为0006_audit_chain（22业务表），金融seed mvp-301-v6。非重置迁移保留原20表原字段全部数据，新审计表为空，正式旧历史保持LEGACY_UNAUDITED；隔离临时库审计正向结果不代表正式全史VALID。`make seed`导入60天事实、129模拟流水、3账单、T0/T1/30天定存及7独立银行开户记录；成功reset业务摘要v2保持固定、审计历史增长。当前事实查询见[账户API](docs/architecture/account-facts-api.md)。

策略已支持明确确认、追加版本、暂停、撤销、到期及旧动作失效；[策略 API](docs/architecture/policy-api.md)。`make policy-refresh` 可落库刷新时间状态，授权检查不依赖刷新是否执行。历史模式发现和离线自然语言编译仅生成可复核候选，默认关闭外部 LLM。

[生活准备金估算](docs/architecture/living-reserve-api.md) 已实现：验证完整历史与分类证据后，按重叠窗口的精确分位数返回建议；历史不足或来源冲突时不给精确建议。估算全程只读，确认前不产生策略权限。

90日现金边界、新增收入的目标月储备规划及[单产品资产配置预览](docs/architecture/asset-allocation-api.md)已完成对应任务验收。配置预览比较明确退出方案和净模拟收益，并保留目标归属、历史占用与在途预留。种子含三类产品的v1/v2共六条目录记录，旧持仓仍关联原版本；这些预览不产生资金动作。

[安全恢复](docs/architecture/recovery-api.md)已通过MVP-205验收：整仓无损赎回、独立模拟银行到账、对账及回执。T1在途仍保留实际缺口，有损提前支取生成待明确确认的提案。[统一执行器](docs/architecture/execution-api.md)已实现五类动作及有损确认后的执行，已通过MVP-301验收；业务界面在后续任务开发。

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

按 2026-10-04 用户指令，全量验收集中在初版完成和完整版完成两个节点；失败修复后可以在同一节点重跑。开发中验证改动模块及直接相关测试，资金守恒、防重复扣款、UNKNOWN 恢复和迁移运行对应集成，前端运行类型检查与相关页面验证。任务的定向通过与版本的全量通过分别记录。

`make types` 从实际 API 同步 OpenAPI 和前端类型；`typecheck` 拒绝过期的合同。`python scripts/verify_quality_gates.py` 与 `python scripts/verify_contract_gate.py` 可复验负向检查。

每次统一命令打印 `run_id`，原始子命令日志与退出状态保存在 `.runtime/quality/<run_id>/`。`test`/`integration` 会启动本项目 PostgreSQL；迁移集成测试只创建和销毁随机 `bf_test_*` 数据库，不回滚演示库。

`seed`/`demo-reset` 会启动本项目数据库、升级到当前迁移并事务性重置专属合成演示用户，保留其他用户及原审计历史；完整竞赛演示待 MVP-404。`audit-verify` 已接只读核验脚本，缺历史、无事件或不完整时非零退出，说明见[审计合同](docs/architecture/audit-chain-api.md)。`export-evidence`、`security-check`、`evidence-check`、`build-proposal` 的命令入口预留，关联任务实现前明确失败，不能用于宣称初版完成。

首次建立锁文件由维护者运行 `uv sync` 和 `pnpm install`；常规 bootstrap 使用 frozen 锁文件，避免安装时漂移。`.env` 只包含本地模拟配置，不覆盖已存在环境文件。

## 原则

未来未到账收入不计入当前自主资金。LLM 仅生成候选策略，用户确认后才形成权限。金额使用整数分，策略版本与审计记录必须可追溯。不得连接真实银行或真实资金接口。
