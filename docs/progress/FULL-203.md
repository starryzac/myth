# FULL-203 · 未来收入与执行资金隔离

状态：隔离安全路径已实施候选；规划用未来收入来源接口尚未实现，原需求仍部分完成、未关闭。

2026-10-06 当前差量：已新增独立的原收入来源登记、USER 条件确认、365 日程及前端原键恢复；下文早期“来源未实现”描述保留其历史范围。当前仍 PENDING：此新差量只有直接模块证据，实际 PG/浏览器及任意合法假设变化的最终验收尚未执行，原年度引擎占位没有替换。

原要求见 `docs/spec/requirements-traceability.md:65` 和原完整版计划第 1220—1222 行：未来收入只在规划视图，不进入当前执行边界。

## 实施内容与设计

`GET /api/v1/planning/annual` 单独提供年度条件投影和原 90 日当前执行视图。两者均只消费已验真的当前原件；没有任何客户端金额、预测工资、授权、用户身份或时钟输入接缝。

响应 `future_income.status=NOT_IMPLEMENTED_NO_REGISTERED_SOURCE`，`included_in_execution_cents=0` 与 `included_in_planning_cents=0` 明确表示当前没有未来收入接入。它们不是“测得未来收入为零”，也不能用于声称预测功能已完成。真实到账收入仍按原银行事实和收入账本验真读取，不重新分类为预测。

年度保护曲线固定不授予权限，任何未来条件余额不成为今天的可执行现金。不保存或缓存授权结果，不重算历史动作/回执哈希。

## 文件和直接验证

实现及检查与 [FULL-201](FULL-201.md) 同批：`full_projection.py`、`planning.py`、严格 horizon 合同和直接测试。

- 纯负例拒绝 `future_income_cents`、`expected_salary_cents`、`cash_cents`、`authority`、`user_id`、`as_of`、`horizon_days`、`principal_available_at` 等查询字段，业务服务未被再次调用。
- 当前事实出现未来观测现金时，原边界标为 `INSUFFICIENT_EVIDENCE`，不把金额加到安全资金。
- 只修改来源问题不能产生新现金；没有未来收入字段的 `BoundarySnapshot` 仍拒绝该类扩展。
- 同一已验真当前上下文的年度读取不会改变原 90 日财务输出；正负测试保留目标本金保护和同日付款顺序。
- 六文件 mypy/Ruff/格式已通过；首轮直接纯检查 137 PASS、1 个原日历边界负例 FAIL 保留。root 修复原日历检查后，只重跑失败日期参数与新模块直接相关测试，29 PASS/1.92 秒；具体命令、原日志和版本见 FULL-201。
- root 统一实际 PG 验证 2 PASS/11.66 秒，年度节点原件见 FULL-201；未知未来收入参数 422、原 MVP 边界一致和全物理表零写已验证。全量与具有真实注册预测来源的未来收入实验尚未测得。

## 具体未覆盖

未来收入的来源登记、观测/有效时间、撤回/替代、规划曲线显示及“任意合法预测变化对当前动作完全无影响”的实测变形测试仍缺。没有可靠来源时，本批不增加公开预测金额参数，也不以成功数字填空。

下一前置：按 W2 双时态/证据合同添加独立的规划收入来源适配，保持执行输入数据流无预测收入。路由/只读事务及直接真实 PG 已通过；最终关闭仍需预测展示能力与原需求验收证据。

## 2026-10-05 · 新完整义务年度视图中的隔离

新增 `GET /api/v1/planning/full-annual` 候选将已确认 DatedExpense/PeriodicTransfer 接入独立规划保护曲线，仍无客户端预测金额或资金权限输入。原 90 日执行视图及原 365 日投影完整保留，新 Full 摘要和条件现金另行展示；新 Full 确认不授予原银行执行权限。

`future_income` 继续明确 `NOT_IMPLEMENTED_NO_REGISTERED_SOURCE`，计入执行和规划皆为 0；投影附加字段 `future_income_in_current_cash_cents=0`、`future_income_in_original_execution_cents=0`。这些字段表示当前来源接缝缺失，不表示测得未来收入是零。Full 未来承诺预留和原本金未来验真到账都不是预测收入。

六个新模块/测试最终直接纯检查 46 PASS/3.47 秒，严格 mypy/Ruff/格式 PASS，原件详见 FULL-201 的 2026-10-05 差量。未来收入/已付额/当前用户/时钟等查询均在实际路由纯测试中拒绝；真实 PG 节点由 root 统一排程，目前未执行。没有声明任意合法预测变化的实际变形结果，因为规划收入来源仍未实现。本编号保持部分完成、未关闭。

15:42 UTC 增补：root 的合批两个实际 PG 节点已 2 PASS/209.43 秒，scoped 稳定、全局因独立源码变化不稳定；原件与精确边界见 FULL-201 同时增补。本 Full 年度节点实际验证未来收入输入 422、计入金额 0、原 90/365 日完整结果一致及全部物理表零写。没有真实已注册的预测收入源或任意增减其值的实测，仍不关闭本编号。

