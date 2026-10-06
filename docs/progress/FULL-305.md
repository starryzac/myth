# FULL-305 最小目标冲突集

状态：**目标策略约束的纯最小冲突能力已实现；真实财务适配及完整范围待补，编号未关闭**。

## 完成内容

新增 `apps/api/app/domain/multi_goal_allocation.py::find_minimal_goal_conflict`，复用本批联合分配的整数可行域，固定原现金、收入来源时刻、义务/生活/应急/目标保护、既有归属和目标授权。可定位约束为逐目标最低保证、到期不允许部分或延期时的完成要求、月 max。

删除式检验返回删除最小的不可满足集合；结果中每个约束均有单项移除后真实可满足的整数见证。见证明确 `counterfactual_only=true / grants_authority=false`，移除 max 仅为诊断，不改变原权限。原保护资金已经不足时返回 BASE_INFEASIBLE，不把它伪装成目标策略冲突；原件不完整时 UNKNOWN。

## 验证

直接测试在 `apps/api/app/tests/test_multi_goal_allocation.py`。两目标各保证 8 分、仅 10 分资金时，输出两项保证冲突；逐项移除见证为 8 分，非返回全体约束充数。同时覆盖单目标 max 与保证冲突、负保护池不可放松、无来源 UNKNOWN。与 FULL-302 共用本批 41 PASS / 严格 mypy / Ruff 原件：

`docs/progress/evidence/W4/multi-goal-allocation-direct-pure-20261005T125326Z-25f2a0f5/manifest.json`，以及同批 types/static manifest。命令与源文件范围见 FULL-302 记录。

## 限制与下一前置

这是 `GOAL_POLICIES_WITH_IMMUTABLE_FINANCIAL_BASE` 范围内的删除最小集合，不宣称最少基数集合，也不是全部十二模板的通用冲突引擎。月 min 是软短缺目标，不伪装为不可满足硬约束。尚缺真实 SQL 事实/权限适配、产品/付款等其他策略冲突解释，以及 PostgreSQL 与最终完整版验收。原硬保护字段始终不可被诊断或修复通道放松。

## 2026-10-06 真实来源与生产只读接口差量

本节为后续增量，保留以上原实施记录和失败证据；编号仍未关闭。

新增 `domain/full_goal_conflicts.py`、`services/full_goal_conflicts.py`、`api/v1/full_goal_conflicts.py`。`GET /api/v1/planning/full-goal-conflicts` 复用实际 `full_joint_goal_planning` 在同一 clean REPEATABLE READ READ ONLY Session 中取得当前已验真的收入 fragments、完整目标分母、原 365 日及 FULL 保护点、当前银行投影及 audit。原件、所有权、银行、版本或分母不足时返回 UNKNOWN，解释和修复均为 null，不能缩小目标分母。

对真实已核输入复用原删除最小求解：展示每项约束的原目标/策略/版本、最低保证或到期额、月 max、当月原已贡献、当前原归属、来源及单项删除见证。明确见证只适用于“该最小集去掉一项”，不声称最少基数、其余所有约束兼容或所有冲突消失。负硬保护点单列不可调整的短缺，不能把它当可调整目标策略。

完整 `current_input_hash` 保留原实际 as_of。独立 `review_state_hash` 只去掉顶层读时钟 as_of，并绑定用户（原输入）、epoch、本地日及实际当前有效状态；所有现金、reservations 保护、来源 hash、observed_at、valid_from、有效版本、完整保护曲线仍在摘要中。新事实或真实经济曲线变化必须重新读取。

### 本次定向验证与保留失败

七新源（上述三生产源及四个 `test_full_goal_conflicts*` 文件）已通过严格 mypy 7 文件、Ruff；对应原件：

- `docs/progress/evidence/W3/full-goal-conflict-and-selected-repair-types-repaired-20261005T185352Z-17985322/manifest.json`：PASSED，scope/global 均稳定。
- `docs/progress/evidence/W3/full-goal-conflict-and-selected-repair-static-repaired-20261005T185352Z-f23cf0e3/manifest.json`：PASSED，scope/global 均稳定。
- `docs/progress/evidence/W3/full-goal-conflict-and-selected-repair-direct-repaired-20261005T185352Z-e002843c/manifest.json`：受影响 service/API 11 节点及新增 inactive/expired/completed 3 节点，14 PASS / 26.43 秒，wrapper 27.959891 秒，scope/global 均稳定。

