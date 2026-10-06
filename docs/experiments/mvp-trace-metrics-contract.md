# MVP-503 A1—A4 原件观察合同

状态：TOOL_IMPLEMENTED / FORMAL_CASES_NOT_RUN。指标原名来自 `docs/spec/mvp-metrics.json: A1—A4`，分别为动作可追溯率、决策所需证据完整率、审计链验证成功率、失败原因是否可定位。这里只交付读取原件的计算工具；没有实际 case×arm 观察，没有关闭 MVP-503，没有金融效果、真人研究或性能结论。合成夹具全部为 TOOL_TEST_ONLY，结果恒带 `financial_effect_evidence=false`。

调用方式：

```powershell
.venv/Scripts/python.exe -m scripts.mvp_trace_metrics --run <明确run目录>/run.json --output <新文件>.json
```

工具不连接数据库、不执行或确认金融动作、不调用 P 的 boundary、autonomy、recovery 或 execution 判断。输出只用 exclusive-create；同名文件已存在就拒绝，没有覆盖输入、失败历史或旧结果的路径。

## 运行与冻结绑定

复用已冻结 `scripts/mvp_observations.py` 的 Bundle 原件读取合同，不修改该文件。run、registration、raw 协议仍分别为 `mvp-observation-run-v1`、`mvp-observation-registration-v1`、`mvp-raw-observation-v1`。每份原 raw 文件严格绑定完整 experiment_run_id/case_id/arm_id/execution_mode/input_sha256/oracle_sha256/design_sha256/rule_sha256/source_sha256/seed_version/isolated_db_epoch/purpose/user_id。input/oracle/design/rule/source 五份原件及归档源码必须实际存在、逐 byte 校验 hash；MVP_FROZEN、FULL_FAMILY_FROZEN 的五份 registration 必须均为 FROZEN。TOOL_TEST_ONLY 的 purpose/mode 必须同时出现，不能改标签充当真实运行。

`oracle.trace_metrics` 必须提供 `protocol=mvp-trace-registration-v1`、带时区 `run_begin_at/run_end_at`，以及显式的 action_requirements/decision_opportunities/audit_checkpoints/failure_opportunities 数组。时刻来自预登记模拟业务窗口，不是性能时间。各 opportunity/checkpoint identity 唯一。工具不从 P 的成功项、trace.sources 或 autonomy 字符串反推独立分母。

每个具体原件引用严格为 `{artifact_sha256,json_pointer,value_sha256}`。第一项校验完整 raw 文件 bytes；pointer 只指向已绑定原 raw 的 `/payload/...` 内容，列表 index 必须无前导零；最后一项使用原 configuration JSON 编码（UTF-8、ensure_ascii=false、key 排序、紧凑分隔、不允许非有限数）校验原 value。JSON 重复 key、歧义 artifact digest、目录逃逸、重绑原 row/table/ID 和读取期间 bytes 漂移均拒绝。单份原文件上限 64 MiB。缺少已登记 raw 文件会明确列在 missing_originals，保留可验证预登记分母；缺注册/源码、hash 不符或非法全局绑定拒绝整个调用，不发布可测结果。

## 原 raw 数据

| kind | 原 payload |
|---|---|
| TRACE_SNAPSHOT | role、captured_at、coverage、tables；tables 是原业务行完整 JSON，包含 id/user_id，原 nullable 字段不得猜默认值 |
| TRACE_RECORDS | actions/decisions/failures 三个显式数组；即使没有观察也必须显式为空，并保留预登记机会 |
| AUDIT_ORIGINALS | checkpoints 数组；每项有 checkpoint_id、originals、verification_ref |
| HTTP_EXCHANGES | exchanges 数组；原请求/响应/错误结果；capture 为完整 run_ref、complete=true、所有 exchange_ids/error_record_ids 的原采集清单 |
| ERROR_RECORDS | errors 原错误数组及 traces 原因果轨迹；完整 owner/run/time/source/step/cause 绑定 |

