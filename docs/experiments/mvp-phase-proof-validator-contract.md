# W1_PHASE_PROOF_VALIDATOR_INSTALLED_V2

已安装 `scripts.mvp_phase_proof_validator`，仅stdlib只读原文件，无DB/HTTP/app/金融判断导入。先核并归档冻结候选manifest及两源码，原56项合成风险保持；新增注册V2缺口显式MISSING测试。安装不会升级任何原raw False或经济等级，结果protocol保留原 `phase-independent-validator-candidate-v2` 数据合同。

调用接口：

```python
from pathlib import Path
from scripts.mvp_phase_proof_validator import validate_capture

derived = validate_capture(
    actual_returned_capture,        # 原函数实际返回封套；不可伪造目录推断成返回值
    original_thirteen_bindings,
    original_registry_descriptor, # {path: absolute, sha256: original byte SHA}
    workspace=Path(actual_workspace),
    actual_run_ref=None,            # 当前尚无可信run→Capture attestation adapter
)
```

每调用fresh读取完整原件与current source，不缓存源或授权。核原V1 registry及五原件完整byteSHA/十三bindings/生成bf_test、全部API runtime与原三个工具源归档和当前字节；V2 phase producer原完整execute封套、每raw byteSHA/strict JSON/pointer/valueSHA/service_call_id；BEGIN/USER_LOCK/BEFORE_OUTER_COMMIT/terminal完整顺序、完整xid8/PID/时钟、outer flags及独立NullPool RR/RO probe、精确pg_xact_status SQL/参数和committed/aborted。核7局部表+ownedUser inventory/owner/action/query scope、request/effect原canonical hash及bank/receipt/posting refs。局部八表不是完整23表或financial facts底座；不计算费用、保护、权限、目标真实份额或完整账本连续性。

支持三phase committed和bank committed/projection aborted的完整原图。缺独立probe/原件/source/clock/阶段，历史重读/无银行/部分阶段/未知能力/expired/in-progress显式MISSING或INVALID。完整原件内部一致时仅 `transaction_artifacts_verified=true`，状态为 `TRANSACTION_ARTIFACTS_CONSISTENT_ACTUAL_RUN_BINDING_MISSING`；`actual_runtime_evidence_verified=false`、`economic_execution_verified=false`、`execution_mode=null` 永远保留。另给PASSED文件也不提升，`actual_run_ref`只读文件并报告adapter未实现。输出是独立derived值，不能回写旧raw或record_execution原字段。

当前source/clock注册消费者支持provider registry V1。新V2 Case/global SOURCE/FrozenDraft/每Step root_registration_ref桥需要独立精确适配；检测到 `mvp-arm-provider-registry-v2` 返回 `MISSING / REGISTRY_V2_SOURCE_CLOCK_ADAPTER_NOT_IMPLEMENTED`，不能包装为V1。无typed完整EconomicEffect schema、没有可信actual driver attestation，也未接经济oracle。此工具不会把旧DROP库或旧SOURCE在新源下提升证明。

实际安装后57 TOOL_TEST_ONLY纯测试PASS20.32s、strict mypy2/Ruff/format PASS。原56合成源码/原件/行与追加注册缺口测试都不是产品/SQL/金融证据，fixtures仅新 `.runtime/W1-phase-validator-tool-fixtures/<UUID>`。当前安装不重新读库、不重跑PG，不重复旧三实际节点；旧候选当时只读保留原件的结论仍在原freeze中，当前源变化后须新注册/新运行。

未覆盖：真实run→完整不可覆盖original目录与returned Capture绑定；V2 source/clock adapter；no-bank failure完整封套和idempotent/历史未到phase分类；完整typed effect；独立经济oracle；实际24×5与14指标全量。root后续driver负责这些真实来源，缺失不得用stage标签、空数组或success字符串填补。
