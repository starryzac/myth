# FULL-002：统一命令与 CI 接线差量

状态：**PENDING / COMMANDS_IMPLEMENTED / ACCEPTANCE_INCOMPLETE**。本包实现三个此前缺失的命令入口与实际 CI 配置，没有关闭 FULL-002，也没有运行初版或完整版全量验收。2026-10-05 UTC（本地 2026-10-06）的金融 PostgreSQL 验证由 Root 独占；本包没有连接数据库、启动浏览器、seed/reset、迁移、安装依赖或读取本地凭证。

来源：仓库根《钱途有界_完整开发计划_Codex执行版.md》112–126 行规定九项检查，1162–1166 行规定工程目录/命令/CI/接口边界，1746–1758 行规定最终命令和人工验收；追踪表 `docs/spec/requirements-traceability.md` 的 FULL-002 状态保持 PENDING。原文件和失败证据不改。

## 可运行入口与真实退出

GNU Make、Windows `make.cmd` 与 `python scripts/tasks.py TARGET` 使用同一入口。

| 命令 | 实际能力 | 尚未证明与退出约束 |
|---|---|---|
| `security-check` | 调用已安装 Ruff 的 S 规则，只扫描 `apps/api/app` 生产源码，明确排除 `apps/api/app/tests`；完整 JSON 告警原样输出 | 不是依赖漏洞扫描、渗透测试或银行风险集成。本次实际 106 条告警、exit 1；没有宽豁免或把断言当作已修复 |
| `evidence-check` | 将显式原请求交给现有 `export_evidence.py`，在新目录重新核源/原字节/hash/运行绑定和已实现的语义门 | 缺请求、非法 schema/path/hash 为 exit 2；缺内容/未验证为 exit 1。不能因 JSON 存在或写了 `PASSED` 成功；完整出口仍须外层实际退出封闭，且当前导出器不是 FULL 67 项验收器 |
| `build-proposal` | 将现有正文与技术附录的原字节复制到新目录，生成转义 HTML 和源/SHA 清单供审阅 | 当前源稿包含过期 W1 状态，未替换为当前成果。没有 PDF、页数、真人研究、实验主张、八图或人工内容审阅证明；合法审阅包 exit 1，路径/来源错误 exit 2 |
| `full-check` | 明示 Full 检查顺序：`security-check` → 原 `check` → `audit-verify` → `evidence-check`；任一子命令失败即停止 | 未运行本次完整命令。原 `check` 保持 MVP 的覆盖率/实测性质计数/原生浏览器/current-context 协议；不是凭配置完成九项验收 |

本次把完整版九项的组合入口登记为显式命令修订 `full-check`，保留既有 MVP `check`。原 `check` 中一次完整后端 pytest 包含 unit/property/integration，保留全后端 ≥85%、声明核心 ≥95%、真实有效性质样例 ≥1000 的原门；不会先跑完整后端再重复跑三套。Standalone 的 `unit`/`property`/`integration` 仍可独立调用。最终 Full 验收需要一次冻结源码的九项等效结果和原分母；本包未提供这些结果，原计划 `make check` 字面命名差异明确保留。

`tasks.py` 现在捕获 `CalledProcessError` 并原样返回子命令退出码，原运行日志/manifest 继续保留；如证据缺失的 child exit 2，外层同样 exit 2。原成功路径和正金额/授权/历史规则没有改变。任务源清单增加 `.github/workflows/`；现有通用 scoped/export 源清单没有借此重写，本次 CI 字节另以最终归档 SHA 绑定。

显式调用示例（路径必须换成已有真实、当前源绑定的原请求，不能使用占位文件）：

```powershell
.\make.cmd security-check
uv run --offline python scripts/evidence_check.py --manifest .runtime/EXPLICIT_RUN/request.json --output .runtime/evidence-check/NEW_UNIQUE_ID
$env:BOUNDEDFUNDS_EVIDENCE_REQUEST = (Resolve-Path .runtime/EXPLICIT_RUN/request.json).Path
.\make.cmd evidence-check
uv run --offline python scripts/build_proposal.py --output output/proposal-review/NEW_UNIQUE_ID
.\make.cmd full-check
```

