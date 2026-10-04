# MVP-401 边界展示合同与单一生成接缝设计

状态：**DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED**（2026-10-04）。本稿在 MVP-304 全量检查等待期间只读核对源码；只新增本 Markdown，没有实现 helper、DTO、接口或测试，没有运行测试、数据库、seed 或迁移。401 实施须等 304 完整验收并提交；以下用例均是未来验收设计，没有填写任何未来通过数。

依据：[401 前置设计](MVP-401-preflight.md)、现有 [边界引擎](../architecture/boundary-engine.md)、[来源适配](../architecture/boundary-service.md)及实际源码。此处只冻结建议的下一义务、当前保护、当前目标归属的最小接缝，完整首页聚合与业务交互仍按 401 预案推进。

## 现有行为与需要补足的信息

| 只读来源 | 实际行为 | 401 必须保留的含义 |
|---|---|---|
| `domain/boundary.py:125–135` | `boundary_hash` 使用算法名与 `_financial` 处理的 snapshot/policies/positions/products；审计关联字段被排除，列表按既有规则排序 | 展示字段不得加入金融 DTO、金融载荷或替换排序/JSON 编码；版本仍为 `strict-cash-boundary-v1` |
| `domain/boundary.py:136–139, 42–45` | 本地今日至今日+90日，两端包含；有效窗按当地整日与半开时间窗是否重叠判断 | 91 日/273 阶段，UTC 与固定 UTC+8 的现行语义不能由浏览器重算或改为即时活跃判断 |
| `domain/boundary.py:213–216, ordinary 分支` | `obligations[key] = (max(first, due), remaining)` | 元组第一项是模拟付款日；原到期日已经丢失。逾期账单与多个旧周期在 day 0 投影付款，但原到期日不同 |
| ordinary 分支 | exact 取固定额；range 默认取 max；有合法 `final_total_cents` 时用实际最终总额；减去该策略、该自然月的累计已付 | range 上限不是已知实际账单总额；一个月的最终总额不能复制到未来月份 |
| ordinary 旧期 | due<first 且没有精确周期结清事实时产生 `MISSING_OCCURRENCE_SETTLEMENT`，最终返回不足 | 不能把内部保守计算中的默认 paid=0 变成“银行证明尚未付款” |
| bill / `bill_balance` | bill 按 `total-paid` 形成一次义务；`bill_balance` 不生成 ordinary 周期，只写未出账须重算的 note | 不按最低还款额保护；不同时计账单与策略付款；未出账不虚构新债务 |
| `services/boundary.py:_policies` | 单一 naturally expired ordinary 版本保留原窗内旧义务；暂停、撤销、修改造成历史不能唯一复原时报告历史核验不足 | 不从最新状态或 updated_at 猜停止时点/旧期金额，不把缺历史当已结清 |
| `services/boundary.py:_settlements` | 同策略/月唯一，完整受信累计来源；as_of 覆盖采用的余额快照，不晚于证明可知时间；bill_balance 不能用 ordinary settlement | 展示只复用已经通过此验证的事实，不匹配相似流水，不另选“最新一条” |
| `services/boundary.py:_goals` | 验证全部已有归属，不只遍历活跃目标；cash=allocated−未结清目标本金；逐账户核对，剩余 GOAL 现金单列 | 暂停/撤销/到期不释放已有目标现金；目标本金不加成现金；原归属事实与预测返本结果分开 |
| 顶层 `BoundaryResult` | 保护分项是 trace[0] BEFORE_PAYMENT；safe_idle 是全窗最小余量 | `cash−当前保护` 不一定等于 safe_idle；不能把各日保护最大值拼成“当前保护” |
| `decision_recording.py:58–73, 124–137` | 原输入保存现有 Context 四类 DTO 与来源；constraint 按原 trace 点保存，包括 point.date | 新展示的原到期日不能替换 303 constraint 的阶段检查日期；展示 details 不进入现有原件 |

只读观察的源码 SHA256（用于实施前确认基线，不是运行验收）：

| 文件 | SHA256 |
|---|---|
| `apps/api/app/domain/boundary.py` | `8cead09ab60c752d4454b9816e9e2d43ad7fec80670c084ec7426fae2bcd011d` |
| `apps/api/app/domain/boundary_types.py` | `425b9913c1001025d4bd92af0adcd667d936bc15a7fff009097dd93d39323618` |
| `apps/api/app/services/boundary.py` | `8bd97f70ace463bdc445757acecb858ad0a52a41d1ace599ea0d7f34a79d9ed4` |
| `apps/api/app/services/decision_recording.py` | `a96a9556523708165e4274fff654596938173c7e1f1cbb6b9cd24c4b1ca1b1be` |