原 row 引用限定为 TRACE_SNAPSHOT 的 `/payload/tables/<原表>/<index>`，不会把 oracle 自述或 HTTP success 字符串当业务原行。普通表严格同 owner；users 的原 id 必须为 owner；只有 typed GLOBAL_CATALOG 的 asset_products 可无 user_id。此例外不适用于金融账户、版本、证据、银行操作或回执。

## A1：动作可追溯率

分母要求一个 FINAL 原 snapshot：coverage 对当次 user/epoch 完整声明 action_plans、bank_operations、action_receipts、simulated_bank_redemptions 及全部 EXECUTE_ACTION HTTP id；capture 不能早于 run_end。每张表原 row id 去重。所有银行动作/回执/赎回 action_plan_id、原 HTTP 执行 action_id，以及 SUBMITTED/SUCCEEDED/FAILED/UNKNOWN/RECONCILED 的原动作行进入逻辑 action_id 集合；同动作重试只计一次，缺原 action 或缺 trace 不会移出分母。单独状态行只能使缺证据动作可见，不能建立分子。缺完整 inventory 时 A1=MISSING/null，已有观察只作为已知部分列出。

每个 action 观察登记 action_id/opportunity_id/decision_opportunity_id/action_ref/decision_ref/bank_ref/version_refs/policy_consent_refs/action_consent_ref（若需本动作确认）/receipt_refs/audit_checkpoint_id。独立 action_requirements 为每个 opportunity 冻结 consent_mode=ACTION 或 POLICY；不会从 P autonomy 猜是否需要询问。

现代 `request.execution` 支持完整 32-field economic effect 原 JSON 和完整 BankCommand。原 action request hash、排序 cash_uses/income_uses/version IDs/liability evidence 的 effect hash、原 bank request hash必须一致；effect/action/bank 的 operation_id、owner、类型、金额、源账户、目的地、目标、产品、版本、幂等键、business_key 均核对。准备决定→原 action created_at→bank requested_at，以及 effect 有效窗、available_at、receipt occurred_at 必须是合法原时序。

原 decision input_snapshot、snapshot_hash、完整 trace_hash/input_hash、run/owner/as_of 必须匹配。该 action 的 A2 必须实际通过，并绑定同一 decision_run_id。effect、decision 和原版本引用全集相等。每个版本的 configuration/content_hash、confirmation 与 USER_CONFIRMED_POLICY 原证据全文、owner、policy/version identity、reviewed_hash、明确 accepted=true 及时刻交叉核对。ACTION 模式还逐项核对原 UUID5 confirmation identity、action/effect hash、明确 affirmative consent、原授权时刻、observed_at、valid_from/valid_to 和有效终点。POLICY 模式必须有真实原版本及确认原件。

每个回执必须存在、同 owner/原 action、发生在请求之后、引用同一 bank operation。原 action、decision、版本、确认、所需事实证据、bank、receipts 必须逐行存在于已完整通过 A3 的原 subject 快照，且匹配同动作 typed audit event 的原 reference/snapshot_hash。完整 typed audit verifier同时核原银行和回执/经济腿关系。这是引用和数据完整性；不能用 A1=1 证明安全资金、授权金额上限、独立金融选择正确或收益。

legacy bank_request 动作尚无该独立 trace calculator，一律 MISSING/null；不会将旧 NO_LOSS 或成功 receipt 文本当完成。新的 effect schema 需升级合同及计算器；不能丢省略字段后重新 hash。

## A2：决策所需证据完整率

每个独立 decision_opportunity 提供 opportunity_id、consuming_at 和 required_evidence。每项原需求为 evidence_id/content_hash/source_type/source_ref/evidence_level/subject_fields。expected hash 和主体字段来自预登记独立 evidence manifest；不能复制 P 所列来源当 oracle。

原决策观察提供 decision_ref 与 evidence_refs，后者必须精确覆盖独立要求。原 evidence content hash、主体、来源、级别、owner、VALID 原状态及 observed_at/valid_from≤消费时刻<valid_to 校验；原 decision.evidence_ids 及 captured trace.sources 必须真正包含同原件、同内容和原 hash。未来、过期、UNKNOWN、错账户、错误 hash、不被决策消费的原件不进分子。未执行/失败机会留在全预登记分母。空 required_evidence 没有验证过的实质要求时返回 MISSING，不能以空数组生成 100%。

