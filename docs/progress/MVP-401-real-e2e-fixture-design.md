# MVP-401 真实 Edge 黄金链 fixture / 银行事件接缝设计

状态：`DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED`。日期：2026-10-04。本文件仅在 MVP-304 冻结验收等待期读取源码编写；未实现/运行 fixture、测试、数据库、资金动作、migration、正式 seed 或控制台，未改变 304 协议。root 在 304 完整验收后决定 401 的最小必要扩展。本文件不替代 [401 前置设计](MVP-401-preflight.md)。

依据 [初版计划](../../钱途有界_初版开发计划_Codex执行版.md) 2.2：A 必须是真工资 BANK_CONFIRMED 到账后安排资金；B 是未来目标的首次确认与后续新增资金分配；C 必须是真普通消费改变现金后恢复安全。修改保护参数可以验证边界算法，但不能冒充 A/C 的触发事件。401 验真实首页状态，402 验策略/目标编辑界面，403 验完整轨迹页面，404 才添加事件按钮和 reset 控制台。

## 当前已支持的路径与精确缺口

| 源码接缝 | 当前实际能力 | 可复用范围 / 不能声称的能力 |
| --- | --- | --- |
| `domain/execution_types.py:ExecutionEffect`；`services/execution.py`、`execution_bank.py:process_operation` | 五类命令：TRANSFER_INTERNAL、PAY_RECURRING、ALLOCATE_GOAL、PURCHASE_ASSET、REDEEM_ASSET；严格 action/effect/request/策略确认、独立银行提交与投影 | 可驱动真实划转、目标储备、申购、赎回；没有外部工资入账或用户普通消费命令 |
| `services/simulated_bank.py:process_redemption` | 205 REDEEM/MATURE，受理/结算/原 operation 对账 | 可复用 T0/T1 原本金恢复；不接收工资/消费 |
| `services/demo_seed.py:_insert_facts/_open_seed_bank` | 从固定合成历史重建交易、余额证明、覆盖证明，再建立独立银行初始 opening | 仅新测试库的初始历史导入参考。没有向已有银行链追加新工资/消费的公开服务/API |
| `tests/test_execution_bank.py:source_ledger` | 导入**已有**真实 INCOME origin 的剩余位置；显式文档说明不改变原收入和现金 | 可用于初始受信来源准备；`available_cents` 不是到账额，不能把该 helper 当新工资事件 |
| `services/income_ledger.py:reserve/commit/release_income_for_action` | 五类命令的已有收入 origin/location 预留、消费、归属、转移 | 不建立新的外部 INCOME 现金来源；必须补来源接缝后才用于到账后的实际安排 |
| `tests/test_recovery_service.py:recovery_fixture` | 构造历史系统持仓、原产品/来源；新增 emergency_buffer 使 margin 为负，再调用真实恢复 | 可验证恢复机制，不能用此“保护增加”当普通消费的银行前后态 |
| `tests/test_asset_allocation_service.py:automatic_position/exposure_statement` | 历史合成申购/回执/归属声明 fixture；可直接修改旧 proof body | 只适合作为来源规则回归参考。黄金链首选真实 purchase→bank→receipt；不能在已有 303/304 原件捕获后将 MANUAL 改成 AUTO、补 receipt 或重写旧 evidence |
| `tests/test_goal_api.py:confirmed_goal_request`；`services/goals.py:create_goal_projection` | 真实 compile→proposal confirm→POST goals，初始 allocated=0，并建立零 goal bank ownership | B 的公开真实接缝；不动既有现金、不认领既有 GOAL 账户余额。确认策略与创建目标是两个实际步骤 |
| `recovery_sources.py:_quote`；`execution_sources.py:load_execution_quote` | 确定性无损到期/赎回报价；或明确 SIMULATED_REDEMPTION_QUOTE 原件 | 有损 ASK 需要明确银行报价；不能从产品字段猜价格，也不能用单次同意补缺失的站立授权 |

