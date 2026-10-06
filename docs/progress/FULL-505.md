# FULL-505 一次一问持久状态机

实施状态：有限规划范围的生产工作流已实现；真实 PG 待根任务统一执行，原编号关闭状态仍 PENDING。按功能优先执行修订二推进，没有替换初版或完整版验收。

## 可调用能力

新 `question_workflow` 复用当前 `finite_uncertainty.analyze_finite_planning` 的真实金融上下文、全部声明世界、经济后果与一步 minimax。每个 revision 只持久保存一个待答问题；回答后先根据当前真实事实重新计算全部原声明世界，再以有效的原偏好答案限制剩余世界。金额、归属、风险、权限或动作后果仍由原确定性引擎决定。

每个状态追加独立 `DecisionRun` / `DecisionTrace`，通过原 `DECISION_RECORDED` typed 审计登记，没有新表、旧审计 canonical 改写或旧 ActionPlan 更新。新 run 的 `action_id=null`，原动作只作 `base_action_id` 输入及父链来源；新候选操作身份、经济后果与原行动确认分离。`READY_FOR_REVIEW` 仍是规划结果，`authority_granted=false`、`execution_eligible=false`、`old_confirmation_inherited=false`，不提交银行请求。

请求使用服务端实际模拟用户与时钟。开始、回答、刷新持原 reset gate 和用户行锁；当前金融分析在独立且关闭的 REPEATABLE READ / READ ONLY Session 内进行，再在父写事务追加回执。同用户已有当前 epoch 的持久待答会话时，开始另一会话或从终态刷新出另一待答问题均拒绝。GET 用只读快照恢复原件并重验当前来源，不写状态。

## API 与精确回答合同

| 接口 | 输入 | 行为 |
| --- | --- | --- |
| `POST /api/v1/finite-planning/sessions` | 原 `base_action_id`、原有限 `variables`、`expected_epoch_id`、`idempotency_key` | 完整真实世界计算并追加 revision 1；同键同请求返回原回执，换请求拒绝。 |
| `GET /api/v1/finite-planning/sessions/{session_id}` | 原 session 身份，无 query 参数 | 读取并核验完整持久父链，再计算当前来源；返回确切当前问题或 UNKNOWN / STALE / ARCHIVED。 |
| `POST /api/v1/finite-planning/sessions/{session_id}/answers` | `expected_epoch_id`、严格整数 `expected_revision`、确切 `question_id`、原 `choice_key`、原命令键 | 仅接受当前待答 revision / 问题 / 选项。事实变化时追加 REBASE、清除此前答案，本次答案 `answer_applied=false`；来源未变才应用答案。 |
| `POST /api/v1/finite-planning/sessions/{session_id}/refresh` | `expected_epoch_id`、严格整数 `expected_revision`、原命令键 | 主动用当前真实事实重算；来源变化清除答案，来源未变保留原偏好答案。 |

响应保留 `original_receipt` 与 `current_revision`。原命令重放返回该命令的不可变回执，同时附当前状态；刷新发现来源变化后，顶层不再呈现旧问题，而以 `STALE_RECOMPUTATION_REQUIRED` 要求重新计算。原 epoch 改变返回 ARCHIVED，旧 epoch 答案拒绝。原回执中的旧候选保留用于审计，所有执行与确认继承标志始终为 false。

新开始 DTO 逐个变量走严格 JSON 验证，正确接收真实 HTTP JSON 的账户 UUID 字符串；没有放宽 StrictMoney、bool / 字符串钱额、extra 字段、变量类型或来源规则。客户端不能传 user、clock、银行事实、预计算世界、结果、授权、确认或余额。

原完整世界和逐轮剩余世界同时保留。删除世界、重复分配、未登记答案、错问题、错 revision、命令键换答案、父链断裂或原命令语义不符均拒绝。UNKNOWN 保留原声明分母，不以空世界冒充成功。原 stateless analyzer 的 `answer_state_machine=NOT_IMPLEMENTED` 等字段原样保留，响应明确这些字段仅描述旧无状态分析；新持久能力由外层工作流独立表明。

## 文件与集成接点

