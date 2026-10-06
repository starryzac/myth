# FULL-507：已提交 Question 的介入生产接缝

原编号状态：PENDING。本包仅生产 Question 通知及其当前观察来源证明，不授予资金权限、回答问题、领取收件或确认人已读。原 full_intervention/domain 消息/回执协议和 question_semantics 均保留。

## 可运行接缝

`app.services.question_intervention_producer.produce_current_question_intervention(engine, actual_user_id, actual_session_id, trusted_now, intervention_policy_id=None)`。只能由服务器读取的身份与可信时钟调用，不接受客户端 workflow、结果、金额、银行事实或 receipt。

调用打开独立干净的 REPEATABLE READ / READ ONLY 快照，用原 read_question_session 和 COMPLETE / VALID 原 DecisionTrace 核查精确原当前 revision/run/pending question/source fingerprint；再打开第二个新快照重核。仅当前 PENDING_ANSWER 能提交原 QuestionObservationRequest。idempotency key 固定由 user/epoch/session/revision/run/完整原 pending question SHA 推导；介入偏好变化或失败不产生替代键。原 observe_intervention 写事务锁实际用户，再 fresh 核原来源。

通知事务失败独立返回 SOURCE_UNVERIFIED / OBSERVATION_OUTCOME_UNKNOWN 等诊断，不覆盖已 committed Question 命令。异常只保 error code/type，不输出连接密码/异常文本。若 observe commit 后响应读取失败，只读相同 epoch/key 查原完整 request hash/receipt；没有自动第二 POST。原 Question 响应应原样返回，由调用者另外保存 producer 诊断。重复触发只重放相同原 key；读取未找到不证明未提交，不能换 key。

## 历史与当前来源

同经济语义 refresh 保留唯一 immutable semantic message，原 payload/source/hash/creation trace/receipt 不变。新的原 OBSERVE command DecisionTrace 绑定新当前 workflow/run/source trace hash；只读视图分别返回原 payload 与 current_question_observation。原 PENDING 消息不因同语义的新 run 被写成 INVALIDATED，合法 DELIVER/ACK 沿原固定 consumer/唯一 Inbox/完整原请求消费新当前证明。整个同 semantic message 的原观察清单仍保完整上界（512），逐个验证原 trace/receipt，并逐确切 session 核当前 workflow；已有等价 session 不借历史 payload.session 推断当前 session。无新原观察时，经济语义相同也不能当 CURRENT。

0012 中原 INVALIDATED/ACKNOWLEDGED 行终态不可改；旧 INVALIDATED 原件保留 LEGACY_TERMINAL_SOURCE，不恢复 PENDING、不借新观察复活 Inbox。已 claim/ACK 的一次性副作用保留，producer 不进行投递/收阅。

## 来源保全

修改前精确源码/旧直接测试/旧 FULL-507 记录保存在 `.runtime/FULL-507-question-producer-before-20261005T195010Z-e823740f/manifest.json`；原 service SHA 为 `1a8f25746eaa632cc94b3e002c3e2be9c72f152d5a5c0de4cc968422624640db`。原失败/旧测试及审核协议未删。

## 检查状态

最终相关检查均为真实工具运行；fixture 明确 SYNTHETIC，不当作金融实测：

| 检查 | 原证据 W5 目录 | 实际结果 |
|---|---|---|
| 首批原通知负例 + 新 producer/current proof | question-producer-current-observation-first-direct-20261005T195757Z-5d51dbdf | 83 PASS / 140.15s；wrapper 142.069394s，scoped 稳定。global 两个并行回拨 Web 文件改变，不能称全源冻结 |
| 跨 session 最小增量 + 受影响原只读门 | question-producer-final-current-source-direct-20261005T200448Z-c51af97b | 18 PASS / 72.77s；wrapper 74.618844s，scoped 稳定。global 其他权限/actor/回拨 UI 文件改变；与首批重叠，不宣称 101 个独立用例 |
| 五源 strict mypy | question-producer-exact-refresh-service-types-20261005T200838Z-d5d79969 | exit 0，5源，global/scoped 稳定 |
| 五源 Ruff；候选唯一真实 refresh handler 名称窄修后单源复核 | question-producer-final-repaired-static-20261005T200749Z-62b97f94 + question-producer-exact-refresh-service-static-20261005T200838Z-c35e7590 | 全部 exit 0，global/scoped 稳定 |
| Ruff format --check | question-producer-final-format-20261005T200749Z-e533a5b5 | 5 files already formatted，exit 0；最后只替换一个 handler 标识符，格式不变 |
| 单候选 collection-only | question-producer-exact-refresh-service-collection-20261005T200838Z-284a2c58 | collected 1 / 3.83s，wrapper 6.402723s；exit 0，global/scoped 稳定；没有运行 PG |