**缺口 G1：当前没有合法动态工资/普通消费 external-fact ingress。** `api/v1` 中没有此 POST；内部 `open_simulated_bank/open_execution_anchors` 是受信 seed/import opening 接口，不是运行中的外部现金事件 API。`SimulatedBankPosting` 的非 OPENING 行必须有 operation_id、leg_ref、previous 与 sequence>1；`BankOperation` 必须绑定真实 ActionPlan；原金融与 304 冻结经济核验按 BankCommand / legacy redemption 原协议解析。向表里随手新增两行即使余额数学相等，也不能宣称该事件已满足现合同与审计。

禁止通过新建“工资账户”、修改既有 OPENING、内部转账改标 INCOME、虚构消费义务再 PAY_RECURRING、直接改最终余额/自主额度、重新哈希旧银行 proof，替代正常到账/普通消费。真正 PAY_RECURRING 会产生 CONSUMPTION 事实，但触发是已确认义务的 Agent 支付，不能冒充用户先发生的自由消费。

## 最小 trusted simulated external-fact service（建议，当前不存在）

拟议服务在独立临时测试库里接收预定义可信模拟银行事件，供 401 E2E 的测试 runner 调用；它不是首页自动命令，不是公开 HTTP 控制台，也不增加工资/消费目的的 Agent 授权。404 将来复用此银行事实服务提供受限事件按钮，避免维护另一套写余额逻辑。

事件来源只能是服务端受信 fixture program/adapter，不接受浏览器提供最终余额、evidence_level、任意 BANK_CONFIRMED 内容、策略级别或“免确认”字段。事件类型最小为 `INCOME_CREDIT` 与 `CONSUMPTION_DEBIT`；具体 protocol/字段/版本须 root 后续冻结，本文件不是现 DTO。

| 持久原件提议 | 语义与幂等要求 |
| --- | --- |
| external event id、user/account、external_ref、event_type、bank counterparty、严格整数分 amount、occurred_at/observed_at、request canonical/hash | 与 Agent action_id/decision_run_id 分开。事件 ID 与 user+external_ref/business_key 唯一，不能换键再次入账。occurred_at 是银行事实时间，observed_at 是系统可信接收时间；均 aware UTC、不得倒退已有 ledger |
| 已接收/银行已结算状态、完整 economic posting ids/digest、原银行结果 | 重放返回原 event/result，不能产生第二次经济效果或改原时间；相同 key 内容不同 409；请求仍是事实，不产生 AUTO/ASK 权限结论 |
| projection identity/status、原 transaction/proof ids、完整 locations 摘要 | 银行结算与应用已导入分开。投影失败保留银行原事件，按同一 event id 对账；不能新建一个 Agent action 代替该原事实 |
| audit anchors/cause、event original snapshot | 在产生事实的同事务封存原件及 actual legs；应用投影在自身事务封存真实结果。新原协议/subject/event 类型必须显式纳入审计 registry，不能把未知 event 伪装成现 BANK_SETTLED |

双经济腿的最小真值如下。外部结算 ledger 是银行模拟器自己的资金来源/对手方账，不是新增用户 Account，也不参与首页现金/目标合计。

| 事件 | CASH 腿 | 外部结算腿 | 应用银行交易事实 |
| --- | --- | --- | --- |
| 工资 S 分 | `CASH:<原用户账户>` +S | 明确银行工资付款方/清算来源 -S | CREDIT，economic_role=INCOME，amount=S；现金只增一次，不能由未来预测生成 |
| 普通消费 C 分 | `CASH:<原用户账户>` -C | 明确银行收款方/清算目的 +C | DEBIT，economic_role=CONSUMPTION，amount=C；这是用户事实，不形成新义务策略 |

两腿同一 durable event、同一实际结算事务，ECONOMIC delta 总和 0；每条有稳定 leg ID/ref、真实 before/after、前序 ID、连续 sequence、可信 occurred_at。账户现金与外部付款方均不能因新事件负余额；若采用非负余额协议，工资清算来源的初始资金须在新库创建时由固定可信银行 fixture 明确导入，不能临时新增用户工资账户或调高已有 opening。外部腿账型/来源必须显式协议支持，不能因为字符串看似 PAYEE 就复用任意现分录。