- 领域：`apps/api/app/domain/question_workflow.py`。
- 持久服务：`apps/api/app/services/question_workflow.py`。
- 路由：`apps/api/app/api/v1/question_workflow.py`。
- 直接纯 / HTTP 合同：`test_question_workflow.py`、`test_question_workflow_contract.py`。
- 唯一真实 PG 候选：`test_question_workflow_api.py::test_actual_question_session_restarts_rebases_and_replays_without_financial_writes`。

根任务已另行注册新 router、GET 前置 RR READ ONLY，并在 `services/decision_trace._supported` 与 `domain/audit_chain._trace_algorithms_supported` 明确登记 `full-one-question-v1`、`full-finite-planning-minimax-v1`，不改变原 canonical。主应用、共享数据库 / 审计版本、生成合同由根任务持有，本包不改这些源。

## 实际检查与原失败保留

六文件 scope 均按上述确切源登记，HEAD `4ccf84e973978482a1098d18c69fbfc9f011fac6`。以下四个最终 manifest 均 PASSED、`scoped_source_stable=true`、`all_source_stable=true`、`source_changes=[]`。

| 实际命令 | 原证据目录（`docs/progress/evidence/W5/`） | 结果 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest apps/api/app/tests/test_question_workflow.py apps/api/app/tests/test_question_workflow_contract.py -q -p no:cacheprovider` | `question-workflow-six-direct-pure-20261005T160041Z-10d10d53` | 38 passed / 12.77s；wrapper 14.451797s。 |
| `.venv/Scripts/python.exe -m mypy --strict` 加上述六文件 | `question-workflow-six-types-20261005T160041Z-796da0c2` | 六文件通过；wrapper 1.676783s。 |
| `.venv/Scripts/python.exe -m ruff check` 加上述六文件 | `question-workflow-six-static-20261005T160041Z-486b64e6` | PASS。 |
| `.venv/Scripts/python.exe -m ruff format --check` 加上述六文件 | `question-workflow-six-format-20261005T160042Z-7c8c9e68` | PASS。 |

原首次 `question-workflow-first-direct-pure-20261005T155320Z-3ca93178` 保持 FAILED：37 passed / 1 failed / 13.54s，真实 parsed HTTP JSON 的账户 UUID 被严格 Python 模式拒绝而返回 422。仅新 DTO 添加上述 JSON 接缝后取得新的 38 PASS；原失败 manifest、输出和源记录不覆盖。旧 finite DTO 的同类接缝由根任务另行修复、验证和保存原 422 证据，不把本 HTTP double 通过当成真实银行 / 数据库通过。

唯一 PG 候选分母为 1，当前 NOT_RUN。本候选通过真实 prepare / confirm 建立原动作，实际 4 个金额 × 目的账户世界、两轮问答、跨 app 重启恢复、原键重放与全物理表零写读取；还通过原 `ingest_external_fact` 实际 INCOME / 两银行腿 / projection 后验证旧问题隐藏、旧答案 REBASE 及新 run。以上是待运行断言范围，没有填造 PG 成功、银行结果或单项耗时。

## 明确未覆盖与下一前置

有限变量范围仍沿用旧五动作规划合同，最多 3 个变量、128 个完整世界；工作流每会话最多 16 个 revision、每用户最多读取 2048 个工作流 run，超容量明确拒绝，不截断原件。完整历史 typed 审计 / trace 原有容量限制保持。未覆盖完整 FULL 新金融动作、全部不确定变量及无限状态空间，不宣称整个提问树总问题最少。

原 base action 已进入银行处理或已执行状态时，原 analyzer 的 `NO_PLANNING_AFTER_ACCEPTANCE` 保护保持：GET 隐藏旧问题并返回 UNKNOWN，回答 / 刷新保守拒绝。显式取消 / 关闭会话回执尚未实现，因此这种持久待答可能阻挡本 epoch 的新开始；不能静默删除旧问题或伪造终态。真正同时线程的新事实 / 两开始竞争尚未实测，当前实现使用原用户行锁和 command guard；没有跨请求授权缓存。

前端逐问与回答后实际候选确认 / 执行接入、专用全局 boundary 介入投递和节流、跨不同等价事件去重、终态取消与完整并发实验尚缺，FULL-507 不因此关闭。当前新合同只提供偏好问答，原确认不会自动转给新候选。

下一前置是根任务统一执行唯一真实 PG 节点，检查原银行 / 财务表不变、原回执幂等和新事实失效，再由前端消费当前问题与明确后果复核。初版与完整版全量仍在最终验收节点执行；本处不更改 STATUS、原需求编号关闭状态、正式模拟历史或失败证据。

## 2026-10-05 16:18 UTC 首次真实 PG 失败追加

根任务 `W5/actual-public-finite-patterns-and-persistent-questions-real-pg-20261005T160818Z-f528f633/manifest.json` 保持 FAILED：三节点 1 passed / 2 failed，pytest 总 634.56s，wrapper 638.210085s，exit1，scoped_source_stable=true / all_source_stable=false。全源变化为独立 Joint / 动态月储备前端七文件，不涉及本六源；不从合批总耗时拆造问答单项耗时，也不把多数前置断言通过当作节点通过。

本问答节点实际走过开始、完整四世界、回答、同键重放、零写 GET 和新 app 恢复等前置，最终在原 `test_question_workflow_api.py:202` 的 INCOME=1 输入失败。所复用 `seed_legacy_income_fixture` 明确在 genesis 前省略 payroll 外部 clearing 初态；原银行账本因清算腿会负数而正确拒绝。银行守卫、原失败、原源不修改。根任务另存原六源于 `.runtime/W5-public-three-first-pg-failed-20261005T1622Z`。

后续唯一候选改用既存 `test_demo_seed.demo_engine` 的隔离已迁移空库，在任何业务前正式 `seed_demo` 一次，建立原默认 payroll 100000000 分清算本金和真实 genesis；测试实际读取该原 opening。随后原 transfer_accounts / prepare / confirm / ingest_external_fact 路径继续，未在已有 epoch 重置清算余额、补写假经济腿或改正式库。

## 2026-10-05 16:44 UTC 必要生命周期功能差量

此前「缺显式关闭」的实施限制由本新差量补齐，真实效果仍待根任务唯一 PG；前文保留为原时间点状态。

`POST /api/v1/finite-planning/sessions/{session_id}/close` 只接原 expected_epoch_id、expected_revision、idempotency_key。用户行锁下验完整原父链，追加 CLOSE / CLOSED 回执；原 evaluation / answers / source fingerprint 仅作历史参考，pending=null、fresh_evaluation_at=null、current_source_fingerprint=null。新 trace 独立算法 `full-one-question-close-v1`，仍用原 `inputs.question_workflow=full-one-question-v1` 和 DECISION_RECORDED；outcome 的 `last_planning_reference` 绑定确切前一 run、原 evaluation 哈希及原来源指纹。它没有 full_fresh_evaluation、金融 sources / policies / constraints，不对已经接受的 base action 假重算，也不取消或执行银行动作。旧 START / ANSWER 字节和哈希不改。

同键同关闭请求重放原回执，换 body 冲突；CLOSED 拒绝新 ANSWER / REFRESH / CLOSE，正常 GET 不分析旧 base。关闭释放同用户当前 epoch 的持久待答槽。问题最多 16 个 revision，预留唯一第 17 个关闭回执；全用户 2048 原件读容量也预留最后一槽供关闭，不生成随后无法完整读回的回执。超容量仍拒绝而不截断原件。

两只读恢复入口使用同一真实 `QuestionCommandLookupResponse`（包含 simulation=true）：

- `GET /api/v1/finite-planning/sessions/commands/{epoch_id}/by-start-key/{key}`：精确恢复 START。
- `GET /api/v1/finite-planning/sessions/{session_id}/commands/by-key/{key}`：精确恢复本 session 原 START / ANSWER / REFRESH / CLOSE 命令；REBASE 回执对应的 original_command.kind 保留实际 ANSWER 或 REFRESH。

协议为 full-question-command-lookup-v1，状态 RECORDED / NOT_FOUND_NOT_FINAL。返回原 envelope、`request_hash`（完整原 envelope 规范哈希）、指定命令的 original_receipt、另列 current_revision、session_id；START 另含 exact original_start_request。均拒 query，不 fresh 重算资金。无原件时身份 / 回执 / hash 均 null，replacement_allowed=false；未找到不是未执行证明，不自动换键或补 POST。客户端必须与 before POST 保存的完整原 body / hash 匹配才能清自己的 pending；不能仅凭 latest revision 或 server flag 当成指定原命令成功。跨 epoch 或损坏父链拒绝，正式 reset 后仅当前业务表读回的封存检索仍未覆盖。

当前请求内审计重复读取作必要差量：`_all_records` 必须 clean RR READ ONLY，完整 verify_audit_chain 一次，保留全部原事实引用 / 协议 / subjects / 经济回单校验，再逐原 run 调原 `_stored_trace` 的 hash / schema / constraints / parent / action 关联与 completeness 校验，并要求唯一同 user / epoch DECISION_RECORDED 锚点。写端在原用户锁内用独立关闭的 RRRO reader 获取这些不可变原件。没有 Session / ContextVar / 跨请求授权缓存，未减少篡改负例；旧 finite 分析自身验证仍保留。纯控制风险证明一次调用且不漏 run / anchor，尚无新 wall / SQL 性能实测，不声称提速百分比。

最终六源必要检查：`docs/progress/evidence/W5/question-workflow-close-key-final-direct-pure-20261005T164417Z-6096f541` 66 passed / 40.93s，wrapper42.670936s，scope稳定true / 全源false（仅 OneQuestionPage.tsx、其测试、Web question-workflow reader 三文件）。strict6 `...final-types-20261005T164417Z-fa315aaa`、Ruff `...final-static-20261005T164418Z-da4d45a6`、格式 `...final-format-20261005T164418Z-150d3a79` 和唯一候选 collection `...final-pg-candidate-collection-20261005T164418Z-61035457` 均 PASSED / scope与全源稳定true；collection 1 / 2.98s，不是 PG 执行。

唯一更新 PG 候选涵盖 native clearing 的实际 INCOME 后 REBASE、原 REFRESH key、旧 ANSWER key 与后来 revision 分列、真实 base execute 终态后 CLOSE / 历史绑定 / 无 fresh、金融表不变、close replay / GET / 两 key lookup 全物理零写、不同 body / stale revision 拒绝、未找到非最终、CLOSED 后新 START 释放槽。当前 actual updated PG NOT_RUN，根任务排程；没有重复执行已通过 finite 节点。原 65 pure / 首 38 pure / 37+1 FAILED 和首次真实 FAILED 全部保留。

剩余：真正同时线程的开始 / 新事实 / close 竞争、前端真实问答及指定原键响应丢失恢复、候选后果确认 / 新动作执行消费者、全局边界事件投递节流与不同等价事件去重、超容量实际长历史和封存检索尚未实测或未接。功能和验收分别登记，FULL-505 / FULL-507 不在本处关闭。

## 2026-10-06 原实际长链失败与窄期望修复

Root实际 `docs/progress/evidence/W5/actual-category-protocol-upgrade-joint-and-question-real-pg-20261005T165235Z-c356176b` 已结束：4节点整批FAILED、3个独立0011/分类/Joint节点PASS；Question在test_question_workflow_api.py:315实际execute_action返回原正确 `SUCCEEDED` 且receipt非空后，因为测试错要求 `EXECUTED` 失败。原整批1918.29s慢链及所有原日志保留，不能改标成功，也不能称CLOSE后半已经测过。Root保存原六源 `.runtime/category-joint-question-real-first-close-failed-20261005T1725Z`。

仅改该一行期望为SUCCEEDED，所有后续CLOSE/金融零写/原键lookup/重放/释放槽断言不删除。窄修前test原bytes另存 `.runtime/FULL-505-SUCCEEDED-narrow-20261005T1728Z`，原SHA f565f55fe8d5d617552ece008cac7f00abea268fd3cfc89131e5f7ce9c3ea50e；新test SHA b26cdab391be00b01dc914d33334bd5d1ee14a545b5b7f9c01e1eb07687d4eef。单文件严格类型/静态分别通过 `question-close-actual-succeeded-expectation-types-20261005T172845Z-9bbf6c87` / `question-close-actual-succeeded-expectation-static-20261005T172846Z-944a9f38`；未重复66纯测试、未自行PG。

真实HTTP读取由约63s升至100+s、末执行约257s属于未解决的可用性问题。只读源码定位同fresh GET至少三次完整审计与每stored_trace整行父祖先读取近O(n²)，详见 `.runtime/FULL-505-request-local-read-performance-review-20261005T1730Z.md`。这是准确调用路径和候选安全接缝，尚未取得分段profile或新优化实测，不声称已提速。共享审计/决策读取接缝由Root单一负责人集成，原全部hash/锚/篡改检查保留，不跨请求缓存权限。
