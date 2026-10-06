# FULL-507：已提交问题通知的重启恢复

状态：`PARTIAL_IMPLEMENTATION / ACCEPTANCE_PENDING`。交付可显式启动的通知恢复进程，补原 Question 提交后、同步通知生产前崩溃的缺口。FULL-507 不关闭；金融、浏览器、进程强杀及所有 BoundaryCrossed 来源仍待验收。

## 可运行能力

新 `app.services.question_intervention_recovery.recover_current_question_notifications(engine, actual_user_uuid, trusted_now, batch_size=8, after_session_id=None)` 完整读取原当前模拟用户、OPEN epoch、原问答 DecisionTrace/审计与不可变 revision 链。发现阶段使用原干净 RR/RO 和同请求审计 scope；未验真或容量超限返回 `SOURCE_UNVERIFIED`、完整性 false、分母 null，不截断成空集。

对原 PENDING_ANSWER heads 按 session UUID 排序，每批 1–32 项；cursor 仅调度分页，不是授权或成功缓存。下一批重新完整发现，不保留旧来源验证。每项调用原 `produce_current_question_intervention`，原 producer 两次新 RR/RO 与原锁内验真继续执行；原 revision/run 派生的同键、semantic ID、request hash 继续使用。重启从头发现不会生成替代键。来源已变化、已关闭或不再待答保留原 producer 的真实状态，不自动 refresh、回答、投递或 ACK。

通知在后续独立周期可以同键重观察：原事务幂等 fence 保证同原通知命令与 semantic 消息复用。它不重试资金、确认或答案 POST；原 producer 每次调用内不增加第二 POST。`COMPLETE_SCAN` 仅表示本轮发现分页结束，item 的 UNKNOWN / SOURCE_UNVERIFIED 不因此升级为成功。

显式进程入口：在仓库根目录设置 `PYTHONPATH=apps/api` 后执行 `uv run --frozen python -m app.workers.question_interventions --user-id <实际模拟用户UUID> --once`；去掉 `--once` 后默认每30秒处理下一批，全部页处理后再次从头发现。`--batch-size` 1–32，`--interval-seconds` 5–300。只输出身份、状态、原键/hash及 message_id；不输出问题正文、连接URL或驱动异常文本。导入模块和正常 GET 均不会启动进程。本次仅运行 `--help`，没有启动数据库循环或修改正式库。

单次退出码：0=发现分页结束且各项 OBSERVED/ORIGINAL_RECOVERED/NOT_PENDING；2=仍有下一页；1=来源或item失败。退出0不等于真人已见、问题已答或金融成功。

## 新增文件

- `apps/api/app/services/question_intervention_recovery.py`
- `apps/api/app/workers/__init__.py`、`question_interventions.py`
- `apps/api/app/tests/test_question_intervention_recovery.py`
- `apps/api/app/tests/test_question_notification_recovery_integration.py`

## 已运行检查与失败原件

| 检查 | 原证据 W5 目录 | 结果 |
|---|---|---|
| 16直接风险（合成原heads/producer，非数据库） | `original-question-missed-notification-recovery-direct-20261005T221006Z-39cc0e32` | 16 PASS / 5.37s，wrapper exit0；缓存目录写权限warning保留 |
| 原4源 strict | `original-question-missed-notification-recovery-types-20261005T221006Z-1faeafd4` | exit0 / 4 sources |
| 首静态 | `original-question-missed-notification-recovery-static-20261005T221007Z-4c297085` | FAILED：worker import顺序；原源保存在 `.runtime/root-question-worker-first-static-red-20261005T2211Z` |
| 最终5源静态 | `original-question-notification-recovery-final-static-20261005T221132Z-18bd57c0` | exit0 |
| 新实际候选首 strict | `original-question-notification-recovery-candidate-types-20261005T221132Z-0159fc30` | FAILED：误用原 Close DTO 不支持的 expected_run_id/reason；原候选保存在 `.runtime/root-question-worker-first-types-red-20261005T2212Z` |
| 按真实原 Close DTO 窄修后4源 strict | `original-question-notification-recovery-final-types-20261005T221229Z-cf3fe064` | exit0；另一个已通过direct test源未变，复用其原strict结果 |
| 最终5源格式 | `original-question-notification-recovery-final-format-20261005T221230Z-200fe759` | exit0，5 already formatted |

风险包括完整发现失败/重复head拒绝、分页无饥饿、重启重新发现、原 notification key/hash/msg复用、单项失败不吞后续、UNKNOWN不填成功、owner/session不匹配与复制授权字段拒绝、batch boolean/非整数拒绝、真实空集不写。保留所有失败，不反写旧manifest。

## 实际集成候选与未覆盖

`test_question_notification_recovery_integration.py::test_actual_worker_recovers_missed_question_observation_and_reopened_engine_replays_zero` 尚 `NOT_RUN`：实际原服务提交而省略API postcommit → worker发现并观察 → 新Engine连接重新发现/同原键回放全物理零写 → 原显式关闭后worker真实空集零写，金融表始终相同。该候选新连接重开不能冒充操作系统进程强杀恢复。

当前唯一资产金融PG尚在运行，禁止并行启动本候选。正式模拟库未迁移或重置；本包不增加模型/迁移，不改原历史hash、不新增授权缓存、不触碰真实资金。后续唯一金融批应运行该候选和原producer/Question实际节点；所有 BoundaryCrossed 全局来源生产、跨进程强杀、浏览器与最终集中全量仍缺。
