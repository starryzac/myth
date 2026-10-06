# MVP/FULL corpus 冻结工具合同 v1

状态：工具实现，正式 corpus 尚未冻结。`mvp-case-design.md` 的 24 项仍是设计槽位；本工具没有将它们补造成可执行输入、独立 oracle 或成功结果。工具只有 Python stdlib 文件/JSON/AST/算术检查，不运行金融服务、数据库、浏览器或真人研究。

入口为 `scripts/mvp_corpus.py`，支持 validate / freeze / verify。所有写入采用新输出或新目录；旧输入、旧 manifest、失败目录不覆盖。

```powershell
.venv/Scripts/python.exe scripts/mvp_corpus.py validate --input <draft.json> --source-root <实际仓库> --output <新的validation.json>
.venv/Scripts/python.exe scripts/mvp_corpus.py freeze --input <draft.json> --source-root <实际仓库> --output <不存在的新版本目录>
.venv/Scripts/python.exe scripts/mvp_corpus.py verify --input <已冻结目录> --source-root <实际仓库> --manifest-sha256 <外部登记的manifest原digest> --output <新的verification.json>
```

验证不从待验证 manifest 自己取得可信 digest。冻结返回实际 manifest SHA；调用方应将它登记到独立的实验/run 来源清单，后续 verify 必须提供该原 digest。全部输出固定 `financial_effect_evidence=false`；冻结和原件验证都不能关闭 MVP-503，也不证明任何金融案例成功或独立 oracle 的语义正确。

## 用途与配额

- DEVELOPMENT：原开发输入及其库存；不得采用正式 quota profile。
- MVP_FROZEN：profile 必须 MVP，严格 24 项；家族/数量如下。
- FULL_FAMILY_FROZEN：profile 必须 FULL，实际 family_schema_ref 的 required_counts 决定全部族配额，不能套初版 24 槽位冒充完整版。
- TOOL_ONLY：纯解析/文件/整数工具夹具；不能变成 MVP/FULL 输入或效果证据。测试仅冻结明确标记的工具夹具目录，未创建 24 个正式假案例。

| 原家族 | family_id | 数量 |
| --- | --- | --- |
| 正常资金充足 | NORMAL | 6 |
| 目标/义务冲突 | GOAL_OBLIGATION_CONFLICT | 6 |
| 大额消费收缩 | CONSUMPTION_SHRINK | 4 |
| 定存/流动性冲突 | FIXED_LIQUIDITY_CONFLICT | 4 |
| 策略到期/换租 | POLICY_VERSION | 2 |
| 即时转账歧义 | TRANSFER_AMBIGUITY | 2 |

缺项、多项、错族或缺正式原件是 INVALID，不产生冻结目录。其原因保留在新 validation report 中；后续改案用新报告/新版本，不改失败。

## Draft 原件与明确绑定

draft.json 的 protocol 为 bounded-funds-corpus-draft-v1，含 purpose/profile、schema_ref、seed_ref、source_ref、design_ref、development_inventory_ref、cases；FULL 另有 family_schema_ref。每个 ref 为 `{path,sha256}`，路径相对 draft 父目录，不接受绝对路径、目录逃逸或缺原件。digest 核实际 bytes；严格 UTF-8 JSON 拒绝重复 keys 和非有限数值。

schema / seed / source / design 原件均须与目标 purpose 相同。SOURCE.files 为相对于显式 source-root 的真实源码 `{path,sha256}` 清单；每份原件直接核当前 bytes，而不是只核 digest 字符串。相同物理源文件别名重复登记会被拒绝。

正式 MVP/FULL 的 SOURCE 必须覆盖全部生产 `apps/api/app/**/*.py`（排除 tests 和缓存）、全部 `apps/api/alembic/**/*.py`、pyproject.toml、uv.lock、alembic.ini，以及已登记 oracle/arm adapter 源码。verify 同样检查新增生产源文件，防止只对旧子集报 source-stable。开发和工具夹具只核它们明确登记的 source scope，不声称覆盖正式生产源码。

- SEED：seed_version=mvp-301-v6、seed_source_sha256、非空 initial_fact_refs。初态事实原件全部复制并核字节；它们是实际捕获原件的输入，不是工具生成的期望余额。
- SCHEMA：contract_status=SUPPORTED_INPUTS_REGISTERED、contract_source_sha256、initial_state_schema、step_schemas、faults。正式用途另有 contract_source_path、runner_source_path、runner_source_sha256。工具从实际源码 AST 取 ScenarioStep.kind literal 与 ScenarioRunner._dispatch 分支的交集；注册不能加入尚无实际 handler 的 RUN_RECOVERY/CHANGE_POLICY 等步骤。
- 独立 schema 子集仅支持 type/required/properties/additionalProperties=false/items/minItems/maxItems/enum/const/minimum/maximum/minLength/maxLength；未知 schema keywords 明确拒绝，不能静默忽略。
- DEVELOPMENT inventory：purpose=DEVELOPMENT、inventory_status=REGISTERED_COMPLETE、非空 inputs 原件。排除比较范围是这份真实登记库存；提供空列表或缺开发原件不能形成“无复用”证明。
- Case entry：case_id、family_id、input_ref、oracle_ref、rule_refs（严格 B0/B1/B2/B3/P 五组）。case_id 唯一并与输入原件一致。