## 2026-10-06 · 原收入来源的显式条件声明

新 `future_income_planning.py` domain/service/API 独立登记 `USER_DECLARED_HYPOTHETICAL_MONTHLY_REPETITION`。真实来源仅接受完整原 IncomeLedger 的 native origin，并复核原交易、BANK_CONFIRMED 收入证据及收入账本证据；交易名、工资分类、期初余额、本金返还或转账均不能作为未来稳定收入证明。来源状态/哈希/时间/原经济角色不能匹配、来源不全、冲突、超容量或无当前轮次时保留 UNKNOWN/null，不补预测金额。

用户选择已存在交易和 `expected_origin_hash`，服务端从原入账金额、用户登记时区及原发生本地日生成明天起的有限365日期月度假设；短月取月末，日内到账时点未知。此为用户自述的条件情景，既非统计预测，也不承诺未来银行付款。首次候选15分钟内须明确复核 `candidate_hash` 后另行确认。所有写入验证当前实际签名本地 USER、owner、OPEN epoch 与服务端时点，客户端不传金额/clock/role/result。

持久层只追加两类 typed USER_DECLARED Evidence 元数据：候选与独立确认。UUID5 绑定实际 owner/epoch/原 key，原完整 body、request hash、来源、候选 snapshot、actor及时间保留；不同命令/配置不能复用原键。同来源不能重复确认计入。没有新 PolicyVersion、银行命令、资金动作、决策或授权缓存；原银行/应用权限仍逐请求独立验证。当前源登记上限1000原收入、200元数据；超限不截断后宣称完整。

接口均在 `/api/v1/planning/future-income`：

- `GET /sources`：完整原来源库存及原分母。
- `GET`：独立365日条件规划、完整来源/状态/原件及 null。
- `POST /candidates`：恰好 `expected_epoch_id/origin_transaction_id/expected_origin_hash/idempotency_key`，仅候选。
- `POST /confirm`：恰好 `expected_epoch_id/candidate_id/reviewed_candidate_hash/accepted:true/idempotency_key`，仅条件确认。
- `GET /commands/{epoch}/by-key/{key}`：指定原命令/完整 body/hash/候选与确认原件；`NOT_FOUND_NOT_FINAL` 不是最终未提交证明，不能创建替代键。

全部响应保 `included_in_current_cash_cents=0`、`included_in_execution_cents=0`、`grants_authority=false`。新规划是独立读模型，**未替换**原 annual/full-annual 的 future-income0占位，不影响原 BoundarySnapshot、原执行输入、实际资金或已有动作。规划条件累计存在时仍不是今日现金或银行可执行金额。

### 可运行前端与恢复

新增 `FutureIncomePlanningPanel({userId,epochId,mutationBlocked?})`，手动读取实际规划及当前本地 USER 显示；选择原收入来源、创建候选、展开原件、完整复核、明确条件确认、显示完整365日期和冲突状态。没有未来金额输入、自动POST、自动确认或自动执行。

新 `future-income-operation.ts` 在每次POST前保存完整原 body、与实际 `JSON.stringify(body)` 相同的 body_json、canonical SHA、owner/epoch/key，以及确认所需的原候选。POST成功、网络丢失、坏JSON和4xx均不清门；只有新的原键GET得到匹配的指定命令/body/hash/原元数据才解除这次pending。NOT_FOUND保持pending，手动重放只用同原body/key。坏存储或权限失败保原存储/意图并禁止新写入。刷新只恢复存储，不自动网络；存储副本不是授权或freshGET，须手动重新读原件才能确认候选。仅关闭本地复核不删除服务端原件，也不撤销已确认条件。

Root接线契约：`recoverFutureIncomeOperation/getFutureIncomeOperation/useFutureIncomeOperation` 暴露 `pending/busy/recovering/storage_error/workspace`；`isFutureIncomeWorkspaceUnresolved` 对候选工作区返回true。跨族资金、reset/logout门由Root统一接，传入的 mutationBlocked 只代表其他族；自己的GET应始终可达。`FutureIncomeOriginalRecoveryPanel` 是无POST的跨页原键核对表面。历史CONFIRM工作区不成为当前资金权限。此次没有修改App、已有页面、共享http、其他族store或generated contracts。

### 本批真实检查边界

