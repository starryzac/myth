# FULL native GENERAL 私有机制消费者

2026-10-06 显式功能差量。状态：`IMPLEMENTED_PRIVATE_DEVELOPMENT_ADAPTER`；真实 PostgreSQL/银行/浏览器/七臂实验 `NOT_RUN`，指标 `null`。本合同不关闭 FULL-805，也不改旧 MVP/FULL 输入、P 结果、历史哈希或原失败。

## 服务与原件

`app.services.full_native_mechanism_steps.FullNativeMechanismSteps` 复用 invocation-local `FullNativeSteps`；`FullNativeCaseRunner(engine, user_id, mechanism_rules=...)` 在原 mixed 入口接四种私有步骤。`mechanism_rules` 由可信 Python caller 固定登记 `Mapping[str, RegisteredFullMechanismRule]`；Case 不能提交 RULE 路径/SHA、角色、Cookie、密钥、金额、银行结果或成功标签。默认空登记不能准备。已有 `FullNativeSteps` 和公共 HTTP registry 完全不改。

固定 RULE 仍由 `load_registered_rule` 按原 repository 区域、完整字节 SHA、DEVELOPMENT purpose、owner 和完整原请求验真，`prepare_full_experiment_asset_execution` 负责当前原现金/收入/占用、产品全集、Full365、P 原结果及原机制选择。适配器不产生资金规划算法、换选机制或伪造银行权限。

| kind | inputs 精确字段 | 实际消费者 |
|---|---|---|
| `FULL_MECHANISM_PREPARE` | `rule_id`, `body`（原 `FullAssetPrepareRequest`） | 构造精确 `FullExperimentAssetRequest(original_request=body, rule_original=server_locator)`，调用已接 private prepare |
| `FULL_MECHANISM_LOOKUP` | `idempotency_key` | clean RR/RO → `lookup_full_experiment_asset_execution`；原 key lookup 不读新 RULE、不 fresh selection |
| `FULL_MECHANISM_CONFIRM` | `idempotency_key`, `action_id`, `body={effect_hash, accepted:true}` | exact key/action/user/epoch/ASK 身份后原 `confirm_action` |
| `FULL_MECHANISM_EXECUTE` | `idempotency_key`, `action_id`, `body={}` | 同一原身份后原 `execute_action`；原银行/Full protection/授权/receipt 守卫保持 |

所有 UUID/body 走真实严格 JSON DTO，未知字段拒绝。Case 仍仅 `purpose=DEVELOPMENT`、`initial_state.mode=EXISTING`，不能 reset/seed/promote frozen。默认返回保留全部机会分母，不把错误/跳过当成功。

## USER 与 UNKNOWN

Case 先使用既有 `LOCAL_USER_LOGIN inputs={}` 真服务器凭证入口取得原签名 Cookie。适配器每次 PREPARE/CONFIRM/EXECUTE 都从 invocation 客户端 Cookie 重新调用 `verify_local_actor_session` 和 `require_local_user`，不接受/缓存 client principal。签名 USER 仅本地模拟身份，`human_identity_verified=false`；不能证明真人或单独授予银行权限。十五分钟到期后须原固定 USER 手动登录步骤，不能用旧会话元数据授权。

LOOKUP 无 Cookie/RULE 依赖，仍每步 fresh owned simulated PostgreSQL/OPEN epoch 守卫；返回原件为历史证据，不授当前权限。写步骤核原 key lookup 的真实 action/current namespace 与原 ASK 后再进入旧函数，其财务权限始终由旧执行管线逐阶段核验。适配器不自动确认、重试、换 key 或推进下一机会。

`FULL_MECHANISM_EXECUTE` 可显式采用原 `EXECUTE_ACTION` 的 `DROP_BANK_RESPONSE` 或 `FAIL_APPLICATION_PROJECTION` fault context，保持原 marker×engine×user 隔离、单次既定 action/key；其它 private fault pair 在 runner 标记 `MISSING` 且不 dispatch。实测故障尚未运行。返回超时仅保留原异常，是否真正 bank commit 必须随后 original LOOKUP/原 receipt/账本判定，不能由错误字符串推断。恢复采用同一 `idempotency_key`、`action_id`；不会重选 RULE/金额。银行原受理与 UNKNOWN 恢复仍为原函数语义。

示例顺序：

1. `LOCAL_USER_LOGIN {}`。
2. `FULL_MECHANISM_PREPARE {rule_id:<server registered ID>, body:<actual original FullAssetPrepareRequest>}`。
3. CONFIRM 的 action_id 和 reviewed effect_hash 以严格 object `$ref` 指向本次 PREPARE 原 `/result/action_id`、`/result/effect_hash`，作者明确 `accepted=true`。
4. EXECUTE 用同一原 key/action；若登记 expected response-loss error，后续 LOOKUP 和显式同 key EXECUTE 保留原身份。

此示例是接线合同，不是实际经济成功记录。每臂必须使用自身真实原 RULE/机制；不能把所有臂继续送到 P。B4 模型未调用、Goal/Pay/Recovery/多产品/梯度等缺 adapter 或生产者不返回 proposal 时，原拒绝完整保留，无默认 P 回退。

## 保存与边界

私有调用原件为 `full-native-private-mechanism-call-v1`、channel `PRIVATE_PRODUCTION_SERVICE_CALL`，保存完整实际 DTO request（PREPARE 另含精确 private_request）、原 LOOKUP、已验签 session 公共元数据、真实 `model_dump_json` 返回 UTF8 文本及 SHA，或原异常 code/message/status/exception_type。不录 Cookie/credential/header。输出 `outcome=RETURNED|RAISED`；这是生产 Python 服务调用，不能伪造 HTTP200。现有真实 HTTP 调用仍保留原 native_response/text/hash/status。

完整 capture 在 mixed `steps[i].private_service_response`，成功实际值在 `/result` 供原 ref resolver；失败只有 `/error`，不能从错误行引用成功字段。`apply_service_capture` 独立核原字节 SHA/严格 JSON 与 result/error 一致。`financial_effect_verified=false`、七臂实验标志 false、`metric_results=null` 保持；source/P/effect 历史完整性由原服务，而非本 capture 状态字符串证明。

剩余：真实 owned PG 的 RULE→prepare→USER ASK→execute、故障后的 same-key UNKNOWN 恢复及银行双维守恒未实测；全臂规则实际接线和 50×7/消融运行仍缺；真实模型 provider 尚缺；本地凭证真实配置由 Root 提供且不进入原件。独立 adapter不替代正式实验 author/freeze/指标/全量验收。