## 输入、独立 oracle 和五组规则

每个 INPUT 原件含 protocol=bounded-funds-case-input-v1、case_id、family_id、intended_purpose、seed_version、seed_sha256、source_sha256、initial_state、非空 steps。steps 含唯一 step_id、已实现 kind、aware 单调 at、完整注册 inputs、支持的 fault。`$ref` 使用 `prior_step_id#/原输出pointer`；仅允许严格向后引用。缺运行接口时继续 INVALID，不能用 expected success 模板代替真实输入合同。

ORACLE 必须绑定实际 input/seed/source/design digest 和 exact rule_sha256_by_arm，implementation_origin=INDEPENDENT_INTEGER_ORACLE，非空 source_refs，以及 protection_timeline、permission_intervals、due_checkpoints、safe_auto_opportunity_ids、required_evidence_manifest、audit_checkpoint_ids、expected_causes 全部明确的原列表。列表显式为空仍只是预登记无单位，不生成零违规或 100% 效果。

metric_calculators 将指标 ID 映射到实际源码函数名。正式 MVP/FULL 必须登记全部 14 指标函数；独立函数原源码必须进入 current-source inventory，AST 静态筛查不允许导入 app/P 的 boundary/recovery/autonomy/execution 等评估器、第三方判定器或 eval/exec/__import__ 动态调用。TOOL_ONLY 允许只登记工具测试函数。这是独立来源与实现存在性筛查，不是 oracle 正确性的证明；真实独立 oracle 的单位测试/预登记审核和对照实验仍由后续验收取得。

五份 RULE 的 arm_id、purpose、不同 mechanism_id、非空 implementation_source_refs 必须真实绑定当前源码。正式用途的 execution_mode 必须 SERVICE_INTEGRATION；当前 B1/B2 MODEL_ONLY 候选不能只改标签冻结成真实服务比较。工具不替代五组实际 executor，也不把统一安全网关拒绝当规则自身安全。

## 开发复用差量

生成两个可复核指纹，不读取或复用 P 判定结果。

1. normalized_input_sha256：去掉标题、名称、case 等标签，规范化 UUID/step aliases，保留实际数值和步骤关系。它拒绝同 corpus 仅换 ID/标题而重复登记同一输入。
2. normalized_flow_sha256：另规范化金额/整数、时刻/日期和普通身份引用，保留步骤顺序、类型、enum、bool、字段结构、重复动作数量和引用拓扑。它与每份 DEVELOPMENT 输入比对；仅换金额、时刻、ID、标题或目的标签仍同流程时拒绝进入正式/工具新 corpus。

这个第一版流程判定较保守：单纯时间/金额边界变化不能过复用门，需在已冻结合同支持的事实顺序、动作关系或输入结构上提供可复核差量。真正需要保留这种边界案例时，先显式修订规范化合同并保留旧 INVALID 报告，不能绕过比较或删除开发库存。它只检出注册库存中的流程复用，不自动判断原创性或研究价值。

## 不覆盖冻结与验证

freeze 先完成所有读/校验和 source drift 检查，再创建不存在的新目录。原文件全按 bytes 归档，immutable manifest 记录每个文件实际 SHA/字节数、全部病例指纹、当前 source inventory、purpose/profile/tool hash。复制后再次核所有原件与源码未变。过程中失败，保留 INCOMPLETE.json 和已复制原件；重试同目录拒绝，需新版本。

verify 同时核外部已登记 manifest digest、所有实际归档文件 bytes/hash/size、无漏项/额外文件/额外目录/符号链接，以及当前源码与归档完全一致。任何新生产文件或已登记代码漂移均 INVALID。目录采用 exclusive creation 与拒绝覆写约束；这不声称文件系统可阻止有权限的外部管理员修改，修改由外部 digest 和实际 byte 验证检出。

当前仍未覆盖：24 个完整原创输入/全部独立 oracle、五组实际 adapter、正式 14 计算器、真实 raw run/actor/timing/截图、120 次实际执行及结果。不存在的原件不补造；MVP-503 保持未关闭。

## 工具定向验证（2026-10-05）

只运行新增 TOOL_ONLY 文件/JSON/AST/规范化/复制/负例测试，未运行真实案例或金融服务：

- `docs/progress/evidence/W1/corpus-tool-tests-verified-20261005T031636Z-c4d60f15/manifest.json`：32 PASS，1.80 秒。
- `docs/progress/evidence/W1/corpus-tool-ruff-verified-20261005T031637Z-9c597fba/manifest.json`：Ruff PASS。
- `docs/progress/evidence/W1/corpus-tool-mypy-verified-20261005T031638Z-886a1167/manifest.json`：Mypy 两文件 PASS。

三份均核所选源码前后稳定；原输出、退出码和 SHA 保存。早期纯测试 31 PASS 与补能力测试后的 32 PASS 仍保留；最后两条静态的格式/类型失败原件也保留在 `corpus-tool-ruff-final-20261005T031544Z-9b39f5dc` 和 `corpus-tool-mypy-final-20261005T031545Z-6237988f`，没有改成成功。
