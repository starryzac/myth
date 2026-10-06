# MVP-503 原件读取 V2 显式执行修订

状态：PURE_TOOL_CANDIDATE / FORMAL_24_BY_5_NOT_RUN。本修订允许原指标脚本读取真正的 Corpus V2 冻结档案；没有制造原 V1 登记包装，没有改变 Case INPUT、五臂 RULE、全局 SOURCE/DESIGN/ORACLE 的原字节或五个运行 SHA。它不证明金融结果，也不关闭 MVP-503。

## 实际入口与职责

`scripts.mvp_observations.Bundle(run_manifest_path)`、`scripts.mvp_trace_metrics.TraceBundle(run_manifest_path)`、`scripts.mvp_financial_metrics.FinanceBundle(run_manifest_path)` 均接受新的 `protocol=mvp-observation-run-v2`。三个原 CLI 的 `--run <明确原件路径> --output <全新路径>` 保持可用，输出以 exclusive create 保存。原 V1 协议、原 registration 门、原 64 MiB 文件上限继续保留。

`scripts.mvp_observation_v2.FrozenOriginalView` 只读冻结档案和当前源码，不导入 NativeAdapter、P 的金融判定/规划/执行或银行/数据库。可信外层必须在每次实际运行前调用 `scripts.mvp_corpus_v2.prepare_frozen_case`，真实重验全部 Corpus/native schema/readonly DAG，并保存其原返回。指标 helper 重核这份原返回与实际完整字节图；它不会把一个 status 字符串变成服务执行成功。

外部已登记的 freeze manifest SHA 是信任锚。指标读取器不提供数字签名，不能独立认证第一次捕获前 collector 是否漏行，也不能鉴别同时伪造全部原件及新信任锚的行为。实际 producer、上下文和原件归档由可信外层提供。

## 当次 run manifest

以下是字段 schema，尖括号表示 producer 必须填入实际原件值；它不是可执行的成功样本。

```json
{
  "protocol": "mvp-observation-run-v2",
  "experiment_run_id": "<实际 UUID>",
  "case_id": "<原 case_id>",
  "arm_id": "P",
  "execution_mode": "SERVICE_INTEGRATION",
  "input_sha256": "<整个原 Case INPUT byte SHA>",
  "oracle_sha256": "<原 ORACLE byte SHA>",
  "design_sha256": "<全局原 DESIGN byte SHA>",
  "rule_sha256": "<本臂原 RULE byte SHA>",
  "source_sha256": "<全局原 SOURCE byte SHA>",
  "seed_version": "mvp-301-v6",
  "isolated_db_epoch": "<实际 epoch UUID>",
  "purpose": "MVP_FROZEN",
  "user_id": "<实际 owner UUID>",
  "run_status": "<NOT_RUN|RUNNING|COMPLETE|FAILED|NOT_IMPLEMENTED>",
  "isolated_database": "bf_test_<实际32位hex>",
  "frozen_archive": {
    "manifest_path": "<实际 freeze manifest 的明确绝对路径>",
    "manifest_sha256": "<已登记 freeze manifest 原 byte SHA>"
  },
  "artifact_refs": {
    "input": {"path": "<原 draft 的 input_ref.path>", "sha256": "<同 input_sha256>"},
    "oracle": {"path": "<原 draft 的 oracle_ref.path>", "sha256": "<同 oracle_sha256>"},
    "design": {"path": "<原 draft 的 design_ref.path>", "sha256": "<同 design_sha256>"},
    "rule": {"path": "<原 draft 的 rule_refs.P.path>", "sha256": "<同 rule_sha256>"},
    "source": {"path": "<原 draft 的 source_ref.path>", "sha256": "<同 source_sha256>"}
  },
  "trusted_outer_validation_ref": {"path": "raw/pre-run.json", "sha256": "<实际原件SHA>"},
  "raw_refs": [
    {"path": "raw/pre-run.json", "sha256": "<同上>", "kind": "FROZEN_RUNTIME_REVALIDATION"}
  ]
}
```