INCOME_LOCATION 是资金来源备查维度，不重复计入经济现金。工资 origin 固定引用新 BANK_CONFIRMED transaction/evidence，origin.amount=S；新 location/fragments 的 AVAILABLE/RESERVED/SPENT/ASSIGNED 总额与 origin 守恒。已有 origin/lot 的原件与 opening 不变。新 origin 的首次银行 location 发行/opening 必须精确绑定这次真正银行入账，不能只接受客户端的 available_cents。消费若使用已有可用新资金，须按明确固定规则记录 AVAILABLE→SPENT 备查腿/后继 ledger；其他归属、预留不得无故释放，返本不能被当工资，新位置也不能掩盖 origin。

最小职责分层：

1. **银行 adapter** 解析服务端预定义原事件、做 owner/金额/时间/原 key 核验，按真实 bank head 顺序追加完整经济腿及收入位置事实，独立保存银行原结果。它不询问 Agent 策略能否接受工资/用户消费，也不绕过后续 Agent 的策略授权。
2. **应用事实投影** 校验原银行 event/hash/legs 和当前账户原 before；在独立完整事务/savepoint 中更新 Account/Transaction、余额 proof、历史覆盖、完整 exposure 及 income proof。从实际腿推导 balance_after，不能从外部指定期望 UI 余额。失败全部投影回滚，银行事实保留待导入。
3. **证据后继** 新建稳定 ID 的银行 transaction proof 和余额/历史/exposure/income 后继；保持旧 body/content_hash 不变，合法更新 SUPERSEDED 状态，保持 supersedes 链与同用户/源/单调时间。不得使旧 303/304 原来源因内容改写变成完整性错误。
4. **审计** 新银行事实及真实投影各记一次；封存 canonical request/result、双腿、typed references 和原 event identity；同 key/recovery 不产生双经济事件。已有 run 的旧来源保持历史原件；current annotation 与原 explanation 时点分开。
5. **读取/边界** 完成事实投影后走原 `load_boundary_context/compute_boundary`、完整曝光/收入位置校验；被动 GET 不导入事件、不结算、不补审计。银行已结算未导入必须待对账/来源不足，不能拼旧应用余额为绿色安全。

若采用独立 bank commit→application projection，整个事实导入/原事件对账命令复用 `audit_command_guard`，各实际写事务在 User 锁之前 `transaction_gate`，与 reset exclusive gate 同 key；保持两个实际经济/投影事务，不用一个大事务掩盖银行已发生事实。reset 不得在银行提交与应用导入之间清掉原 event/legs。初始外部来源导入、status 后继、审计追加的锁顺序也须一致。

银行 facts 会真实改变余额，Agent 仍只在五类动作合同和已确认策略范围内行动。接收到工资不等于新增储蓄权限；消费是已有资金事实，不需事后让 Agent 批准消费，更不能因此创设新支付授权。

## 协议与 migration 选项（需后续决定）

| 选项 | 必要改动 | 取舍 |
| --- | --- | --- |
| A：独立 external-event 原件，复用统一 bank posting 链 | 新 durable external-event/projection 表；posting 增 external_event_id+owner FK/唯一 leg；非 OPENING 原点改为 operation/external_event 互斥；显式外部清算 ledger 身份；bank projection/unprojected 守恒和 frozen audit registry 识别新原件 | 倾向优先评估：用户 CASH 仍是一条真实链，可复用 ledger_heads/顺序检查。涉及共享 posting 约束和审计，必须专门 migration/真实 PG RED/GREEN，不能在 304 冻结代码中直接塞类型 |
| B：独立 external-event / posting 原件表 | 新原件和 legs 表；将同一用户 CASH 的前序/余额序列与现命令 posting 明确合并，读取、投影、封存和原件核验支持两种原点 | 不改变五类 BankOperation 的原点合同；但若留下两套独立现金 head，会失去唯一银行真值，必须解决统一顺序与完整 ledger 核验；不是“test-only 新表所以无需金融改动” |

两选项都不能在没有 migration/核验职责的情况下仅添加 helper。旧 304 canonical/schema/hash 保持原版本可核；新版本显式注册并保存原文，不能默默升级旧 event。新公共银行事件 endpoint 和演示按钮留给404；401最小职责是受信 runner 可复用的真实银行事实服务及其一致读取，不做公开控制台。

