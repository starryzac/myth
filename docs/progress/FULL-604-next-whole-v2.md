# FULL-604 下一整仓服务器选择 v2

状态：独立生产 domain/service/router 已实现，27 项直接纯/HTTP 接缝风险通过；真实银行候选仅 collection。Root 尚须注册新路由和 RRRO；FULL-604 仍 PENDING。本包不改 v1 算法、原件、哈希、确认或银行执行门。

原范围见 `requirements-traceability.md:92` 和完整计划第 697–708、1344–1346 行：现金、T0、T1、到期定存、损失/时限、完整回执及无法及时恢复的 LIQUIDITY_RISK。该包交付的是当前实际可受理的下一笔整仓，不代表全部恢复范围完成。

## 实际入口与原写路径

新增 `domain/full_recovery_next.py`、`services/full_recovery_next.py`、`api/v1/full_recovery_next.py`。协议 `full-recovery-next-whole-v2`，三个新端点：

| 路径 | 作用 |
|---|---|
| POST `/api/v1/full-recovery-next-actions/preview` | 完整 RRRO 原计划与原 v1 预览；无写 |
| POST `/api/v1/full-recovery-next-actions/prepare` | 显式请求服务端选择的一笔原动作；调用 v1 prepare |
| GET `/api/v1/full-recovery-next-actions/by-key/{key}` | 当前签名 USER 回读同一原 root key；未找到不终局 |

POST 闭请求为 `{policy_id, expected_version_id, expected_epoch_id, idempotency_key}`，无仓位、金额、截止点、角色、时钟、报价或银行结果输入。UUID 使用实际 UUIDReference；根键非空且最多120字符。所有路由沿现生产 LocalActor USER 依赖验证 owner/role/有效时点。身份本身不授予银行权限。

新请求在原 clean RRRO 读取当前 FullRecovery 身份及完整 `read_full_recovery_planning`。保留完整 planning response、全部 candidates/lossless_steps、1098 原阶段曲线、uncovered_checkpoints、原 quote、来源问题和原 LIQUIDITY_RISK；全量候选和已选分母分别明确，不把未知计划显示为零。

选择遵循原 lossless_steps 顺序，只允许当前 REDEEM、整仓、零费零损、已确认原 scope、原 ASK、T0/T1、原 quote 有效和按原 deadline 可到款者。重复/缺失/替换的 candidate/selected 分母拒绝。选择后仍调用实际原 `preview_full_recovery_execution`，核其 owner/epoch/version/position/as_of/deadline/effect hash 与当前输入一致。原 v1 scope 尚不足则显示 BLOCKED/UNKNOWN；不能跳过失败的财务验证或改时间找成功。

prepare 先以原 audit command guard 排除 reset，再由原 v1 prepare 在 User 锁中重新验证所有当前事实、完整 FULL 保护、原授权和 ASK。没有 v2 自建 Action、资金、同意或回执写入。后续必须显式调用现 v1 `/full-recovery-actions/actions/{action_id}/confirm` 和 `/execute`，使用原 epoch/effect hash/签名 USER，同意与银行护栏完整保留。

## 同键恢复与多仓界限

root key 映射为 v1 内键 `next-whole-v2:<hash(protocol,exact root key)>`，与仓位/版本无关。已有原键时，原完整 v1 lookup 先验证实际原请求/hash/Action/原 trace，返回该 Action；不重读新计划、不准备下一仓。改变同根键的 policy/version/epoch 是 IDEMPOTENCY_CONFLICT。网络未知、UNKNOWN、已撤销或历史同键不能因此替换仓位/key，也不凭未找到认定未受理。

lookup 的 `bound_request` 明确是“已验证 v1 原 body + 完全相同 root key”的有限投影；`request_binding_kind=PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY`，`original_v2_request_separately_recorded=false`。完整原 v1 request/server request/hash/Action/同意仍保留，不冒称另有 v2 原持久化命令或把重构字段说成新原字节。