十三项绑定精确为上述 experiment_run_id/case_id/arm_id/execution_mode/input_sha256/oracle_sha256/design_sha256/rule_sha256/source_sha256/seed_version/isolated_db_epoch/purpose/user_id。每份 raw 原件必须有完全相同的 bindings；不能给全局 SOURCE 添加 case_id 或 FROZEN 标签来凑旧门。实际正式 INPUT 从作者原件起就须有正式 purpose；DEVELOPMENT/TOOL_TEST_ONLY 不能升级。

`artifact_refs` 必须逐字等于实际冻结 Draft 中对应的 `{path,sha256}`。这些 path 是冻结前的逻辑原件名，通过实际 freeze `files[].original_artifact_relative_path` 定位 retained bytes；不能把它改成临时拷贝名。当前源码则由 `files[].current_source_relative_path` 映射。全局 SOURCE.files 保留原当前 repo-relative path，没有变成 archive path。

## 当次可信外层原返回

保存新的 `mvp-raw-observation-v2` 原捕获文件：

```text
protocol = mvp-raw-observation-v2
kind = FROZEN_RUNTIME_REVALIDATION
bindings = 上述完整13项
payload.result = prepare_frozen_case 的真实、未修改完整返回
```

读取器必须看到该文件也登记在 raw_refs；返回必须保留 `bounded-funds-frozen-runtime-input-v2`、`RUNTIME_INPUT_REVALIDATED_NOT_EXECUTED`、原 case/purpose/manifest SHA/whole INPUT SHA/validated execution SHA/完整 source_inventory。真实执行是否完成另由实际 Scenario result、银行、应用投影、actor、monotonic 和审计原件判断。

whole INPUT SHA 通过 `ORIGINAL_CASE_INPUT_SHA_BINDING_V1` 注入执行片段的 frozen_case_sha256，原 Case INPUT 不修改。原 execution fragment、converted fragment、typed validated fragment 三个 SHA 分开核，不能把 normalized/fragment SHA 充当 whole INPUT SHA。

## 字节图、类型运行时与预算

- freeze 必须为原 `bounded-funds-corpus-freeze-v2`、正式 purpose、`IMMUTABLE_CORPUS_BYTES_REGISTERED`。逐原件 size_bytes/hash 重核；目录文件清单必须精确相等，额外文件/目录、丢失、symlink、别名、遗漏当前生产/迁移源码均拒绝。
- 逐 SOURCE 当前文件完整 bytes 等于 archived bytes；当前 API/迁移/config/lock 和六个只读脚本均须在完整原 source_inventory。实际 readonly registration/source archives 和 14 calculator slots 精确绑定；显式模式必须为 `REGISTERED_READONLY_SYMBOL_CAPABILITY_DAG_V1`，不静默回退旧 stdlib 模式。
- 注册的五个 Pydantic typing distributions、Python 版本、uv.lock 版本和逐 runtime file 的原字节/大小/SHA 重新核；不借 module 已加载或旧缓存授予权限。完整 native/DAG AST 语义由可信外层每次原 verifier 重验。
- 每个 V2 原文件最多 **512 MiB**；V1 仍 **64 MiB**。超限拒绝，不截断历史。V2 在这一通道支持原 RAW v1/v2 capture envelope，十三项绑定仍精确校验；它不会将旧原件改成新 purpose。
- 原件 graph 中每个 exact `{path,sha256}` descriptor 必须能由实际 freeze logical artifact 或 current source archive解析，逐 bytes 重新核。调用结束 fresh 重核全部已读取 bytes。仅调用内的解析复用允许，不存在跨请求授权缓存。

## 实际 14 指标 producer 接缝

原 ORACLE 对象可以包含原 `trace_metrics` / `financial_metrics` 嵌套选择合同；它们是作者在首次 freeze 前明确定义的观测选择，不是 `mvp-observation-registration-v1` 代替整个 ORACLE。既有 `mvp-trace-registration-v1` 和 `mvp-financial-registration-v1` 内层公式合同保持，V2 的源定位直接从 FrozenOriginalView 取得，不需要伪造 `audit_verifier_sources` / `calculator_sources` archive paths。