第一轮 E2E 应把 external facts 安排在没有未解决 UNKNOWN/已结算未投影前操作的稳定阶段。若未来事实接收允许与这些状态交错，需另做实际 bank order、pending projection 和收入预留的完整协议验证；不能让适配服务静默丢掉已经发生的银行事实，或用 reset 消除 UNKNOWN。

## fixture 组装与时钟/运行隔离

未来 runner 只在 `db/testing.py:temporary_database/require_test_database` 产生的随机 `bf_test_<32hex>` 库迁移/初始导入。允许复用 seed 的固定初始真实来源；不修改正式 demo、不在各阶段 seed/reset 来冒充连续事件。完成所有初始可信导入后，浏览器跟踪同一 user、epoch、account、origin 与实际 run/action/receipt/event 身份。

真实 FastAPI 进程须明确使用该库，并注入可信 test clock（参照 `tests/test_goal_api.py:goal_client` 的 get_engine/get_now 接缝；当前该 fixture 是 TestClient，尚无 E2E server runner）。浏览器和 API request 不接受 now 参数。T1 使用服务端 clock 前移并确认读取时点，不等待一天，也不改 accepted_at/available_at/原 request 或本地 Date.now 来骗权限。先 freeze 特定 now 再出报价，保持 quote 的 15 分钟有效期和原 effect。

`apps/web/playwright.config.ts` 现自动启动真实 uvicorn 与 Web 代理，默认 channel=msedge，但可被 PLAYWRIGHT_CHANNEL 覆盖。未来 fixture runner 需明确隔离 DATABASE_URL/进程/端口、禁止 reuse 正式现存 server，确认实际 Edge channel/version；不可只把 TestClient dependency override 当成另一个真实 uvicorn 进程已生效。

401测试 runner 调用公开 POST prepare/confirm/execute、policies/goals/recovery 和拟议内部 external-fact service，页面只读刷新/解释；它无需提前添加404按钮。只允许固定事件程序/阶段名称，不提供 arbitrary SQL/balance endpoint。金额预期取固定事件腿与原回执字面值，不镜像规划算法生成 expected。

## 三条黄金链与必要分支的阶段表