## A3：审计链验证成功率

独立 oracle.audit_checkpoints 预登记 checkpoint_id，未捕获者保留分母。每个 originals 必须包含原 head_text、event_texts、subject_texts、current_subject_texts、checkpoint_text、reference_manifest、current_business_refs，以及已 bound 原 HTTP 文件内的 verification_ref。原 subject/event/checkpoint 原 canonical text 交给 `app.domain.audit_chain` 的原 typed canonical verifier。该导入只验证数据完整性，不作为独立金融安全 oracle。

工具以 EXACT 模式重新调用 verify_epoch，限制 10000 events / 64 MiB，然后对照原完整 AuditVerification 全字段、实际 head/count/tail/epoch/checkpoint；必须 chain/reference/status 都为 VALID、checkpoint=VERIFIED、全部 sequence 实核、无 errors/截断，才可能进入分子。LEGACY/UNSUPPORTED/INCOMPLETE 不会成功，原 status=VALID 字符串不够。

reference_manifest 的 event 顺序/数量、所有 historical/current kind/id/hash 唯一全集逐项匹配；每个 historical identity 必须有完整 current 原件。current_business_refs 还必须引用同原 raw snapshot 中实际同 owner 原行，全文与当前 typed subject.data 一致。缺 current 原件仍 MISSING/null，不能利用原 verify_epoch 允许可选 current 检查的宽松路径获得成功。

oracle.audit_verifier_sources 精确登记当前整个 `apps/api/app/domain/*.py`、mvp_trace_metrics.py、已冻结 mvp_observations.py、pyproject.toml、uv.lock 的 original_path/path/sha256。每份必须在 source.files 原 byte archive 中，并与实际当前模块文件一致；清单新增/缺失/源码漂移使 A3=MISSING，不能把新 verifier 的结果套在旧运行源码上。已导入 domain 的原顶层函数还与归档源码仅编译、不执行得到的 code object 比较，拒绝磁盘已更新而进程内仍为旧 verifier/helper 函数的情况。入口采用上述独立新 CLI 进程；升级 typed schema 或运行源码后应启动新进程，不支持沿用旧 Python 进程中的 Pydantic class schema。每次调用重新校验，调用结束再核 bytes；没有跨请求授权缓存。该当前源码 guard 有意阻止历史源码漂移后直接宣称当年已实核；历史再现需对应版本的新工具/新结果，不改历史 hash。

## A4：失败原因是否可定位

每个 failure_opportunity 冻结 opportunity_id/logical_cause_key/first_failing_step/error_code/cause_code/source_ref。source_ref 包括 original_path/path/sha256/line/line_text/symbol，绑定归档源码实际 bytes。实际 failure 观察提供 failure_id/opportunity_id/logical_cause_key、原 http_ref/error_ref/trace_ref、首步骤/code/cause/source、audit_checkpoint_id/audit_event_id 及完整 run_ref。

原 HTTP ≥400 的失败响应、原错误日志、原 causal trace 必须指向同 exchange/failure/step/code/cause/user/run。按原 HTTP sequence 找同 logical cause 的首个失败步骤；源码精确行文本必须存在、落在唯一原 Python symbol 的 AST 行区间内；原 stack 最内层 frame 必须与 source_ref 相同。完整 typed audit 中所指原事件必须存在，其 reason_code/cause_ref 指向同预登记原因/原失败。非空 message、存在某个 symbol、只写一个 trace_id 或 audit_id 均不能 PASS。

分母是全部独立 planned logical causes，加原 HTTP、ERROR_RECORDS 和 actual trace 中的意外真实 logical causes；重试按原 logical_cause_key 只计一次。预登记缺 trace 但原同 cause 错误仍存在，保留一个缺失 planned unit，不重复加成意外失败。意外失败无冻结独立 oracle 时明确 MISSING，不能猜 cause 给分。HTTP/error complete inventory缺失或 id集合不符使 A4=MISSING。当前仅实现有原 HTTP 失败响应的定位，非 HTTP/无响应网络错误/HTTP 200 内业务失败定位尚未实现；仍保留预登记/原错误分母，不以消息补成功。