`BOUNDEDFUNDS_PROPOSAL_OUTPUT` 可给 `build-proposal` 指定新目录；旧目录一律拒绝覆盖。`full-check` 的金融与原生步骤只能在正式验收节点、已审查隔离环境内由唯一 owner 调用。本包仅实际执行三个新增入口的安全/缺失输入/原稿出口，不执行这个全量示例。

## CI 的实际配置与边界

新增 `.github/workflows/quality.yml`。PR 的 Ubuntu job 实际顺序调用 lint、typecheck、生产安全扫描；没有 `continue-on-error`。人工选择的 unit/property/integration/e2e/security/audit/evidence/proposal/full-check 通过同一任务入口执行，依赖 static job 成功，失败停止后续。金融 job 使用明确的自托管 Windows 隔离标签与单一并发组，不取消在途金融验收。

当前没有部署该专用 worker，也没有 GitHub Actions 运行原件。配置本身不证明 CI PASS、native Edge/截图/录像/离线三链/渗透测试完成。worker 须由验收 owner 提供独立工作目录、真实 Edge/Docker/runtime、owned 测试库及精确原件/context；不能复用正式历史或用 hosted Linux 冒充 Windows 原生录制。现有 106 条生产安全告警会真实阻止后续 job。没有生成/安装任何凭证。

CI 的 Python 3.12、Node 24 和 pnpm 11.19.0 按当前项目锁和运行合同登记；依赖安装只写在 CI 中，本机未安装。模式参照 [uv 的官方 GitHub Actions 指南](https://docs.astral.sh/uv/guides/integration/github/)、[pnpm/action-setup](https://github.com/pnpm/action-setup)、[actions/setup-node](https://github.com/actions/setup-node)，Ruff 范围按 [官方设置合同](https://docs.astral.sh/ruff/settings/)明确排除。部分 actions 按官方当前版本标签引用；没有宣称离线 CI、依赖漏洞扫描或供应链验收。

## 本次原件与检查

- 六个 TOOL_ONLY 工具风险测试真实 PASS（0.49s）：`docs/progress/evidence/W2/full002-command-risk-final-scope-20261005T233511Z-1ff3ac14`。仅验证错误传播、精确传参、字节保全、转义与路径/覆盖拒绝，不是产品/金融效果。
- 五源 strict mypy、Ruff 和 format 均真实 exit 0：`full002-command-final-types-20261005T233512Z-99c33186`、`full002-command-final-static-20261005T233512Z-b1376d1c`、`full002-command-final-format-20261005T233900Z-04de7a3c`。
- 生产安全实际 FAILED：`full002-actual-production-security-scope-20261005T233513Z-97d63fd9`；S101 104 条、S110 2 条。后两条位于 `full_goal_release_execution.py:1340/1358`，需 owner 对真实异常处理语义进一步处置。本包未改冻结金融源。
- 缺证据实际 exit 2：`full002-actual-missing-evidence-original-exit-20261005T233512Z-5a4bd829`。只有 `status=PASSED` 的 TOOL_ONLY JSON 同样被原导出器实际拒绝 exit 2：`full002-actual-false-pass-json-rejected-20261005T233629Z-c197a023`。
- 原稿 HTML 真实输出、原字节稳定、exit 1：`full002-actual-proposal-review-command-20261005T233326Z-8666dad9`；产物在 `output/proposal-review/FULL002-actual-source-20261005T2333Z-e27a19b5/`，状态 INCOMPLETE。

上述命令目录均位于 `docs/progress/evidence/W2/`；各保留 native argv/exit/log 和源前后清单。最初 import/类型/格式失败、安全扫描排除规则错误及旧 outer exit 1 的原件保留，不能因最终工具检查通过改写为成功。修改前原字节分别归档在 `.runtime/FULL-002-command-wiring-before-20261005T232852Z/`、`.runtime/FULL-002-first-check-before-20261005T233220Z/`、`.runtime/FULL-002-original-security-scope-before-20261005T233427Z/`。

## 具体未覆盖

原九项冻结源全量、完整后端与前端金融集成、正式 CI 运行、production S 告警处置、依赖漏洞/渗透检查、完整 FULL 证据分组、当前企划正文/PDF页数/人工审阅、真人研究、24×5 实验与十四指标、必需图表、四分钟展示/备用三链均没有在本包完成。新 events/whitepaper 目录只登记实际接口和材料入口，不作为事件规格覆盖或白皮书成品。FULL-002 保持 PENDING；下一真实验收必须引用这些缺口的后续独立原件。
