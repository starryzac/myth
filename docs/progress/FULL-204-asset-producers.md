# FULL-204 实组合资产生产者差量

## 可运行功能与来源

仅新增独立 `domain/full_action_set_asset_producers.py`、`services/full_action_set_asset_producers.py`，不改已冻结旧 v1/global-full-v1 六源、现有资产执行十一源或金融公共底座。以同一干净 REPEATABLE READ/READ ONLY Session 的实际完整 v1 原库存为底，枚举当前 OPEN epoch 的每个 Full AssetAuthorizationPolicy、所有同 scope/Goal 的实际 MVP 资产策略，并分别保留 `PORTFOLIO` / `FIXED_LADDER` 分母。缺同 scope 原 MVP 策略保留 NO_MATCHING_MVP_SCOPE 项，不能用候选规划确认填银行权限。

每项调用实际 `preview_full_asset_execution`（没有 prepare/confirm/资金写）及真实财务/策略读取，保存实际 FullPolicyView、原 preview、完整 FullAssetPlanningInput、原完整 FullAssetExecutionBasis 和当前 AuthorityAssessment。纯消费者重算原 `plan_full_assets` 与 `build_frozen_portfolio`，原优化金额、产品顺序、每批原 command/effect、组合保护、来源与哈希均须一致。组合不是多个单产品预览金额相加。全部当前目录原记录/不可变 snapshot/银行来源/原确认/占用分母仍参与验证；任何未验原件均为 UNKNOWN。

组合签名保留总金额和有序各批原经济签名，权限统一为 ASK_ONCE，需原 whole 用户明确确认；金融执行仍由原专用 consumer 每批重新验真，观察结果不授予权限、不预留资金。数值差分与真正集合差分的全局聚合必须另用明确新算法版本，不能把新组合数学写回原 full-v1 冻结 trace。

## 接线接口

`capture_asset_family(session, user_id, now, base: ActionSetCapture | None = None) -> AssetFamilyCapture(inputs, result, originals: DecisionCapture)`。

`derive_asset_family(AssetFamilyInput) -> AssetFamilyResult` 是新 family 的原输入完整重算，`family_complete` 仅表示该有限资产 family；即使它为 true，原 Global 若还存在原候选 UNKNOWN / 其他 Full family 未覆盖，整体仍 UNKNOWN。`covered_full_policy_ids` 仅在整个本 family 来源和分母完成时返回；没有全局 COMPLETE 标记。

原输入复用仅限单调用当前 RRRO Session；每次新调用重新读取，恢复外层 DecisionCapture 后合并实际 sources/policies，不覆盖原 inputs，不缓存授权。原已存在单产品 MVP HTTP 执行入口仍是独立真实能力，新增 whole-confirmation 入口没有撤销它。因此 `replaces_original_candidate_keys=[]`，原单产品签名保留；307 同 Goal nominal→dynamic 是已显式批准的不同替换规则。如果产品要求 FullAsset 独占旧 MVP scope，须先实现明确 lifecycle/scope 约束，不能观察器暗自删除原能力。

Root 接线最小差量：新 Full Global 输入字段保存 `AssetFamilyInput` 原件，严格核其 base 与同一原 FullGlobal base 全字节相同；新的 pure version 显式调用 derive_asset_family；仅完整 family 对应原 FullPolicy IDs 可移除 unsupported 标签；完整候选分母加入本 family 项，重新统计全部 UNKNOWN / 未支持项及完整经济签名。旧 full-v1 API、父链、hash 与算法 dispatch 保留。缺回拨/恢复等其他 family 不能由资产适配自动消除。

## 检查状态

新增四 Python 源严格 mypy、Ruff PASS，直接风险 **22 PASS / 167.90s**；实际候选只 collection 1 node / 4.34s，没有运行 PG。当前源不再改。synthetic fixtures/doubles 仅程序风险证据，任何成功均不表示实际银行能力、正式 corpus 或真人确认。

| 真实检查 | 状态 | 原证据 |
| --- | --- | --- |
| 首直接风险 | FAILED，16 PASS / 2 FAIL，110.19s；General FIXED_LADDER 原拒因与 fixture 选错 nullable basis | `W3/asset-family-first-direct-20261006T001032Z-e96c3df4` |
| 首 scoped 类型 | FAILED，fixture UUID/目录 Binding 局部变量同名，共 5 错 | `W3/asset-family-first-types-20261006T001033Z-02dc992f` |
| 首 scoped Ruff | FAILED，原长字段行超过 100 字符 | `W3/asset-family-first-static-20261006T001033Z-b84ea8f4` |
| 修正后直接风险 | 22 PASS / 167.90s | `W3/asset-family-final-direct-20261006T001643Z-7a3c4ba0` |
| 四源 strict mypy | PASS | `W3/asset-family-final-types-20261006T001643Z-c574c062` |
| 四源 Ruff | PASS | `W3/asset-family-final-static-20261006T001644Z-c2021ac6` |
| 实际 PG 候选 collection | 1 collected / 4.34s，只收集 | `W3/asset-family-final-pg-collection-20261006T001644Z-8dd69318` |

全部原 FAILED 不改；修正前原源存 `.runtime/FULL-204-asset/before-first-direct-type-correction-20261006T0016Z`。更早未冻结直接 mypy 11 错记录在 `first-strict-20261006T0003Z`；不将并行改源的首直接命令当 frozen 证据。FIXED_LADDER 没有真实 Goal 时，只有原完整 typed 输入重放确切 `FIXED_LADDER_REQUIRES_AN_ACTUAL_GOAL` 才 EXCLUDED，未知输入不会通用排除。

## 未覆盖与真实下一依赖

独立 family 尚未接入新全局算法/API/507 notification；实际 PG 未运行；有限最多 16 个原生产者，库存容量沿旧 v1，超容量 UNKNOWN，不裁剪后宣布完成。Root 实际 v1 诊断已证明旧 required inventory 含不存在的 `simulated_bank_ledger_heads`，200 行 evidence/512KiB 门也不足实际原件；本包没有伪造该表，也不删除旧失败/原限额，因此实际 complete 沿旧结构仍 UNKNOWN。新增唯一 actual candidate 只验证具体真实目标组合与全部物理表零写，不声称 family/global 完整；需要新显式 actual-action-set-v2 真实物理表/完整计数/更高显式容量合同后才能真实全集完成。

缺实际共同保护或目录源、未支持资产类别、未知未来账户扣款、无真实 MVP 权限、未决银行占用仍拒绝/UNKNOWN。回拨、FullRecovery、Full周期付款和 Full全局联合调度仍需各自真实 preview/input consumer，不能只移除其标签。本包不关闭 FULL-204。
