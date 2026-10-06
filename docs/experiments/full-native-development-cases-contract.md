# FULL 开发病例：原生混合执行与 50 族作者原件

2026-10-06，状态 `AUTHORING_NOT_FROZEN_NOT_RUN`。这是必要业务输入与实际服务入口；不是正式实验成功、金融验真、真人研究或完整七臂运行。FULL-804/805/808 关闭仍 PENDING。

## 可调用入口

`app.services.full_native_cases.FullNativeCaseRunner(engine, user_id).run(original_input_bytes, expected_epoch_id)` 返回 `full-native-development-case-result-v1`。`validate_full_development_case(original_input_bytes)` 只校验原字节、闭合字段、步骤顺序和引用结构，不运行服务。

输入协议 `full-family-case-input-v1`，profile=`FULL`，purpose 只能 `DEVELOPMENT`，initial_state.mode 只能 `EXISTING`。初态原 expected_epoch_id 可为 null，调用方必须给实际已验证 OPEN epoch；有原绑定时必须严格匹配。原始字节 SHA 单独保留，不补写原 INPUT。入口只接受本机 `127.0.0.1:54329` PostgreSQL 的 `bf_test_<32hex>`、固定模拟 DemoUser 与实际 OPEN epoch；它不建库、迁移、seed、reset、切角色或连接正式库。

Root 在运行前创建并登记全新隔离库、调用真实原 seed 与已实现的零余额第二 CASH 初态接缝，再给该入口 EXISTING 库和实际 epoch。这些动作尚未由本包实测。Local USER 凭证仍需 Root 服务器配置；`LOCAL_USER_LOGIN` 输入严格 `{}`，服务端使用已有真实签名会话，不将 cookie、secret、header 输出或保存到病例。

原 MVP 步骤仍调用原 `ScenarioRunner._dispatch` / `_fault`，原 v1 源与协议不改。Full 操作只映射 `full_native_operations.OPERATIONS` 固定真实 API；当前实际 50 种，原 Root 表实际 37 种，加 13 种本包所需真实读取/预览。GET资产规划只允许严格 `FullAssetPlanOptions`，固定映射成 `planning_*` query；原金额 bool/未知字段/任意 URL/headers 不获接入。周期命令的原 key 是独立受限 ASCII 路径身份，不能注入路径、query 或 fragment。

每次 Full dispatch 原样保留 `{status_code, result, original_response_text, response_body_sha256}`。成功 row.result 是该**实际 body 的引用视图**，不是另造结果；失败保留完整 native_response 和 error，不能给后续 `/result/` 引用。原引用仍仅严格向前一步已经返回的原件。HTTP200 不自动成为金融成功；输出 financial_acceptance / frozen_acceptance / bank_authority_granted 永远 false，metric_results=null。未注入的 TimeoutError 仅 ACTUAL_TIMEOUT，不猜银行已提交。

匹配原 expected_error 后可继续；不匹配或缺原件时停止后续 dispatch。每一个未执行原步骤都仍记录 skip 与 opportunity_denominator_retained，完整输入分母不能删除。Full故障尚未实现，不能用旧 EXECUTE_ACTION 故障冒替；明确作者副本篡改操作同样缺 producer，均返回 MISSING，而非假执行。

## 作者数据版本

当前主包：`docs/experiments/full-family-authoring-v4-20261006/manifest.json`，SHA `f3ea8a71f0aac694702ccef9dfba322dfe907008afae1bdf4e697e9200183c52`。

实际 50 族 / 100 变体 / 16 类，整族 18 DEVELOPMENT、32 VALIDATION、0 FROZEN。每案有完整 INPUT、TRUTH、SCHEDULE，每族有 DESIGN；SOURCE 保存实际源码原字节，SEED_DESIGN 标记尚未初始化。除数值日期外也忽略作者标签、key、step名称的因果拓扑读回为 50 个不同流，不将金额改动算新族。

八真值是 `DETERMINISTIC_AUTHOR_DESCRIPTION`：登记硬义务、权限、可调整范围、产品条款规则、机会、预期自主规则、冲突规则、恢复身份。它们各有作者原 DESIGN SHA，尚未独立审核/原账本重放，不能直接当实测正确值；全部28指标 null、actual_runs=[]。原 N02 两次 income/purchase 顺序型草稿留在 v1 补充数据，不列主50。v1 实际51/102与硬编码split失配仅旧作者草稿，不能据其口头50作证明。

v1、v2、v3 原件保留。v2显式主50；v3修正实际 seed 收款人 `synthetic-landlord-001`、预登记604同可信时点 composite sequence、收紧消费初态；v4补实际 `RUN_RECOVERY` 合同到期原回执后才 maturity preview，并绑定未注入 timeout 不猜银行原因的新源码。作者输入未根据金融运行结果反调；本包没有运行金融。

只读交付检查：`W7/full-fifty-authored-original-data-readonly-20261006T032055Z-b5425ccb`，100原 INPUT/TRUTH/SCHEDULE 字节SHA、整族split、源码当前/归档一致、混合步骤 envelope 校验通过。`AUTHORING_READINESS` 仍 **AUTHORING_INCOMPLETE**，并非正式冻结。

## 具体未覆盖

| 范围 | 尚缺能力或原件 |
|---|---|
| 50×7 / 8消融 | Root真实机制 selector / 每臂独立真实执行和完整28指标；当前混合入口只单个DEVELOPMENT流 |
| 100案 API body | 所有金融DTO/refs需要实际原返回后重验；纯 envelope 合法不证明每案银行或规划成功 |
| 季节 S01—S03 | 默认seed60天不具完整既往公共季节窗；未知建议不得造READY/采纳0，需要合法隔离历史事实 |
| T1 T01—T03 | 当天缺口不能接受未来现金；原604当天deadline下T1可真实拒绝，完整受理后跨日协调能力仍待实际适配 |
| U01 / X01—X02 | Full组合 DROP_BANK_RESPONSE 与只改副本的历史完整性 producer 尚未实现；原旧篡改负例不删 |
| 本金到期 | 原RUN_RECOVERY实际返回无动作时保缺口；仅首个实际动作原件能被引用，不能以购买回执当到期回执 |
| 新收款 P01 | 没有银行原身份应拒绝，用户填写名称不生成银行payee |
| 修复 D03 | 仅实际返回首个修复row后用户脚本再次原FullGoal双hash确认；无row保MISSING，不能造候选 |
| 时钟与初态 | 预登记合成trusted时轴，不证明实时调用延迟；默认seed及正式历史不改 |

## 定向检查

60纯风险 PASS3.45s：`W7/full-native-mixed-original-direct-20261006T031056Z-9ad4b718`；随后独立 timeout 风险1PASS2.35s：`...noninjected-timeout-risk-20261006T031913Z-0a60fb09`。5源strict与Ruff最终通过：`...final-types-20261006T031913Z-50e7ccd5`、`...final-static-20261006T031914Z-ac9c4a98`。以上scope/global均稳定。

实际候选 `test_full_native_cases_integration.py::test_actual_existing_mixed_full_api_and_original_mvp_reads` 仅 collected1，证据 `...real-candidate-collection-20261006T031619Z-1fc38f96`，**PG NOT_RUN**，Root串行排程。未调用 make check、浏览器、Docker 或真实资金接口。

首追加纯风险 41PASS1FAIL（错误操作数量断言）、首Ruff import RED，首mixed类型 RED（测试引用未显式 export）原件与失败日志保留；修正数量与import/type接缝未删风险断言。最终冻结清单见 `.runtime/full-fifty-native-data-final-20261006T0323Z/manifest.json`。