实际原捕获需要登记如下 kind；各内层字段及严格 source_refs 沿用 [金融合同](mvp-financial-metrics-contract.md)、[独立事实合同](mvp-financial-oracles-contract.md) 和现有 trace 工具合同。每个数组必须来自实际 complete capture，不能从结果反推机会或造空数组成功。

| 组 | 原件 | 必须在首次 freeze 前的选择/分母 |
|---|---|---|
| E2/E5 | ACTOR_LOG、TIMING_LOG、原 STEP_LOG refs；原 BANK_SNAPSHOT 供回执与账本观察 | actor_event_ids、decision/recovery_opportunity_ids |
| A1/A2 | TRACE_SNAPSHOT、HTTP_EXCHANGES、TRACE_RECORDS | action_requirements、decision_opportunities |
| A3/A4 | AUDIT_ORIGINALS、ERROR_RECORDS、TRACE_RECORDS、实际 source/stack/step原件 | audit_checkpoints、failure_opportunities、原独立 cause |
| S1—S5/E1/E3/E4 | FINANCIAL_BASIS、FINANCIAL_FACTS、FINANCIAL_OBSERVATIONS、ASK_LOG | protection/due/deployment/ask checkpoints、safe_auto/version consumption 全机会 |

原 basis/facts 保留全部必需表、时间、原 policy/audit/source/bank/ownership/income 身份。多个业务观察点需各自真实 facts/basis，不能用最终余额回填过去。独立公式未支持、原件缺失、实际尚未运行时逐项 MISSING/NOT_RUN；零分母 NOT_APPLICABLE。原完整 24×5 金融与 14 指标仍 NOT_RUN。

## FINANCIAL_FACTS V2 与 typed 政策原时轴

`protocol=mvp-financial-facts-v2` 明确采用 512 MiB embedded artifact 上限。V1 facts 仍 64 MiB；V1 run 不接受 V2 facts。这是读取容量修订，没有修改保存的原文件或减少行分母。

实际 collector 保存 `FINANCIAL_BASIS` 封套：`payload.tables` 为原完整业务表；另保留实际 audit_events/audit_subject_snapshots（可以就在 tables 内）。随后 FINANCIAL_FACTS.payload.facts 包含以下字段，source_ref 的 pointer 以实际保存位置为准：

```text
protocol = mvp-financial-facts-v2
user_id / timezone / as_of / complete = 实际采集身份与时刻
tables / inventory / artifact_originals / expense_history / income_payload = 原金融合同
policy_state_events_protocol = TYPED_AUDIT_POLICY_EVENTS_V1
policy_state_events = 原完整 audit_events ORM rows（含 canonical_text）
policy_state_events_source_ref = 原 BASIS 完整 audit_events 数组的 source_ref
policy_subjects = 原完整 audit_subject_snapshots ORM rows（含 canonical_text/snapshot_hash）
policy_subjects_source_ref = 原 BASIS 完整 audit_subject_snapshots 数组的 source_ref
```

此路径不让 collector 提供 from_status/to_status。独立 stdlib reader 从原 POLICY_STATE_CHANGED 的 changes 及实际 BEFORE/AFTER POLICY 和 BASIS POLICY_VERSION 引用解释状态；逐 canonical JSON、原 event/subject namespace hash、owner/epoch/identity/version、完整数组、时刻及状态两侧哈希核验。POLICY_VERSION_CONFIRMED 在没有同版本同时刻状态变化时保留原 AFTER policy 和 exact version activation：首次捕获的 previous status 为 null，明确没有推断 PROPOSED；后续未变化状态使用实际完整已观察历史。没有原激活事件或原 subject 时仍 MISSING。

