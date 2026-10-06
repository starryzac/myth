# FULL-204/507 actual-v2 原全局来源通知差量

仅新增两份测试和本记录。生产新算法接线由根负责：本作者不改 frozen actual-v2、旧 v1/full-v1、Main/dependencies/shared、原负例，不执行 PostgreSQL/金融/浏览器。状态：**22 个直接风险 PASS；真实 HTTP 候选 NOT_RUN**。没有把程序 doubles 当银行、真人阅读或实际通知证据；FULL-204/507 原全量验收未关闭。

## 原来源与实际接线入口

可用冻结 fixture `app.tests.test_full_action_set_boundary_actual.fixture()/trace_for()` 为 TOOL_ONLY 完整原件；新 `app.tests.test_global_intervention_actual_source.source_originals(kind='numeric'|'crossed')->Originals` 返回 value/newtypedMessage/inputs/完整父子trace。numeric 只改变原非经济 metadata；crossed 实际按 synthetic raw SUSPENDED→原 typed拒绝重算使动作集合消失。两者不是把预先声明 signature 改成成功。

实际 source helper `app.services.full_action_set_boundary_actual.actual_global_boundary_intervention_source(session,user,run_id,now)` 经原完整 trace/audit、父链与新 typed输入重算，产生原 `FullIntervention.Source`。当前 getter必须 `read_current_actual_action_set(session,user,now)`；原算法 literal **full-policy-action-set-boundary-actual-v2**、scope POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2。Root `_global_boundary_source` / `_global_source_status` 精确分派，不能调用 legacy/full-v1去验新对象。当前来源不完整、owner/epoch/version/scope异常返回 UNKNOWN；真正不同原动作签名是 STALE。

Root `InterventionMessage` 新 union第三类 `ActualGlobalBoundaryObservation` 保留完整原 `boundary_observation`、source_run_id/source_trace_hash/semantic_key、owner/epoch/creation_command_run_id。Global消息只在原完整 snapshot/status/signature/父比较/attention一致时成立，`bank_authority=false / execution_eligible=false / answers_question=false`；不把“完整数学来源”当金融授权。

`postcommit_global_observation(engine,user,value,now,response)` 同新 typed第三约束；仅实际完整 BoundaryCrossed + requires_user_attention + 非 replay 才调用 producer一次。numeric/initial/unknown/incomplete/replay 不生产；producer异常只给 X-Global-Intervention-Status=SOURCE_UNVERIFIED，返回完全原 value，不能把已提交原观察改失败或金融成功。未知原来源不调用 observe_intervention。

Root公开原路由：`GET /api/v1/boundary/actual-action-set/current`，`POST .../observe`，`GET .../observations/{run_id}`；GET RR READ ONLY。原通知 `/api/v1/interventions/observe`、`/{id}/deliveries`、`/{id}/acknowledgements` 和 `/commands/{epoch}/by-key/{key}` 不增金额/时钟/facts/权限字段。原 complete basis与不可变 JSON/父轨迹仍由精确actual版本消费者检查；不以旧 global helper成功补新证据。

## 必要直接风险及原检查

Root SOURCE_READY 后运行，源scope包括新两test + Root domain/full_intervention、services/full_intervention、api/global_boundary_postcommit、services/global_boundary_intervention_producer、冻结actual service和Rootpostcommit后的actual API；没有在Root WIP上取通过。

| 范围 | 实际结果 | 证据目录 |
| --- | --- | --- |
| 新来源/typedMessage/GET/postcommit直接风险 | **22 PASS，13.83s** | W3/actual-global-notification-new-version-first-direct-20261006T004423Z-3f9c481b |
| 新两文件 strict mypy | PASS | W3/actual-global-notification-new-version-first-types-20261006T004423Z-04d2f748 |
| 新两文件 Ruff | PASS | W3/actual-global-notification-new-version-first-static-20261006T004424Z-2b7e8f6e |
| 新两文件 format-check | PASS | W3/actual-global-notification-two-tests-format-20261006T004525Z-535b22ff |
| 新 HTTP PG 候选 collection | 1 node，4.24s；**未运行** | W3/actual-global-notification-http-candidate-collection-only-20261006T004424Z-177b1cae |

风险保留：真实pure原hash/父链新version helper仅新current getter（legacy/full函数均trap）；missing/incomplete/audit/unknown-algorithm/missing-parent不能降格；current owner/epoch/version/scope/source signature异常UNKNOWN、真实集合变动STALE；typed消息禁止旧version重标签、grant=true、假complete/attention/source/owner、额外bank success字段；original postcommit crossing一次与initial/numeric/replay/unknown零调用；观察原receipt在通知异常后原样保留，未知source绝不写通知。所有 source before/after/current SHA以及全仓是否稳定由最终manifest逐项登记，不用scope代替global。

## 根唯一真实 HTTP 候选与未覆盖

新增独立文件 `apps/api/app/tests/test_global_intervention_actual_source_integration.py`，唯一节点 `test_actual_v2_http_crossing_original_notification_delivery_ack_and_zero_money_writes`。

候选通过真实 FullGoalModel生产入口、原明确Goal/MVP确认、真实 ExternalFact INCOME并核银行SETTLED/projected；不写seed/bankproof不造来源。初次完整 dynamic20000 原观察不给通知；第二笔不同收入原金额不变仍Observed且Outbox/Inbox 0；真实原 suspend 后 exactactualCrossed HTTP postcommit只有1 Outbox/0Inbox。原observe同body/key重放不新增，原完整 source trace/audit可读。手动原通知observe按语义合并、原key与原payload/trace绑定不变；unknown grants/facts/time和错误hash仍422/409，负例未删。

随后通过原真实 deliveries 两次仅首次present_once，同inbox；明确原 payload/hash ACK与by-key恢复，再新 create_app/新 TestClient只读重开验证原ACK/claim，全部金融physical tables逐原行相同；最终独立RRRO原EXACT审计VALID。source/body/notification都固定用户/epoch/原完整比较；未知不去legacy。metadataappend明确不当金融效果；delivery/ACK仅原模拟服务状态，不能证明真人看见。

本节点目前只是 collection，Root实际唯一金融链运行后再追加原manifest，不预报结果或耗时。actual-v2真实全来源、通知生命周期、真实重启/响应故障与所有family、容量/时延测量、前端、完整版验收仍依各自原分母；新源码没有替原原失败改标成功。
