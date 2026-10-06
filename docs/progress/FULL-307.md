# FULL-307 目标进度与动态节奏

状态：功能候选已实现，直接纯检查通过；真实 API/PG 两节点待主协调器执行，完整项未关闭。按已登记的功能优先执行修订推进 W3 依赖；本次窄检查使用现有 W4 证据目录，不调整原 FULL 编号，也不冒充完整版验收。

原追踪依据：`docs/spec/requirements-traceability.md:75`、`docs/spec/full-delta-map.yaml:1296–1300`，对应原完整版 F:1264–1266 的 `[min,target,max]` 内动态月储备与进度。原文没有规定唯一节奏公式；以下为首次真实执行前明确登记的确定性实现规则，不能按后续结果反调。

## 可运行能力与接口

- `app.domain.dynamic_goal_reserve.compute_dynamic_goal_reserve(DynamicGoalReserveInput)`：仅消费严格、带原来源的实际单目标、当月贡献、收入 fragment 和硬保护点；返回明确状态及整数分建议。
- `app.services.dynamic_goal_reserve.read_dynamic_goal_reserve(session, user_id, goal_id, now)`：同一 clean Session 的 REPEATABLE READ / READ ONLY 快照中读取实际目标、当前有效版本、已确认 FULL 模型、银行/exposure/收入原账本、当前归属及当月贡献原证明；完整当前审计链验证后再给可计算金额。
- 新路由 `GET /api/v1/goals/{goal_id}/dynamic-reserve`。空查询模型拒绝客户端用户、时钟、金额、余额、权限和 source override。路由尚需主协调器注册，并在原依赖开始读任何用户表之前设 RR/RO。

返回 `schema_version=verified-dynamic-goal-reserve-v1`，含真实 `policy_effective_status`、`state`、`reserve`、`source_evidence_ids/source_issues/input_hash`。明确 `simulation=true`、`grants_authority=false`、`preview_only=true`、`future_income_included_cents=0`。缺原模型或财务原件时为 UNKNOWN/空 reserve，不推定零贡献或可用资金。

## 显式节奏规则

采用用户本地当前自然月（当前支持原 UTC / Asia/Shanghai），不以 30 天近似月份。记剩余目标 `R=max(0,target-current_owned)`、本月实际贡献 `C`，从当前月到 deadline 月含首尾的日历月数为 `n`，过期时 n 保留为 1：

`gross_pace=ceil((R+C)/n)`；当月累计目标为 `min(R+C, clamp(gross_pace, monthly_min, monthly_max))`。将 C 加回仅用于累计节奏计算，不能将它加入可用资金。当前月已经贡献的 C 从新的追加建议中扣除，避免相同实际账本重复形成建议。结果另给 nominal target、gross pace 及二者差额，不能将动态目标误称原用户 nominal target。

剩余目标小于月 min 时，累计需求按真实剩余额截断；current owned 已超 target 时保留实际超额并标 COMPLETE，进度最多 10000 basis points，不伪造实际完成日期。月 C 已超新 max 时返回 MONTHLY_MAX_ALREADY_EXCEEDED 并保留真实 C，不撤销历史贡献。

最低保证未满足时，动态累计目标可以在 max 内上提。保证仍不足或在不允许部分/延期的到期点不能满足剩余目标时，分别返回 HARD_GUARANTEE_SHORTFALL / DEADLINE_BLOCKED，不能由 allow_partial 或 allow_deferral 放松。已过期、未生效/停用、原硬保护现金风险和原件缺失分别返回明确状态，未证明的金额保持 null；延期成本仅报告已实际逾期天数乘原已确认成本，不伪造预计完成日。

可用资金只来自原收入账本尚 available 的 fragment，收入发生时刻必须不早于当前实际确认/生效窗口。旧收入、reserved/spent/assigned 和已经归属目标的钱不能充当新收入。建议还受实际收入、当前月 max、剩余目标和完整硬保护曲线最小余量共同限制。法律义务、生活、应急、原目标归属及其他原保护不得被软节奏解除。

本批保留原 365 天边界中所有 goal minimum 为独立 other protection，响应明确 `ALL_ORIGINAL_365_DAY_RESERVES_RETAINED`。没有宣称已完成联合目标 minimum 的精确释放或整个联合分配问题最优。

## 本次源文件与 SHA256