## 建议的最小合同

新增独立展示结果，不继承或扩充 `BoundarySnapshot`、`BoundaryResult`、`BoundaryPoint`、303 Trace DTO。旧 `GET /boundary` 响应和 `compute_boundary(...) -> BoundaryResult` 保持原序列化与签名。建议首页聚合内新增 `boundary_details`，其 `schema_version='boundary-display-v1'` 只标展示合同，不改变金融 algorithm_version。

公共 envelope 最少包含 `simulation=true, user_id, as_of, timezone, window_start, window_end, input_digest, boundary_hash`；同一个 trusted now、同一个 caller-owned RR READ ONLY Session 的 Context 与旧 BoundaryResult 提供这些值。window_end=window_start+90日。调用方不能提供金额、日期、策略/归属或改变窗口。

三个展示分区采用 `status: PROVEN | NOT_PROVEN`。最小实现仅当旧边界为 READY 或 LIQUIDITY_RISK 时发布精确展示结果；INSUFFICIENT_EVIDENCE 时相关总额、下一日期和保护对象为 null，列表可为空但 status=NOT_PROVEN，携原 blockers/source_issues。这避免在历史缺源或对象被遗漏时把某个已知条目冒称全局“下一笔”。账户账面事实仍可按独立来源展示。将来若需要部分可证的归属卡片，应另定逐实体完整性合同；本稿不通过私有 source issue 名单猜局部安全。

### 下一义务条目

内部生成的 `ObligationOccurrence` 至少保存以下字段；这是一次生成后保留的不可变元数据，不是另一个债务账本：

| 字段 | 最小类型/规则 |
|---|---|
| occurrence_id | 原 `bill:<bill_uuid>` 或 `policy:<policy_uuid>:YYYY-MM`；保持既有字符串身份 |
| kind | `CREDIT_CARD_BILL`、`RECURRING_ORDINARY`；bill_balance 不成为新条目 |
| bill_id / account_id | bill 的真实 UUID；ordinary 可为 null，不能造虚拟信用卡账单 |
| policy_id / policy_version_id / period | ordinary 的实际已确认版本与月份；bill 可为 null，不凭付款策略猜 bill 来源 |
| payee_id | ordinary 的该版本配置值；没有可靠值时 null |
| due_date | 原账单到期日，或同一次月展开得到的夹月末到期日，**不使用 max(first,due)** |
| projection_payment_date | `max(window_start,due_date)`，只解释现有 BEFORE/AFTER_PAYMENT 计算阶段，不宣称实际付款或银行计划 |
| overdue | `due_date < window_start`；今日到期为 false。以受信当地自然日判断，源账单 status 可另留，不能用其改写时钟 |
| protected_total_cents / remaining_protection_cents | 现算法采用的总保护额及减已付后的保护额；严格非负整数分，不接受 bool/float |
| total_basis | `BILL_ACTUAL`、`POLICY_EXACT`、`POLICY_RANGE_MAX`、`SETTLEMENT_FINAL` |
| actual_final_total_cents | 仅真实 bill 总额或合法该期 final_total 有值；range 未有 final 时 null，不把 max 填进去 |
| paid_cents | bill 或该期结清事实的真实累计值；没有当前/未来预付进口时 null，不能显示成有银行来源的零 |
| payment_fact | `BILL_CONFIRMED`、`SETTLEMENT_CONFIRMED`、`NO_IMPORT_CURRENT_OR_FUTURE`、`MISSING_HISTORICAL_IMPORT` |
| evidence_ids | bill 事实，或该 ordinary 已确认版本与存在的该期 settlement 的真实引用；只指向本 Context 已验证/采用的来源 |

`NO_IMPORT_CURRENT_OR_FUTURE` 沿用旧算法完整保护的保守口径，显示“按已确认规则保护”；它没有实际已付数。`MISSING_HISTORICAL_IMPORT` 只用于内部原因定位，导致对外 NOT_PROVEN，不发布精确未付数。旧算法暂算出的总额不能冒称历史实际应付。所有 remaining_protection=0 的条目不进入下一义务选择；已付清的判断来自 bill 或准确的该期证明，不能按账户余额/相似流水猜测。

选择合同为 `selection_scope='KNOWN_PROTECTION_COMMITMENTS_DUE_BY_WINDOW_END_INCLUDING_OVERDUE'`：与本次生成的义务集合完全同源，包含窗口前未结清旧期和截止窗口末日的未来承诺。按 `(due_date, occurrence_id)` 稳定排序，最早原 due_date 组成同日组。返回 `next_due_date, next_count, next_remaining_protection_cents, basis_summary`；basis_summary=`EXACT | UPPER_BOUND | MIXED`，任何 range-max 成分都不能把组额称为实际准确应付。