这里检查政策原件及状态解释，不替代 A3 的完整 epoch/checkpoint/链连接验证。源 Namespace hash 只读取、重新校验原 bytes，不改写任何历史 hash。真实 CASH_MANAGEMENT/FIXED_DEPOSIT 账户类型允许作为非现金账户读取；其展示余额、本金及虚拟腿不能计入 actual cash。

V2 A4 causal source locator 的 path 和 original_path 都保留实际 repo-relative current source 名，SHA 必须等于实际冻结 SOURCE；读取器通过原 manifest 定位 retained bytes，不能让 producer 另造 archive path。line/line_text/symbol 和源 AST 区间仍严格验证。原 V1 path 规则保留。2026-10-05 的显式后续修订增加下述原异常步骤路径；此前未接入版本 bytes 留在 Stage A checkpoint，不更名为成功。

## V2 A4 实际 native 异常步骤修订

`SCENARIO_RESULT.payload.result` 必须为实际未修改的完整 `bounded-funds-scenario-v1` 返回，其 case/purpose/typed execution INPUT SHA 与冻结原件精确相同。原 steps 的 id/kind/aware at 必须为冻结 validated execution INPUT.steps 的顺序前缀。条件调度的不同执行片段需要另行明确注册 source/input/fragment 桥；此工具不会静默把不同 fragment 的 SHA 当作整个原执行片段。

原捕获另有 `payload.capture={complete:true,run_ref:<13绑定>,failed_step_ids:<实际全部error步骤>,error_record_ids:<全部SCENARIO_STEP错误原件ID>}`。匹配 expected_error 后继续且 Scenario status 为 EXECUTED 的实际拒绝仍是异常步骤，不能因外层返回成功而漏掉。没有 HTTP 时无需伪造空的 HTTP 成功原件；存在 HTTP 时原 HTTP 完整 inventory 门继续保留。

`TRACE_RECORDS.failures[]` 的 native 读取选择如下。这里的尖括号是 schema placeholder，不是成功样本：

```text
failure_channel = SCENARIO_STEP
scenario_result_ref = 原 SCENARIO_RESULT 的 /payload/result exact descriptor
service_step_ref = 同一原件的 /payload/result/steps/<实际index> exact descriptor
step_index = 实际非负整数
first_failing_step / error_code = 原 row.step_id / row.error.code
error_ref / trace_ref = 原 ERROR_RECORDS 内完整错误/真实栈引用
source_ref = 冻结 original_path/path/sha256/line/line_text/symbol
logical_cause_key / cause_code = 实际原因原件与独立预登记原因
audit_checkpoint_id / audit_event_id / run_ref = 原完整审计与当次13项绑定
```

独立预登记的 failure opportunity 另须 `causal_step_ids` 完整真实因果步骤选择；reader 检查其中第一条实际 error，不能把后来失败当首因。原 ERROR_RECORDS.error.exchange_id 为 null，step/user/code/cause/run/occurred_at 精确绑定。原 trace 的 source 和真实 frames 最末叶必须是冻结 source；source 原 bytes、行内容及 AST 函数区间全部核验。

HTTP 2xx 的原 response_body 若是同一完整 ScenarioResult 且确有 error 步骤，可用 `business_step_ref=<原HTTP exchange>/response_body/steps/<index>` 加实际 `step_index` 选择。实际 transport status 200 不改写；reader 只在调用内构造私有 inspection view，绝不保存伪 HTTP 4xx 原件。ActionResponse.UNKNOWN、RecoveryRunResponse.RECONCILIATION_REQUIRED 等正常状态对象本身不属于此异常路径，不能被当作另一场已定位失败。

默认仍要求完整 A3 验真及实际 causal audit event。仅在首次 freeze 的独立原因明确登记 `audit_cause_required=false`，且确为 INVALID_SCENARIO_STEP/INVALID_SCENARIO_INPUT 的原 422 DTO/input 拒绝、audit_event_id=null 时，可以没有业务 causal audit event；完整审计 checkpoint、原源行、真实栈、run/code/cause 门仍全部保留。其他错误不能借此豁免。