一次只准备一仓。后续仓位需要用户显式发起新的 fresh 请求，并从当前全事实重算；前一未知结果应先原键回读/恢复。当前包无新前端跨族工作区接线，不能以 API 支持新请求表示已有自动安全顺序 UI。原 v1 POSITION claim 与银行 closing-position guard 继续阻止同仓重复经济效果。`atomic_combination=false`、`automatically_advances=false`；部分改善不等于原全缺口解决。

## 验证与失败原件

- 27 直接纯/HTTP 风险 PASS /7.20秒：`W5/full604-next-whole-final-repaired-direct-20261006T031609Z-2a833999`。包括原多仓顺序/全分母不变、T1迟到、MATURE/有损不得转 REDEEM、闭请求、原根键回读、PLANNED/UNKNOWN 不推进、换身份冲突、原 hash/owner/key 缺失拒绝、NOT_FOUND非终局和签名 USER。合成夹具仅验证检查/路由，不是银行或正式场景成果。
- 最后六源 strict types PASS：`...final-repaired-types-20261006T031645Z-ce22eaa4`；原六源 Ruff/format PASS：`...final-static-20261006T031347Z-9f9c61db`、`...final-format-20261006T031347Z-3577810f`。最后仅新测试文字/参数修订的 Ruff/format 另有最终原 manifest，详见独立 freeze。
- 新真实候选 `test_full_recovery_next_integration.py::test_actual_next_whole_key_selects_original_t0_and_never_advances_after_unknown_or_revoke` 只收集1项/4.91秒，`...native-candidate-collection-20261006T031156Z-3be9739a`，**NOT_RUN**。它准备真实原收入/购买/消费/确认和 USER，故障发生在原银行实际 commit 后，拟核完整物理零写回读、原键不推进、撤销后原 Action/key/receipt 恢复。复用现隔离 T0 业务时钟，不证明现实当前瞬间、T1或多仓银行组合。
- 首25风险 PASS `1c470c9b` 不改。首次 Ruff 两处导入错误 `6fb45139`、新未来账单夹具多余 observed_at 导致 `0c758a7d` 与 strict `e10bc5d5` 失败、新未来账单期限假设 `6c294f19` 失败全部保留。对应源码逐字节保存在 `.runtime/FULL-604-next-whole/source-before-*`。

未来账单原 pure 实算证明：未付款本来属于当前保护，原 deadline 仍 as_of，不能用未来 due_date 延后时限。修复的是新增测试对真实原语义的错误假设；原生产 deadline/保护算法未改。原25 PASS/1 FAIL和该失败原件没有升级为成功。

## 精确未覆盖与后续接线

Root 需 include 新 router；POST preview、GET by-key 必须按新 prefix 配置原 RRRO get_session，prepare仍原 engine writer。之后由 actual Main 生成 OpenAPI/TS，再接消费者。当前未注册/Schema未生成、候选银行未运行、浏览器与全量均 NOT_RUN。

T1只在原 planner 与 v1全部门真实通过时可进入原执行链；没有新增T1银行证明。现实墙钟下当前瞬间 deadline 很可能过期，保留原拒绝；不能改成确认后15分钟。GOAL回款仍须原总边界安全改善门，当前纯范围匹配不保证真实 prepare 通过。仅新 FULL 保护产生缺口且原 MVP 无负点也不能绕原 redemption 改善门。

MATURE 在原 modern `domain/execution.py` 明确要求独立 reconciliation。真实旧到期链位于 `services/recovery.py` 的 ASSET_MATURITY→simulated_bank→原回执；`full_maturity_replanning.read_original_maturity` 只读已完成原件。旧 run_recovery 还可能自动处理其他已授权仓位，不能直接包装成新逐次 USER 到期执行。需后续单独选择的原到期对账接缝。

部分赎回缺原部分本金/claim/银行 position 合同；有损缺新明确 loss 权限，原 RecoveryPolicy max_fee/max_loss=0，USER checkbox不能覆盖。当前只支持原整仓选择的部分“总体缺口改善”，不支持“单仓部分本金”。多仓端到端顺序、到期独立回执、有损专项确认、边界收缩触发、完整恢复验收及真人研究均未覆盖，不关闭 FULL-604。