- 后端新五源直接 domain/metadata/FastAPI JSON 35 PASS/3.43s；synthetic principal、来源和读服务 doubles，不是实际PG/cookie/银行实测。原件 `evidence/W3/future-income-domain-and-json-direct-final-20261006T011456Z-409ce72b/manifest.json`；strict5 `91c8403a`、static `398b4143`，精确源副本 `.runtime/FULL-203/backend-final/`。
- 前端三直接模块37 PASS/7.27s（原wrapper8.425971s）`evidence/W6/future-income-web-direct-root-config-20261006T013223Z-d71aad26/manifest.json`。21 reader 风险包括原Python synthetic DTO/hash互通、365完整分母、短月末、日期/金额/权限篡改、未知null、不安全整数；9原store+7组件风险包括原body/hash持久、freshGET、NOT_FOUND、同body重放、存储拒绝、跨族门、scope变更。所有HTTP夹具明确 `SYNTHETIC_HTTP_FIXTURE_NOT_PRODUCT_PROOF`，没有真实浏览器/数据库。
- 初次整体Web types的 TS18047、36 PASS/1 FAIL（测试单查询误判保留的两个UNKNOWN说明）、sandbox EPERM与重复config路径启动失败全保留。修前八源 `.runtime/FULL-203/web-source-before-null-and-query-fix/`、37PASS源 `.runtime/FULL-203/web-pass37-original/`，没有修改这些旧检查状态。
- 必要原body_json逐字节加强：拒绝改格式或重复键，即使解析canonical相同也不能替代保存的实际POST bytes。只改自身store+新增2风险；此前未变reader21证据继续保留，store11+直接消费者7共18新PASS/6.84s，`evidence/W6/future-income-original-bytes-risk-final-20261006T014205Z-70148d43/manifest.json`。相关lint `63c598eb`；该窄批wholeWebtypes原PASS为 `future-income-original-bytes-types-final-20261006T014159Z-71ee19aa`；后续最终状态以下方最新检查为准。
- 最后当前来源一致性窄差量：已确认候选须与当前源库存的同originID和originHash一致，允许当前ledger Evidence更新而保留原ledger原件；原发生/知悉时点不能晚于候选受理。reader22+直接Panel7共29 PASS/19.02s，`future-income-current-origin-risk-final-20261006T014628Z-4fe1c623`；相关lint `2115440c`。时间负例随后重绑全部其他hash/metadata并先验证控制分支，避免因无关旧hash而失败；仅此必要节点1 PASS、21未选中，`future-income-time-order-risk-isolated-20261006T014826Z-a62b8785`，其lint `3341d5a4`。没有将未选中的节点算作新PASS。
- 当前最后wholeWebtypes `future-income-current-origin-types-final-20261006T014625Z-3e8bdbf4` **FAILED exit2**，诊断仅独立 GoalAdjustmentsPanel及其/API tests的空值/联合类型，无203诊断；没有动他方源或重复wholetypes。前次 `71ee19aa` 是PASS，但Root App运行中变化，不能用它声称最后全Web冻结类型完成。Root需在这些他方源与宿主稳定后统一最终类型检查，本编号不因该直接源证据关闭。
- 首批前端lint7源 `5a2ee512`/wholeWebtypes `a477b122`均exit0；scope稳定。direct和types的global false仅原Main/deps及独立domain变化，不能宣称全仓冻结；lint首批global与scope均稳定。没有再跑全量。

### 实际PG候选、未测与未实现

新节点 `test_future_income_planning_integration.py::test_actual_signed_user_condition_is_original_recoverable_and_never_executable_money` 仅collection，未运行。它使用原 owned `bf_test_UUID` fixture、真实OPEN epoch之后的固定服务端UTC、原独立模拟银行income入口、真实签名USER cookie和实际Main路由；候选/确认已commit后明确丢弃原HTTP响应，再查同键并重开HTTPclient读取；全部物理表完整反射，预期只有两条新Evidence，其余包括银行、现金、本金、Goal、PolicyVersion、Action、DecisionRun、审计等逐字段不变；原trace实际completeness前后保留，不假升COMPLETE。原native audit按真实chain/reference/count/tail/errors核验。含错sourceHash/错review/额外金额/非严格accepted/AGENT拒绝，以及owned库原银行证据CONFLICTED后的UNKNOWN/null负例；不恢复该篡改、不触正式库。

候选strict首失败错误地引用并不存在的 `AuditVerification.complete` 已保原源与 `fead8124`；按原类型完整字段窄修后 strict `d3041fb2`、Ruff `ab38aa2b`、collection `340ea28e` 均exit0。collection显示1节点/4.88s，**不等于**数据库效果通过。Root独占后续实际金融运行；本代理没有迁移、seed、连库或金融执行。

仍缺：这条新真实PG效果、真实浏览器/手机与原请求跨页全局宿主检查；动态实际clock过期行为（候选测试固定trusted now）；任意合法条件增减对原动作的完整实际变形验收；annual/full-annual/联合规划显示新条件源的消费者（原0占位保留）；编辑/撤回/替代/跨轮次续期声明。统计预测、工资稳定推断和银行未来承诺从未实现或证明，不能作为本能力成功项。原编号PENDING、旧21/92关闭数不变，未运行全量、正式历史未重置。

Root已通知实际Main注册与真实Schema生成/独立check通过，并自行接Annual宿主、跨页GET及全局门。本代理只交付上述独立组件，没有将Root正在实施的宿主检查或金融单链称为已完成验收；宿主最终证据由Root追加。