同日组 `items` 最多 20 项，另给 `items_complete`；group count 与 group protection sum 从**整个已生成组**求出，再截展示列表，不能从前 20 项求金额。截列表不降低组汇总真实性；输入扫描未完成/越限则 NOT_PROVEN，不用 items_complete 掩盖。只有 PROVEN、next_count=0 时才显示“当前已验证窗口内无待保护义务”；不说“没有任何负债”。窗外已知 bill 不进入此下一选择，也不偷偷扩普通周期生成窗；若后续产品要展示窗外实际 bill，可另列实际账单信息，不能称为同一全局下一义务。

### 当前保护与目标归属

`CurrentProtection` 最少包含 `date=window_start, phase='BEFORE_PAYMENT', amounts_by_reason`（五个既有键）、`total_cents=sum(amounts_by_reason.values()), cash_cents=trace[0].cash_cents, margin_cents=trace[0].margin_cents`。直接复制旧 trace 首点；有符号 margin 不截零。另展示旧 minimum_margin/safe_idle/deficit，不修改其口径。不得用下一同日组额替代全部义务保护，也不得把还未到期的未来已知义务从当前严格保护中去掉。

`CurrentGoalOwnership` 最少包含 `items`（最多现有 100 个 GoalOwnership，按 goal_id 排序）、`cash_owned_cents, principal_owned_cents, allocated_cents` 三个集合和、`unassigned_goal_cash`（最多现有 100 个账户，按 account_id 排序）及 `unassigned_goal_cash_cents`。条目保留 `goal_id, policy_id, account_id, evidence_ids`；本金对应的 position_ids 从同一验证过的 positions 取真实未 REDEEMED 目标持仓，不按产品或 policy_version_id 归类。

归属总额等于目标现金加目标本金；未映射 GOAL 现金单列，不假配给某个目标。`cash_owned_cents+unassigned_goal_cash_cents == CurrentProtection.amounts_by_reason['goal_cash']`。本金是归属/配置视角，不是当前现金；未来 AFTER_PRINCIPAL 的目标现金不能覆盖当前归属 summary。目标策略已停用而已有归属仍有效时，仍展示归属；goal_minimum 是尚未完成的额外最低保护，不是已归属现金，不能混到 allocated 或第二次扣现金。

单项金额沿用旧 MoneyCents/StrictInt 规则；集合求和使用精确整数。新展示的求和/容量检查不得让本来合法的旧 `compute_boundary` 多出失败或溢出限制，不能 clamp、浮点求和或以 Decimal/string 改旧金融输出。前端按 401 预案验证 Number.isSafeInteger 并明确契约错误；如要改成十进制字符串，仅能在新展示合同显式决定，不悄悄改变旧接口。

## 单一生成逻辑的最小提取

建议的实现形状（本稿未实现）：

```text
_compute_boundary_core(snapshot, versions, positions, products)
    -> InternalComputation(boundary: BoundaryResult, obligation_plan, adopted_goal_facts)

compute_boundary(snapshot, versions, positions, products) -> BoundaryResult
    return _compute_boundary_core(...).boundary

compute_boundary_details(snapshot, versions, positions, products)
    -> BoundaryComputation(boundary: BoundaryResult, details: BoundaryDisplayDetails)
    单次 core 计算后，构造独立展示 DTO
```

1. 保持入口重验、排序、duplicate/容量/日历、配置 hash 和事实一致性校验的既有顺序；`_financial` 与财务 hash 构造不变。不给展示 DTO 加字段到财务模型，也不把展示 schema 名写入算法载荷。
2. 在原账单和 ordinary 生成位置保留 original due 与金额来源，生成一个不可变 plan。计算用的 mutable dict 仍由该 plan 投影成原 `{key: (max(first,due),remaining)}`；付款阶段 pop 的是 dict 副本，展示从原 plan 读取，不能等 91 日循环后读取已被 pop 的空集合。
3. exact/range/final/paid、自然月夹日、原有效窗、缺旧期证明与 notes 只在现有生成位置计算一次。不要在 dashboard 再从配置展开月份，不解析 occurrence_id 推日期/金额，不读取 trace 相邻现金差猜 bill 实付。
4. core 的 legacy result 构建不依赖新展示 DTO 是否能序列化；`compute_boundary` 不构建带额外展示限制的公开 details，从而保留旧返回与拒绝行为。异常仍沿原 wrapper 映射为 INVALID_BOUNDARY_INPUT；不能 catch 后补一个看似有效空结果。
5. dashboard 在同一个 RR READ ONLY Session、一次 trusted now 下调用 `load_boundary_context` **一次**，再调用 details 接缝 **一次**。现金事实、当前归属、当前保护和下一组都来自此 Context/core。旧 `compute_user_boundary` 继续使用兼容入口；展示不额外登记来源到 `Sources.used`，不修改 source_digest/used/source_issues。
6. 标签只能取该 Context 的已确认配置或同 RR 的明确展示投影；不得拿最新另一次读取的 policy/goal 状态替换原金额版本。display 元数据仅在新的 dashboard 响应；现有 capture_boundary、执行/恢复输出与 constraint 建造继续只接收旧 BoundaryResult/Context。