最初 strict 类型 FAILED（两个测试夹具类型），新跨 session 测试 Ruff import 顺序 FAILED，及把 refresh endpoint 误称独立 refresh service 的 types/collection FAILED 均保留原 manifest/output/source SHA；实际原 endpoint 用的是 answer_question_session + QuestionRefreshRequest。只改候选引用为这个真实 handler，没有改原 Question service/router或删除风险断言。后续修复通过不重标旧失败。失败目录含 fda2b89f、40e30591、5c767fe3、d88d2b42。

纯风险包括完整世界语义、原 owner/epoch/run/revision/hash、第二新快照变化、原键响应丢失只读查证、NOT_FOUND 非终局、旧/新原观察来源区分、跨 session 原身份、缺原观察 STALE、容量拒绝、旧终态与唯一 claim。producer 纯节点仅调用 synthetic 状态和原确定性世界，实际 financial success 仍未知；本段以原 pytest 日志确切总数为准，不填臆测金融成功。

## 单个真实候选

`apps/api/app/tests/test_question_intervention_producer_api.py::test_actual_postcommit_producer_refresh_same_semantics_deliver_ack_once_and_restart`。

候选使用隔离 demo_engine/原 seed/原 prepare/真实原服务 Question START/REFRESH（直接调用原服务提交后接缝，避免未来 Root endpoint 自动 hook 把未观察阶段提前完成），固定可信 SEED_AS_OF 仅隔离同语义 refresh，不证明动态 clock/真人已见。检原观察首次 PENDING、新真实 refresh 未观察时 STALE、producer 新原观察恢复当前来源、原 payload/hash 字段不变、同消息唯一 Outbox、Inbox 未自动创建、合法首次 DELIVER、再领取 present_once=false、原 ACK、重启只读同原件不再弹；真实 terminal SQL guard 负例事务回滚保全。完整 physical_snapshot 和金融表分母在每个风险处保留。不由本 agent 启动实际 PG。

## 未覆盖与下一依赖

- Root 尚需在原 Question endpoint 事务提交完成后接缝，或实际 worker；本包不修改原 endpoint/receipt/hash。建议接在 START/ANSWER/REFRESH/CLOSE 原 service 返回后，以实际 current_revision.session_id 调用；CLOSE/READY 不 enqueue。不能让通知失败改变已提交 Question HTTP receipt。
- 新 response 字段需 Root 生成真实 OpenAPI 并让介入 UI 分别展示历史 source/current proof；不能由前端用 old payload.session 代替 proof session。
- 真实 PG 候选、真实浏览器投递/收阅、归档 source resolver、Boundary 全局订阅、不同无权限事实的间隔释放、真人研究及全量仍未运行/未覆盖。
- 本服务不是 autonomous process；没有后台循环、自动 DELIVER/ACK、银行接口、跨请求授权缓存或历史重写。

## Root正式Question API postcommit接入（2026-10-06 05:09）

原START/ANSWER/REFRESH/CLOSE成功提交后同步触发独立producer；原response object/receipt/body不改，仅响应头X-Question-Intervention-Status诊断。Unexpected异常只记录静态类型与sessionID，通知失败不覆盖已提交问题，GET和原key读取不observe，不自动DELIVER/ACK或POSTretry。8新真实FastAPI但SYNTHETIC source检查加旧workflow/605pure共68PASS14.67s，6relatedstrict PASS；实际新独立node test_question_postcommit_integration.py已strict通过，尚NOT_RUN。原same-semantic producer actual候选NOT_RUN。崩溃间隙可靠worker/所有BoundaryCrossed生产者仍缺，不将该有限postcommit称全部507完成。