首轮 `full-goal-conflict-and-selected-repair-pure-20261005T185028Z-d4da87f7` 是 64 PASS / 2 FAIL：包括未改旧 solver 41 节点和首次新 domain 14 节点；两个 FAIL 指出 `PolicyLifecycleError` 是 ValueError 子类，被本层错误包装为 422。现已保留原 409 原语义，仅对真正候选 ValueError 使用 422，受影响节点真实重跑通过。首轮类型错误（PG candidate 错误导入路径）和两处静态错误全部保留，失败前七源字节归档 `.runtime/W3-goal-conflicts-first-red-20261005T1851Z`。未改的旧节点没有再次重复运行。

### 未覆盖项及下一前置

新增唯一 `test_full_goal_conflicts_real_api.py` 实际隔离 PG 候选，涵盖实际模型确认、合法 payroll 双银行腿、GET/POST 全物理表零写、原最小集、新版本明确确认、旧版本保留、stale 拒绝及真实现金篡改 UNKNOWN。代理未运行 PG，不能把纯测试或候选文件视为实证。Root 需注册 router 和 POST preview 的 RRRO 依赖后统一运行该节点。付款/产品/全部模板的通用冲突、多期全局范围和最终全量仍未覆盖。

## 2026-10-06 真实 API 读取与冲突界面差量

新增 `apps/web/src/api/full-goal-conflicts.ts` 与 `components/GoalConflictRepairPanel.tsx`，读取实际 GET 返回，展示完整原目标分母、删除最小集合、每项逐移除的反事实见证、不可调整保护短缺及原权限范围内的修复状态。读取器使用 Root 已生成的真实 `FullGoalConflictResponse`、`FullGoalRepairResponse`、`GoalRepairPreviewBody` 类型，并独立核对用户、epoch、当前策略/版本、hash、严格整数分、删除见证及全部目标身份；不把一个 read-only 响应当银行授权缓存。

UNKNOWN 时保留原不足项，不显示旧成功金额或假空冲突。当前目标列表若由宿主传入，逐项比对实际版本；无法获得某目标当前版本时，该目标不可选。页面允许只读刷新，用户修改范围、外部目标版本改变、读取失败或新读取进行时会撤掉旧预览；原其他写族的 pending/storage_error 和全局 mutationBlocked 会阻挡提交预览。新组件尚待 Root 接入 GoalsPage，原共享页面未由本代理修改。

### 本次直接验证与边界

- API 读取器 22 项与面板 11 项：33 PASS / 2 文件，native Vitest 3.83 秒，wrapper 4.936234 秒。原件 `docs/progress/evidence/W3/full-goal-conflict-repair-ui-direct-unit-20261005T191311Z-295324c7/manifest.json`，scoped/global source 均稳定，source_changes 为空。
- 五个新 TS/TSX 文件定向 ESLint PASS，wrapper 3.714407 秒。原件 `docs/progress/evidence/W3/full-goal-conflict-repair-ui-static-20261005T191256Z-c2078d7a/manifest.json`，scoped/global source 均稳定。

测试 JSON 明确标为 `SYNTHETIC_UNIT_HTTP_ONLY_NO_DATABASE_BANK_OR_RUNTIME_PROOF`，仅检验原合同解析、交互和篡改拒绝，不能作为真实 SQL、银行、用户或浏览器实证。Root 约定在本组件 FINAL 后统一执行整体 Web 类型检查，本代理未重复运行，也未登记 type PASS。实际 PG、页面挂载和真实浏览器仍待 Root；原编号及初版/完整版全量验收均未因此关闭。

### 同日 TYPE_ONLY_DIFF 修订

首次统一 Web 类型命令 `docs/progress/evidence/W6/goal-reallocation-ui-generated-types-20261005T191802Z-540b5eb6/manifest.json` 原 FAILED 保留：本模块仅 synthetic `goal-conflict-fixture.ts:7` 的 JSON `number[]` 无法推断为八元 objective_vector tuple，另六处错误属于独立 303 reader。本次仅夹具加明确返回类型及 `unknown` 中转并注明合成范围；生产 reader/组件、原 JSON 和任何金融判断均不改。原 `.runtime/W3-goal-conflict-ui-final-20261005T191827Z-3df25732` 字节及 SHA 不覆盖。

定向该夹具 ESLint PASS：`docs/progress/evidence/W3/full-goal-conflict-ui-fixture-type-only-static-20261005T192125Z-96544cd0/manifest.json`。33 个行为测试不因类型注释重复运行；后续唯一 whole Web types 由 Root 执行，本追加不声称其已通过。


## 2026-10-06 04:30 Root增量

六实际PG批终态PASS/1600.41s，wrapper1611.67274s；见 `evidence/W3/actual-release-sources-consent-repair-and-current-maturity-20261005T195334Z-810817b1`，范围来源稳定/全源独立变化。仅相应节点实证，非完整版本验收。具体旧缺口与原失败保留。