也可把 bill/ordinary 生成段完整提为私有 planner；前提是 v1 校验顺序和 notes/blockers 的完整输出保持等价。不要同时保留一份原生成代码和一份展示生成代码。这里的 core 包装比新增第二套 planner 更便于保持一次计算、一次判断；最终具体函数名在 401 开始后冻结。

## 需要实际 RED 的金标准（全部待实现/待运行）

下表的数字是独立手算验收设计，不能作为已执行证明。每个状态用显式受信事实、固定 clock 与唯一身份构造；生产适配类场景必须通过合法隔离 PG fixture/来源入口，禁止禁用数据库保护或改最终首页数值造结果。

| 编号 | 手算准备/变体 | 要证明的真实行为 |
|---|---|---|
| B01 原 due 保留 | UTC 2026-10-04，现金 20,000；bill 原 9/20 到期，total=10,000/paid=4,000，来源状态自洽 | 新 detail due=9/20、projection=10/4、overdue=true、remaining=6,000；旧首点保护6,000、AFTER_PAYMENT现金14,000、safe_idle14,000，全 trace/hash 不变。第二张更晚逾期 bill 不能因都投影 day0 而成为原日期并列 |
| B02 ordinary exact | 同 clock；确认后 due_day=15、amount=5,000，本窗口生成10/15、11/15、12/15；10月准确累计paid=2,000 | 10月剩3,000、未来两期各5,000，全部保护13,000；现金20,000时safe_idle7,000；下一组为10/15的3,000，不把13,000当下一笔；仅准确该期付款抵扣 |
| B03 range 与 final | 同三期，range3,000–5,000；10月paid1,000，无final/该月final4,000/该月paid=final4,000三个变体 | 无final当前保护4,000且实际最终额null，全保护14,000；final4,000时当前3,000，全保护13,000；paid=final时10月移除、下一为11月、未来仍max各5,000。paid4,000但无final不能据此宣布5,000上限全部结清 |
| B04 旧期缺源/零/结清 | 单一策略9/1确认，due15、5,000，valid_until10/3；分别无9月结清、受信paid0、受信paid5,000 | 无源为INSUFFICIENT/NOT_PROVEN/null，不能冒称无义务；paid0保留9/15原due与5,000，自然到期不释放；paid5,000移除；不生成10/15。暂停/撤销/修改且旧历史不明确沿原 HISTORICAL_* 不足 |
| B05 bill_balance 去重 | 真实bill total10,000/paid4,000，另有同信用卡bill_balance策略 | 保护6,000一次，不追加ordinary/最低还款；真实付清后移除；未出账note保留。坏/缺bill来源与真正无已出账bill须区别 |
| B06 窗口两端/当地日 | 2026-10-04 UTC窗口末日2027-01-02；bill分别今日、末日、末日+1；另在2026-10-03T16:00Z比较UTC/Shanghai | today不逾期，末日纳入，+1排除本模型；本地跨日决定原due排序/overdue，不能用Date.now；旧273点及本金三阶段顺序不变 |
| B07 短月/有效窗 | due31 的11月落11/30；另覆盖2/28与闰年2/29。due日valid_until=00:00与当天中午；valid_from在due日中午 | 继续原 monthrange 与整日覆盖；00:00停止不生成该日，中午停止/开始仍按原重叠规则；prepare_days_before不制造第二义务。120个月上限与121个月拒绝不改成静默截断 |
| B08 当前保护与最紧点 | 现金1,000,000，当前应急0，第10日开始应急900,000，其他保护0 | 当前total0/current margin1,000,000，safe_idle与minimum margin100,000；不得把未来应急塞成当前保护，也不强凑条形等式 |
| B09 当前目标归属 | 现金170,000；已证目标cash100,000/principal60,000/allocated160,000，另GOAL未映射现金20,000，目标策略已停，未来有已证本金可用事件 | 当前goal_cash保护120,000、一般安全额50,000；归属本金60,000不进现款；未来返本总现金与目标现金同增不扩大一般额度；summary仍是原当前归属，不改成投影后的现金 |
| B10 最低额与贡献 | 当前月贡献真实与缺源/较新贡献配旧归属；同一次91日结果内越过deadline而valid_until未到，再越过valid_until | 原目标最低公式/封顶/贡献同快照核验不变；本次投影越过deadline不自动释放已选最低，显式有效期到期才停止该最低；已有cash归属始终保留，不把allocated代替月贡献；不把这一投影规律推广为跨次重算后的固定金额 |
| B11 空/不足/超预算 | 已证全部付清；missing历史源；20项以上同due；原容量越限/非法金额/bool/float | 只有完整已证空集合显示窗内无待保护义务；缺源为null/NOT_PROVEN；同日组汇总覆盖全组且items_complete=false；超限/非法输入拒绝，不给假零或截短财务窗 |
| B12 同快照/零写 | 真实银行提交后、应用投影前后；聚合GET、解释GET、轮询；真实五类动作原303轨迹 | 同RR不会拼出新余额+旧归属；未投影保持来源不足/待对账；GET不刷新生命周期、记run/audit/epoch、结算T1或释放UNKNOWN；只读全表快照相等 |