实际 RUN_RECOVERY 的投影中断在原 bank commit 和 PROJECTION_FAILED 原 audit 后抛出原错误，可走此异常步骤路径；随后 READ_RECOVERY 读取 RECONCILIATION_REQUIRED 是同一历史状态，不重复计为新失败。未捕获真实 except 栈/cause、缺完整 failure inventory、不同 fragment 调度桥或不支持的返回形式仍 MISSING。不能由 expected 字段生成实际错误栈。

43 个新增纯风险例覆盖 identity/input/clock/order/first cause/capture denominator，以及源行、symbol、stack、trace、run、audit 缺失或篡改；HTTP200 路径同样核声明的 native 顺序/clock 和首因。纯 reader doubles 明示 TOOL_ONLY；完整冻结验证和完整 A3 链验证分别由对应原风险套件检查。这不是实际 24×5、银行、金融效果或生产异常捕获证明。

## 后续公式的具体未覆盖项

已接入的是原件 V2 读取、typed 政策状态与 A4 上述异常通道。以下不得凭新文件或纯测试标为初版完成：

| 原项 | 当前仍缺的独立能力 |
|---|---|
| S2 | GOAL exact ownership/source、ALLOCATE_GOAL 月限额/target、PAY_RECURRING 真实 payee/occurrence/paid-so-far 权限；超自动限额的人工 asset scope；历史失效确认需当时原证据 |
| E1 | PURCHASE 全部实际 settled+income-location+应用 projection；GOAL/income projection；独立安全点选择与无逐动作 actor/确认观察 |
| S1/E4 | 不同 as_of 的准确独立因果时间线、GOAL scope 保护/可部署；planned-principal-return 产品独立 candidate exit selection，不能借 P effect 判 safe |
| A4 | 实际 source/stack/cause producer；条件执行 fragment 的原件桥；非异常业务状态的独立原因支持 |

每个正式时点必须有当时实际 facts/basis；不能用最后余额回填过去。未尝试机会、真实故障、缺原件或未支持族保留完整分母和 MISSING。正式 24×5、全部14数值和初版全量目前 NOT_RUN。

## 当前纯验证

45 locator 纯风险例已 PASS：`docs/progress/evidence/W1/observation-v2-locators-tool-only-pure-20261005T102046Z-ad9e871d/`。原只读 DAG 的实际 14 入口重建及拒绝门共 85 pure PASS：`docs/progress/evidence/W1/observation-v2-readonly-DAG-tool-only-pure-20261005T102147Z-992a78a1/`。6 文件 strict types PASS：`docs/progress/evidence/W1/observation-v2-locators-tool-only-types-final-20261005T102148Z-9dfe6490/`。第一轮 duplicate module 类型失败和后续测试夹具注解失败原件仍保留；这不是 PG、浏览器、全量或正式效果实证。

Stage B typed 政策原状态新增21加旧84 oracle纯例共105 PASS：`docs/progress/evidence/W1/financial-v2-typed-state-tool-only-pure-20261005T104403Z-0e968419/`。新增 native A4（当时37例）与旧直接相关 trace/financial/typed风险共161 PASS：`docs/progress/evidence/W1/observation-v2-native-failure-and-old-related-tool-only-pure-20261005T110231Z-1b27cf1b/`。之后只修测试类型注解、共用 HTTP200/native 步骤核验和增加6个 HTTP200负例；最终43 PASS 原件：`docs/progress/evidence/W1/observation-v2-final-native-and-HTTP200-tool-only-pure-20261005T110939Z-0548c042/`。最终7 strict types、9 Ruff/format及实际14 DAG重建分别在 `observation-v2-final-shared-locator-tool-only-{types,static,format}` 和 `observation-v2-final-shared-locator-actual-fourteen-DAG-tool-only-pure` 明确原目录内。19个测试类型注解错误与 import-sort失败保留在对应20261005T11023*原件，未覆写为PASS。