## 输出与未覆盖

每项保留原指标 status/value/numerator/denominator/applicable_units/missing_reason/raw_refs，并附 units/failures 的具体结果、失败原因和原引用。分子为已明确验证数；任何缺失 unit 使整体 MISSING/null，已知分子和全分母仍保留。已完整观察且失败的 unit 可明确为 FAILED/false，不与缺失混淆。零分母为 NOT_APPLICABLE/null；NOT_RUN/NOT_IMPLEMENTED 为 NOT_RUN/null。没有 S1—S5/E1—E5 计算器、实际 frozen case、真实 run adapter、原材料和实验测量时本工具不会创建这些成功项。

真实 adapter、原 HTTP/错误/stack 捕获、独立必需证据 manifest 和因果 oracle 尚待接入。完整性工具无法从本地文件证明采集者没有在导出之前遗漏数据；因此必须实际接入可信 run/export inventory，并保留失败原件。该边界不能用合成纯测试代替。

## 本轮原件保护和验证记录

开始实现前逐 byte 保存 observations/corpus 四份工具及测试原件至 `.runtime/W1-trace-metrics-source-originals-20261005T112255-ab568bb6/manifest.json`，副本 hash 与原交付 hash 全部一致。没有修改这四份文件或其已有 PASS/失败证明。

初轮 42 个 TOOL_TEST_ONLY 用例已实际通过；随后补上失败 cause 去重、原 ERROR_RECORDS 意外失败、完整采集清单、失败引用保留和显式 catalog scope 测试。最终验证结果、源码实际 SHA 和 scoped manifest 在下一节追加，不能将初轮结果冒充后来源码的验证。

### 最终实际证明（2026-10-05 03:55 UTC）

48 个 TOOL_TEST_ONLY 用例实际 PASS，49.80 秒；命令仅该新测试文件，`-p no:tmpdir -p no:cacheprovider` 使用新 workspace inherited-ACL 夹具。测试包括新、旧 loaded verifier 函数错配负例。Ruff 与严格 mypy 两文件分别通过。没有 PG、browser、全量、正式实验或真实金融接口调用。

三个最终 scoped manifest 的实际 prefix 同时覆盖新工具/测试与整个 `apps/api/app/domain/`（含 audit_chain.py/types）；全部 `all_source_stable=true`、`scoped_source_stable=true`、无源漂移：

- `docs/progress/evidence/W1/trace-tool-final-20261005T035345Z-b2195f7f/manifest.json`，实际执行 03:53:45—03:54:36 UTC。
- `docs/progress/evidence/W1/trace-tool-ruff-final-20261005T035534Z-ba54c928/manifest.json`。
- `docs/progress/evidence/W1/trace-tool-mypy-final-20261005T035534Z-6d9cca3f/manifest.json`。

该版本实际文件 SHA256：

| 原文件 | SHA256 |
|---|---|
| scripts/mvp_trace_metrics.py | `511c2f011c962f35b5bedc042b07aaf0d13f0b22bef5d40bc21d51bdec07caef` |
| scripts/tests/test_mvp_trace_metrics.py | `a42b7a843cb556f1c59c7e93394697d79543420a04204f05905be501502d44bb` |
| app/domain/audit_chain.py（未由本子任务修改） | `a25554905f2957ecb11b4e785f52b25b81057bdfe7fa1c56e4781151822639e0` |
| app/domain/audit_chain_types.py（未由本子任务修改） | `0741cc79df41a968f3e4fdbf756c120679af27a792b9921fec68c4eca46fcfbf` |

候选 47 PASS 的 `trace-tool-tests-candidate-20261005T034951Z-72a49e77/manifest.json` 原件仍保留；其 `all_source_stable=false`（父任务当时修改 audit_chain.py），因此不充当最终冻结源证明。初轮 42 PASS、候选 Ruff/mypy 和所有失败输出也保留，没有覆盖。现 observations/corpus 四份原文件 SHA 重新核对仍与开始前副本完全一致。