| 文件 | SHA256 |
| --- | --- |
| `apps/api/app/domain/dynamic_goal_reserve.py` | `af760755170e181a3fd9a8121500142944d6502805594c158bde31950221cb9e` |
| `apps/api/app/services/dynamic_goal_reserve.py` | `216406b0de72e3748e0d708bacd53000282a243e2fb3cc9b07bec87d82d4db5a` |
| `apps/api/app/api/v1/dynamic_goal_reserve.py` | `d13b19b34e6c4ad98a5cc628ebd036c5822642f404c3d012bfee50ee0cd03617` |
| `apps/api/app/tests/test_full_dynamic_goal_reserve.py` | `067a27c0d120b466ab46ed7fe394fa1269c35bbee16e5efc143407630ebdbf61` |
| `apps/api/app/tests/test_full_dynamic_goal_reserve_api.py` | `1680398e3c10003d662678d97749f3db225b16a09ca5c44be0d304b406b53eb1` |

没有修改旧目标算法、旧策略 canonical/hash、单目标执行、shared model、migration 或正式模拟历史；没有跨请求授权缓存、未来收入入金或真实资金接口。

## 已执行检查与失败保留

只执行模块直接纯/类型/静态检查，没有 PG、浏览器或全量。原纯批次 **16 PASS / 1.75 秒**：`docs/progress/evidence/W4/dynamic-goal-reserve-direct-pure-20261005T133418Z-75050a59/manifest.json`。覆盖进度前后、当前月已贡献/重复、剩余额截断、已超 max、部分允许/拒绝、保证、真实保护余量/负现金、旧收入、过期/停用/未知、Asia/Shanghai 自然月切换及非法 API 参数。

另增保证在 max 内提高节奏但不变成未来收入的一个直接节点，**1 PASS / 1.80 秒**：`dynamic-goal-reserve-hard-guarantee-direct-pure-20261005T133824Z-39da3dc7`。这是分次 16+1，通过记录不能改称一次 17 节点全量。

加入两项 PG 候选后，五文件严格 mypy、Ruff 已通过。追加纯节点后的一次 mypy 因测试直接比较 Optional[int] 失败，原件 `dynamic-goal-reserve-final-types-20261005T133824Z-b50e9aba` 保留；只增加显式非空断言，公式和预期不变。当前五文件严格 mypy PASS：`dynamic-goal-reserve-optional-assertion-types-20261005T133900Z-1d463ac7`；改动测试 Ruff PASS：`dynamic-goal-reserve-new-risk-static-20261005T133901Z-825e2343`；五文件格式检查 PASS：`dynamic-goal-reserve-final-format-20261005T133825Z-975cf32d`。此前 API 候选五文件 Ruff PASS 原件 `dynamic-goal-reserve-api-risk-static-20261005T133710Z-d8f9353f` 也保留。

## 具体未覆盖项与下一前置

主协调器应注册路由/前置 RRRO，再在新自建隔离库实际运行两项 `test_full_dynamic_goal_reserve_api.py` 节点。正向候选通过原 FULL 目标确认及真实外部 payroll 两银行腿，再读动态目标；随后实际原 GoalIntent 准备/原 hash 确认（仅实际 ASK）/执行及原收入归属，再读取进度/同月新建议并比较所有物理表零写。负向候选检查缺模型、未知参数和实际现金 projection 篡改后的保守返回。这些测试尚未运行，不能计为金融实证。

当前动态金额仅用于新只读视图。正向候选执行的是**原已确认 nominal 目标动作**，用其原归属事实证明节奏随实际贡献变化；没有把动态金额接入现单目标执行，也没有接联合 planner 的实际动态目标预算或更新其已确认摘要。尚缺多目标动态节奏实际调度、月初/月底真实事件回放、前端展示、本项目完整真实集成及最终初版/完整版全量验收。FULL-307 保持未关闭。

2026-10-05 真实集成补录：主协调器已注册 GET 及前置 RRRO，并实际执行上述两个 PG 节点，**2 PASS / 90.58 秒**。原件 `docs/progress/evidence/W3/dynamic-goal-actual-month-contribution-and-source-real-pg-20261005T134730Z-f91a7c48/manifest.json` 为 PASSED，wrapper 92.576334 秒，`scoped_source_stable=true`、`all_source_stable=false`；唯一全局变化为同期独立前端 `apps/web/src/features/full-policy-operation.ts`，不能改称全仓库源稳定。此次实际证明原到账 payroll、原 nominal 动作真实贡献后的同月防重复、缺模型/未知参数/银行现金篡改保守返回及完整物理表只读；没有执行新动态金额或联合调度。前述“待执行”文字保留其交付时状态，由本补录覆盖执行状态，原失败记录不改。