| 阶段 | 真实准备/触发（未来实现） | Edge 可见状态与实际 oracle |
| --- | --- | --- |
| A0 工资未到 | 初始银行历史/旧现金；原已确认房租、信用卡、生活/应急与 `[min,target,max]` 目标/申购授权；工资预告仅低可信预测 | 现金不含工资，金融上限不因预测增加；无新 INCOME origin/银行腿/receipt；已有手工持仓仍 excluded |
| A1 银行工资结算/导入 | external-fact service 接收固定 S，独立双腿+INCOME证明+完整新 origin/location；真实投影完成 | 现金增加 S；origin/available 相符；保护由原算法更新，来源完整。同一 RR 聚合 as_of；不得从“银行已结算未导入”提前显示可用工资 |
| A2 目标分配 | 对原已确认目标调用 prepare，读取服务端选出的真实 effect，再 execute；相同账户分配可不搬现金，不同账户则有真实 cash 对腿 | allocated/cash_owned 真增加；new-funds AVAILABLE→ASSIGNED；总现金同账户情形不变，跨账户总现金守恒。GOAL账户已有未归属现金不能当新目标归属 |
| A3 合规自主申购 | 原真实产品版本与完整曝光；prepare/execute 选许可产品和金额。 fixture 通过已确认候选范围保证可行，不从前端指定“想要”的 amount | 银行 CASH -purchase / POSITION +purchase；receipt、原 purchase来源、managed principal 与剩余现金一一核对；AUTO 与为何没问来自真实 phase/outcome，不能仅看 safe_idle |
| B0 新目标候选 | 真实 `/policies/compile` 使用已支持的确定性自然语言 fixture 文本 | 候选没有 ACTIVE，goal列表/allocated 未增，现金不变；401只验首页受影响状态，候选编辑UI属402 |
| B1 首次确认 | 原 proposal reviewed_hash+accepted true；再真实 `POST /goals` 绑定原 version 与账户 | policy ACTIVE；goal allocated=0；GOAL_CASH/GOAL_PRINCIPAL 新独立零初始；保护可因新目标最低承诺改变，不能把保护变化当已归属。重复确认/创建不动钱、不重复事件 |
| B2 后续新增资金 | 新工资实际进入后重复 A1/A2 合法路径 | 只有真实新增资金分配才增加归属；历史余额、旧目标和 MANUAL本金不被首次确认追认 |
| C0 消费前 | 首选 A3 真实执行得到系统持仓，原申购授权允许适当恢复；经原边界证明安全 | 固定原购入action/receipt/transaction/product/策略；无需回写旧持仓政策归属或补合成申购回执 |
| C1 普通消费后 | external service 接收固定 C，用户现金 -C/银行收款方 +C；新 CONSUMPTION proof，收入可用位置相应实际消费 | 现金真实下降 C；原保护/义务不凭空增加。重新边界出现真实缺口/配置退出安全区；不评价消费、不创设虚构支付义务。最小 fixture 可用已确认固定生活准备金，避免借分类器变化制造缺口 |
| C2 T0 无损恢复 | 真实 recovery preview→POST runs，或五类 redeem 原工作流；原 policy允许，产品/报价无损；独立 bank→projection | HELD→赎回→REDEEMED；现金 +net、本金 -principal、真实 receipt/PRINCIPAL_RETURN及通知；经济腿和原 operation 仅一次，返本不变新 INCOME |
| C3 T1 受理/等待 | 产品真实版本 delay=1；POST受理，clock未到available_at；后推进clock仅GET | ACCEPTED/PENDING_SETTLEMENT，receipt absent、现金不变；到期被动 GET仍不结算，全表0write，不能以计划 projected_boundary替代actual_boundary |
| C4 T1 实际对账 | 推进服务端 now 后，重复原 idempotency_key 的 POST runs/原 execute | 原 operation 结算一次、receipt一份，现金与本金实际转移；不得新建run/operation再赎回。恢复完成按 actual_boundary显示 |
| C5 有损 ASK | 真 fixed-product 版本/原购入权限，显式 bank EARLY_WITHDRAW quote，principal=net+fee+loss；quote绑定目标/账户/产品/原时间；不能仅改产品bps猜价 | preview/run真实 ASK_ONCE；无 automatic bank request/经济腿/receipt，原持仓仍HELD。显示具体净额/损失/费用/到达时点与未满足确认；即使原策略允许提前支取，也不能省略本次后果确认 |
| C6 ASK 人工来源（补充分支） | 真实 RedeemIntent prepare→effect_hash原后果确认→execute；保留 USER_ACTION_CONFIRMATION 事实与 expires_at | 确认只授权该 effect，不改原 standing policy；ASK原等级保留、confirmation_satisfied真；在实际receipt前不写已到账；401只验首页/解释状态，确认按钮UI非401范围 |
| X UNKNOWN | 实际 projection savepoint INSERT失败，银行独立 SETTLED 保留，再解除故障原操作重试 | UNKNOWN/待对账、原bank identity、receipt absent与保守来源状态；GET不恢复；原键重试只补一次projection/receipt。不得在浏览器fake response制造“真实UNKNOWN”证据 |

T0/T1/ASK现有机制参考 `test_recovery_service.py`、`test_execution_redemption_flow.py`、`test_audit_workflow_integration.py`；它们证明机制已有测试接缝，**不代表上表未来完整 Edge/工资/消费链已通过**。现 loss quote 回归部分使用现金管理 fixture 加 fee/loss；不能把它的截图标“真实定存提前支取”，定存分支须新真实产品/申购来源与显式报价 fixture。

## 必须复用的银行证明与全表 oracle