先让新 detail 公共接缝或 dashboard 请求出现真实 RED（缺接口或缺原 due 等具体断言），再最小实现。既有领域/来源测试只是可复用基线，不把本稿手算数字、模拟解析或既有通过日志登记为 401 GREEN。

## 怎样证明 v1 结果、hash 与 303 原件等价

1. 304 验收提交后、401 改源码前，在固定 clock/显式输入上用**旧公开 compute_boundary**捕获完整 JSON goldens；归档输入、旧源码SHA、algorithm_version与真实命令/退出码。手算用例另给独立债务/保护/付款/本金分录预期，不能从新 helper 或新 trace 反推预期。旧 JSON golden 只证明兼容，手算/既有独立 ledger oracle 证明财务含义，两者职责分开。
2. 重构后对相同输入比较 `BoundaryResult.model_dump(mode='json')` 全字段相等：状态、flags、额度/有符号余量/缺口、五分项、全部产品caps、blockers、notes、273点的日期/phase/现金/保护/身份顺序，以及 boundary_hash。对不足/异常/重复/容量/日历输入，保持 null/空trace/相同拒绝类别与服务映射。不能只比较 safe_idle 或只验证hash长度。
3. 对重排输入、证据ID重排、原证明展示收益/未来收入正确重hash等既有性质重验；财务输出与 boundary_hash 沿旧隔离规则相等，来源 input_digest 可按原规则变化。这不是新的 hash 算法或算法升级。
4. 同RR、同now分别证明旧 boundary 服务响应与 dashboard 内嵌 legacy响应完全相等：source_evidence_ids、source_issues、input_digest、financial boundary；展示读取不改变 `Sources.used`。不在 loader 中加展示引用、重跑第二次估算或改来源采用范围来“补齐”卡片。
5. 原303 Context 的 snapshot/versions/positions/products/source_issues JSON 保持原字段与字节规范；`boundary_constraints` 的273点保持原 point.date/phase，不能被 original due 替换。用固定身份/原 as_of 的冻结303 DTO，重算同一旧边界与 constraints 后与保存副本比较，`build_trace`/`verify_trace` 对同一字段得到原 input_hash/trace_hash，原 explanation 保持相同；GET不写新原件或审计。
6. 实际动作新创建时 run_id/action_id/时钟可能不同，其 trace_hash 本来就应不同；不能把不同运行的摘要相等当兼容门，也不能归一化身份后声称是原摘要。对真实PG已有原件，在固定 read_at 下比对读取/核验结果；当前refs/audit状态与原解释继续按既有协议分开。
7. 最终401验收还需各定向真实GREEN、静态/生成合同一致、同快照与零写PG、真实Edge业务证据及主任务统一full check与源码路径/SHA复核。本稿不将这些待办写成完成，也不根据304的1300项通过数预报401数量。

若任一完整 v1 JSON/hash/原件断言改变，应先停在真实RED定位；金融语义确实要变时需独立版本/ADR/迁移范围决策，不能在 `strict-cash-boundary-v1` 名下通过更新 golden 接受变化。401 当前建议是展示提取，没有理由升级已有财务算法或303原件协议。