| 事实 | 必须引用的现校验/真值 |
| --- | --- |
| 现金余额 | `boundary.py:_cash` 精确 SIMULATED_BANK_BALANCE 绑定 account/type/balance/currency/as_of；`simulated_bank.py:ledger_heads/validate_bank_projection` 独立连续链和完整现金/本金集合 |
| 工资/消费流水 | `domain/history_coverage.py:bank_fact_snapshot`：同用户 transaction/evidence，SIMULATED_BANK_TRANSACTION、BANK_CONFIRMED、原hash、direction/amount/balance_after/time/counterparty 精确匹配；经济 role由银行原件提供，不能靠可编辑category推断 |
| 完整历史 | `build_history_coverage` 真实 account scope、交易数量/digest、期间和时间；不把新增交易留在旧覆盖摘要里，也不靠省略旧交易让生活估算/资金来源变绿 |
| 新资金 | `income_ledger.py:read_income_state`、domain IncomeLedger v2、`execution_bank.py:validate_income_locations`；完整 origin、可用/预留/消费/归属及银行位置一致；`open_execution_anchors`仅真实首次受信来源创建参考 |
| 目标 | 已验证 OWNERSHIP_SOURCE/CONTRIBUTION_SOURCE、零首次 goal bank anchors；cash_owned/principal_owned/allocated总量与原银行legs、当月真实贡献匹配；同账户分配不伪造现金转账 |
| 系统持仓与恢复 | `execution_exposure.py` 实际 successor、完整 asset_exposure_snapshot/v2 bank requests/postings/operations/reservations，真实 purchase来源；`execution_sources.py` acquisition/quote/return-account；MANUAL/UNKNOWN 不擅自接管 |
| receipt | 301 `execution_projection.py:verify_execution_receipt`；205 `recovery_receipt_integrity.py:verify_recovery_receipt`；bank完整legs、原request/hash、实际transaction/evidence与receipt posting IDs；不能只比响应文字 |
| audit/历史 | 304 `verify_audit_chain` 真实 typed anchors、same UUID不同kind、旧内容/合法状态/当前引用、单经济事实；未来external协议须增原件核验后才可宣称VALID |

保留 `test_boundary_service.py:snapshot` 与 `test_goal_api.py:all_tables` 的全表语义：从 `Base.metadata.sorted_tables` 按主键读取所有行、完整列作确定性序列化。未来多表 oracle应使用同一 RR连接，并在无并发写入或明确barrier处采样；现 helper本身是直接connect，不能把它宣称为已实现跨表RR oracle。若新 adapter 增表，要包含迁移后的新表，不固定旧表数。检查只读 GET/preview/解释/刷新前后所有表相等，包括audit head/events/subjects与bank/income，不只余额相等。

银行/投影成功阶段也要 literal oracle：预定义 S/C/P/net/fee/loss 与真实腿数量/符号/身份、sequence、前后余额、全局经济守恒；一笔 bank事件/命令、一份对应receipt/projection与一组transaction；原opening哈希/行完整相等，原evidence body/hash不改。金钱发生变化是预期，不采用“全表相等”代替资金变动验收。重复原操作才要求全表相等或协议明确的真实新观察事实；实际completed早返回不应新增经济事件。

## Edge 证据包与待验收边界

未来每条链保留真实 Edge截图/浏览器版本channel、Web代理→真实FastAPI请求/响应与request_id、服务端now、同一聚合snapshot as_of、原event/run/action/operation/receipt IDs、before/after全表摘要及银行腿/证明摘要、源码路径/hash和实际命令exit。健康检查 PASS只证明连通，不计业务通过；route.fulfill业务JSON、组件mock、重新seed到另一个余额、渲染图均不能作为本矩阵真实证据。

401聚合必须在一个 caller-owned RR快照下读取真实facts/边界，不由前端拼多个不同请求快照；解释GET不保存决策或执行。真实未导入/UNKNOWN/T1pending/ASK、来源不足/错误/null/整数分应各有截图与oracle；screenshot仅onfailure的当前health配置不足以留完整业务阶段证据，未来测试需显式记录这些实际阶段。

实现前阻断项是 G1 external事件原点与迁移、可信E2E server clock/隔离runner，以及401单快照聚合合同。原五类服务、现测试helper和工程health结果均不能替代它们。此文没有填写任何未来 PASS数量；没有实现external service、endpoint、控制台、fixture或测试，也没有修改304原协议。
