# MVP-401 三黄金链：最小合成场景与独立手算 oracle

状态：`DESIGN_ONLY / MANUAL_ORACLE_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED`。日期：2026-10-04。本文件只读现源码并手算，没有执行算法、fixture、测试、数据库、migration、资金动作或正式 seed，也未改源代码/合同/304协议。现有 [401前置设计](MVP-401-preflight.md)、[真实E2E接缝设计](MVP-401-real-e2e-fixture-design.md)保持不变。

依据 [初版计划](../../钱途有界_初版开发计划_Codex执行版.md) 2.2、10.1、MVP-401：工资到银行事实→目标储备/自主配置；新目标首次确认→后续新增资金分配；普通消费到银行事实→无损恢复/有损ASK。本文给未来合同/fixture冻结时的字面预期，不宣称这些数字已由 API 或 Edge 得到。

## 共同事实、时钟与算法口径

金额单位全部为**整数分**，包括表中的 S/C/P；例如 500,000分=5,000元。`minimum_margin` 可为负，`safe_idle=max(0,minimum_margin)`；来源不足则二者都是 null，不能把未知写成0。

- 主时钟 `T0=2026-10-04T00:00:00Z`，用户 timezone=`UTC`。各准备/确认/结算在同日按可信服务端时钟推进若干分钟；银行报价在原15分钟有效期内。初始策略确认已在 `2026-10-03T23:50:00Z` 完成、valid_from不晚于T0且窗口内不失效；不造更早未结清义务。未来目标B在T0后真实首次确认。
- 当前窗口为2026-10-04至2027-01-02（含），91日×BEFORE_PAYMENT/AFTER_PAYMENT/AFTER_PRINCIPAL=273点。T1对账后的窗口为10月5日至1月3日；主场景的义务仍是同三次，目标仍覆盖四个月。
- 一笔 CASH 账户初始500,000，GOAL现金账户初始0；本主场景的目标明确绑定这笔同一CASH账户，零GOAL账户保持未归属现金0。信用卡账户账面现金不计入现金总额。初始无持仓、无UNKNOWN、无预留、无其他账户余额/目标/义务。必要资产持仓账户在初始固定银行导入时为0，不能为了触发工资而新建“工资账户”。
- 房租确认金额20,000，每月15日。confirmed/valid_from为10月3日，故窗口有10月15、11月15、12月15三笔，合计60,000；1月15在窗口外。已出账信用卡bill总额10,000、paid=0、due=10月20，最低还款额即使更低也不替代全未付总額。无其他已发生但缺来源的往月义务。
- emergency_buffer=20,000，整个窗口有效。
- 生活准备金按**现 LivingReserve 算法**生成10,000，不是不存在的固定amount字段：horizon_days=10，lookback_days=60，quantile=1.0，essential_categories=[food]，exclude_one_off=true，extra_buffer=0；8月5日至10月3日每天有1,000真实BANK_CONFIRMED消费+用户类别确认，每个账户每天有完整覆盖。51个连续10日窗口均为10,000，nearest-rank=51，结果10,000。初始现金500,000由固定历史入账/消费重建，既有opening不后改。
- 主目标 target_cents=1,000,000、deadline=2027-10-01、月度[min,target,max]=[5,000,10,000,15,000]；priority.minimum_cents=0、reducible=false、deferrable=false；不跨目标挪用。主场景A中已完成公开确认/目标创建，ownership=0、October contribution=0且有真实零证明；B开始时尚无此目标。

`domain/boundary.py` 在T0先计算全部窗口目标最低，随后每点使用同一最低储备总额。四个月是**10、11、12月与1月1–2日**；部分月份也算一次月最低。目标最低为：

`min(target-allocated, max(Σ各覆盖月 max(0,min-month_contributed), priority.minimum-allocated))`。

只有当前月可有已知贡献；本例未来三个月贡献都是0。allocated与当月contributed是不同事实，不能用累计归属替代当月证明。goal_cash来自已归属现金；goal principal不再次当现金扣减。基础生活/应急保护在本窗口恒定；未到账未来工资、收益不进当前cash或safe_idle。

银行工资/普通消费入口目前缺失，须按前述E2E设计补trusted external-fact service/新持久原点与守恒双腿。这里的工资+200,000、消费-350,000是**待补真实银行事件**；禁止用内部转账、虚构消费义务、修改保护参数或改已有OPENING替代。

## 为什么本例可以逐点手算91日最小

若没有新的保护生效/本金到达，每次到期义务会同时减少cash与remaining obligations相同金额，因此margin不变。它是确定性预测中的付款假设，不能说未来已产生银行支付或回执。goal_minimum在本次trace按窗口一次算好，不在11月1日自行减掉一个月；未来目标贡献不能被预填。

定义保护向量 `V=(obligations,living,emergency,goal_cash,goal_minimum)`。主A2/A3以后 `V0=(70,000,10,000,20,000,10,000,15,000)`，总保护125,000。A3实际现金450,000，无提交的T0退出计划：

| 冻结T0预测检查点 | trace现金 | 剩余义务 | 其余保护合计 | margin |
| --- | ---: | ---: | ---: | ---: |
| 10月4日BEFORE_PAYMENT | 450,000 | 70,000 | 55,000 | 325,000 |
| 10月15日AFTER_PAYMENT | 430,000 | 50,000 | 55,000 | 325,000 |
| 10月20日AFTER_PAYMENT | 420,000 | 40,000 | 55,000 | 325,000 |
| 11月15日AFTER_PAYMENT | 400,000 | 20,000 | 55,000 | 325,000 |
| 12月15日AFTER_PAYMENT | 380,000 | 0 | 55,000 | 325,000 |
| 1月2日AFTER_PRINCIPAL | 380,000 | 0 | 55,000 | 325,000 |

以上是**同一个T0预测**，不是在未来日期重新GET后的余额/义务真值。真正未来读取必须核原付款/结清事实、当时新91日窗口及来源覆盖。273点中最小325,000首次在T0 BEFORE_PAYMENT出现；同日付款先发生，本金再到达，同一天晚到本金不能补此前负点。

T0/T1申购的 `purchase_exit_status=UNSUBMITTED_PLAN` 只是规划元数据，现 `execution_projection.py:_purchase` 不给它实际本金可用证明。即使纯candidate_boundary放入预期退出日，首页实际boundary也不能在1月2日加回250,000或把这个计划称银行承诺。固定合同的明确CONTRACTUAL_MATURITY及已受理本金证明另按真实来源核验。

## 链A：工资到账后的自动安排

初始确认了目标与general_idle_funds资产授权。为唯一确定申购额，资产授权 max_auto_managed=single_action_cap=250,000、allow_auto_recovery_without_penalty=true、max_lock_days=0、max_redemption_delay_days=0、principal risk=0，允许集合仅CASH_MGMT_T0（可保留CASH作不申购fallback）。一个当前已知有效T0产品：minimum_purchase=1,000、lock=0、delay=0、本金不波动、明确planned-principal-return-v1、条款/catalog annual_yield_bps均为365。90日候选比较收益为`250,000×365×90÷(10,000×365)=2,250`分，严格正且不计当前安全现金。无既有managed/pending/reserved；最终DTO与精确catalog原文尚待fixture冻结。

该目标已经在工资前被用户确认，因此工资origin时间晚于confirmed/valid_from，合法进入eligible new funds。500,000旧现金不是这次新目标分配的新增收入；旧收入如有，也显式已耗尽，不得靠helper重新调available。

| 阶段 / 真实触发 | 现金 | 保护V | 总保护 | minimum_margin / safe_idle | 目标现金 / 本金 / allocated | general已配置 / 总已配置 |
| --- | ---: | --- | ---: | --- | --- | --- |
| A0 尚未到账；未来工资预测200,000 | 500,000 | (70,000,10,000,20,000,0,20,000) | 120,000 | 380,000 / 380,000，READY | 0 / 0 / 0 | 0 / 0 |
| A1 真实工资CREDIT200,000及完整投影 | 700,000 | 同A0 | 120,000 | 580,000 / 580,000，READY | 0 / 0 / 0 | 0 / 0 |
| A2 原GoalIntent prepare→execute，目标储备10,000 | 700,000 | (70,000,10,000,20,000,10,000,15,000) | 125,000 | 575,000 / 575,000，READY | 10,000 / 0 / 10,000 | 0 / 0 |
| A3 原PurchaseIntent prepare→execute，申购250,000 | 450,000 | 同A2 | 125,000 | 325,000 / 325,000，READY | 10,000 / 0 / 10,000 | 250,000 / 250,000 |

A2手算：已知当月contributed从0变10,000，当前月最低5,000完全满足；未来11、12、1月最低仍3×5,000=15,000。goal_cash增加10,000、goal_minimum减少5,000，所以保护净增5,000，而非净增10,000或不变。目标规划建议受target=10,000、max=15,000、eligible=200,000与安全限制，故唯一最大安全建议是10,000；不会自动存max15,000。

目标存同一CASH账户时仅发生真实GOAL_OWNERSHIP和INCOME_LOCATION变化，现金不搬；若实际绑定独立GOAL账户，cash source -10,000 / goal account +10,000，总现金仍700,000。最终fixture选择一种，不混用账户口径。

A3手算：一般可用cash funding=700,000-已归属goal_cash10,000=690,000；金融占用上限575,000。max_managed剩余额250,000、单笔上限250,000把真实建议锁为250,000，且产品满足min与正收益。现金/本金经济腿-250,000/+250,000。新工资的190,000可用位置被此次申购消耗，10,000已经ASSIGNED；即使另外60,000取自旧一般现金，不能将申购额全部伪称工资available。原income总额仍200,000，位置为assigned10,000+spent190,000。

此时总现金450,000已包含目标现金10,000；配置本金250,000不再在现金卡。不能把“现金+goal allocated+managed principal”称总资产：它会重复计目标现金。正收益只参与候选比较，不预计到账扩大safe_idle。具体AUTO及“为何没询问”须真实AutonomyDecision/trace证明；本文不手造等级/理由或哈希。

## 链B：新目标首次确认、零归属、随后新增资金

独立新测试库采用同样500,000现金、70,000义务、10,000生活、20,000应急，开始无目标/申购。目标自然语言编译需生成本文已冻结语义；具体deterministic文本/normalized configuration/reviewed_hash尚待真实编译确认，不能照抄另一个测试的不同金额文本后沿用这些数字。

| 阶段 | 现金 | (goal_cash,goal_minimum) | 总保护 | minimum_margin / safe_idle | allocated / principal | 正确边界 |
| --- | ---: | --- | ---: | --- | --- | --- |
| B0 未创建/仅compile候选 | 500,000 | (0,0) | 100,000 | 400,000 / 400,000 | 无目标 / 0 | READY；候选不是ACTIVE、没有自动权限 |
| B1 accepted首次policy确认，但尚未POST goal | 500,000 | 暂无法证明 | 不返回有效分项 | null / null | goal仍不存在 / 0 | 当前算法MISSING_GOAL_OWNERSHIP→INSUFFICIENT_EVIDENCE；不能提前显示零目标完整边界 |
| B2 原version/account真实POST goals | 500,000 | (0,20,000) | 120,000 | 380,000 / 380,000 | 0 / 0 | READY，零GOAL_CASH/GOAL_PRINCIPAL新初始与零当月贡献证明；钱未挪动 |
| B3 后续真实工资200,000投影 | 700,000 | (0,20,000) | 120,000 | 580,000 / 580,000 | 0 / 0 | READY；first confirmation不把500,000旧现金认领为目标 |
| B4 原公开GoalIntent完成10,000储备 | 700,000 | (10,000,15,000) | 125,000 | 575,000 / 575,000 | 10,000 / 0 | READY；新增资金来源与当前月贡献都有银行证明 |

重复原confirm/create/execute返回原结果，无第二次归属/分录；不能靠“再次创建目标”重置已经归属的金额。B1→B2的变化是来源完善与未来最低承诺，不是目标资金已经到账。401只验首页对这些真实状态的反映；候选编辑/确认UI留402。

## 链C：普通消费导致缺口、T0真实恢复

从A3真实持仓分支接续（同日，无新增策略/义务保护，原250,000一般T0持仓来源完整）。普通消费C=350,000必须新增CONSUMPTION银行双腿：用户cash -350,000、实际收款方/清算 +350,000；禁止额外确认一个350,000 recurring策略制造缺口。该消费为非food一次性用户事实；它发生在今天，未进入T0的昨日截止生活历史；未来覆盖/用户分类须真实更新，不能让估算器或银行角色凭空变成food。

| 阶段 | 应用现金 | 总保护 | minimum_margin / safe_idle / deficit | 目标现金 / 本金 | 一般已配置本金 | 状态与事实 |
| --- | ---: | ---: | --- | --- | ---: | --- |
| C0 消费前 | 450,000 | 125,000 | 325,000 / 325,000 / 0 | 10,000 / 0 | 250,000 | READY、原真实申购receipt |
| C1 普通消费完成 | 100,000 | 125,000 | -25,000 / 0 / 25,000 | 10,000 / 0 | 250,000 | LIQUIDITY_RISK；缺口来自cash下降，而非保护额被修改 |
| C2 只读preview提出全本金T0退出 | 100,000 | 125,000 | actual仍-25,000 / 0 / 25,000 | 10,000 / 0 | 250,000 | 原完整本金250,000无损、standing允许；conditional projected现金350,000、margin225,000；GET零写 |
| C3 原POST recovery独立结算且投影 | 350,000 | 125,000 | 225,000 / 225,000 / 0 | 10,000 / 0 | 0 | RECOVERED；cash+250,000/POSITION-250,000；一个原operation/receipt、PRINCIPAL_RETURN |

恢复是整笔本金250,000，不能按25,000缺口手造部分赎回。消耗的是用户已有一般现金，不改既有目标归属；本金返本不是新工资、不恢复原income AVAILABLE。C3未来义务支付同额消除保护，所有最小检查点仍225,000。只展示被核实际结果与原权限，不能评价这笔消费该不该发生。

## C的T1、有损ASK、UNKNOWN最小替代分支

以下各自从相同A2状态通过真实申购建立原250,000持仓，再发生同350,000消费；**不是事后修改A3产品、购入策略、available_at或原receipt**。不同库或独立fixture分支分别选择产品/权限；每条连续事件内不reset。

| 分支 | 单独需要的真实产品/权限 | 独立数字与正确可见状态 |
| --- | --- | --- |
| T1消费前 | 仅允许CASH_MGMT_T1；lock0/delay1，原权限max_delay1与无损恢复true，管理/单笔均250,000 | 申购后的实际现金450,000、保护125,000、safe325,000；UNSUBMITTED退出计划不作到期承诺。消费后现金100,000、min-25,000、safe0、deficit25,000 |
| T1 preview/受理 | 250,000原本金、fee/loss0、真实受理available=T请求+1日 | 今日三个点仍-25,000，明日AFTER_PRINCIPAL以后条件margin225,000；整窗min仍-25,000，所以**PARTIAL_RECOVERY_AVAILABLE**，不能写AUTO_RECOVERY_AVAILABLE/整窗READY。受理后top PENDING_SETTLEMENT、现金100,000、receipt absent |
| T1到期仅GET | primary采用205公开recovery路径，真实position.REDEEMING和available_at已保存 | GET不改ACCEPTED/不追加settlement/不造receipt，现金仍100,000。在available_at≤now但未结清时，`boundary._positions`产生UNRECONCILED_POSITION_AVAILABILITY，boundary min/safe/deficit为null、分项空、source不足；top仍PENDING_SETTLEMENT。不能保持先前0当“当前已证明零”或显示现金350,000 |
| T1原POST实际结清 | 相同key/run/operation；银行与投影真正完成；当前账单/目标/月贡献/历史覆盖均完整 | 现金350,000、本金0、保护125,000，10月5日至1月3日min=safe225,000、deficit0，RECOVERED；一份原receipt。若覆盖不足则null，此数值以明确来源完整为前提 |
| 有损定存消费前 | 从原授权实际购买FIXED_DEPOSIT，term60/settlement0、risk0、明确保证本金、lock与standing上限一致，原权限允许提前有损但仍须具体确认 | 现金450,000/一般本金250,000；合同到期12月3日来源可核，但最早点margin仍325,000。消费后最早点-25,000；未来合同返本不能解决今日缺口 |
| 有损显式quote/ASK | 原bank EARLY_WITHDRAW报价：principal250,000、fee100、loss1,000、net248,900，原return account/terms/product/version/quote时点/hash一致 | ASK_ONCE，cash100,000、本金250,000、min-25,000/safe0；无自动银行请求/receipt/经济腿。不能用allow_early_withdrawal_with_penalty=true省本次ASK，也不能从产品bps猜报价 |
| 有损原后果确认后execute | prepare→真实USER_ACTION_CONFIRMATION accepted+原effect_hash→原execute | 独立结算：POSITION -250,000，CASH +248,900，FEE +100，LOSS +1,000；经济delta总和0。现金348,900、本金0、保护125,000，min=safe223,900、deficit0；ASK原等级保留、confirmation_satisfied=true，receipt真实净额/fee/loss可核 |
| T0投影失败UNKNOWN | C2后原银行SETTLED，真实应用projection保存点失败，无receipt | 银行cash350,000/POSITION0；应用账面cash仍100,000、旧本金投影仍250,000；原action UNKNOWN/bank SETTLED。完整独立银行/应用来源核验不足，首页金融上限null，不能把银行新cash和应用旧归属拼为safe225,000；原身份待对账 |
| UNKNOWN原操作修复 | 解除真实故障、同原action/effect/operation重试投影 | 不再发生第二次cash+250,000/POSITION-250,000；应用cash350,000、本金0、保护125,000、min=safe225,000，一份receipt。无新工资origin、无新赎回身份 |

T1主选205路径是为给出可明确定位的pending available证明语义。301通用execute的ACCEPTED分支只返回None、未进行205的待结算position投影，因此某些未到/到期读取的boundary source状态可与205不同；必须以实际选择的路径冻结fixture，不把两入口状态拼接。未结清资金都不进入今日现金。

T1服务端clock前移会移动living历史窗口。最小来源前提是银行对10月4日已发生事实/零额日期给完整覆盖后继，不能让GET自行补证据；10月5日51个10日窗口中至少有未受该新日影响的10,000窗口，q=1仍为10,000。若真实来源并未覆盖新日，正确结果是INSUFFICIENT_EVIDENCE，不能为了保住225,000手算预期改旧coverage或加伪造支出。

## 两个小型交叉口径手算（非新黄金链）

**目标本金的包含关系。** 在A3另有真实goal-scope授权、与goal.asset_policy_id严格关联，申购已归属目标现金中的5,000（是否选中与具体源协议需fixture单独冻结）。现金450,000→445,000，goal_cash10,000→5,000，goal_principal0→5,000，allocated仍10,000；保护125,000→120,000，minimum/safe仍325,000。general managed250,000，total managed255,000；其中5,000同时属于目标归属本金，不能重复累加成额外资产或把target_cash仍写10,000。后来此goal本金真实返回时cash与goal_cash同增5,000，一般可自主margin不增加。

**当前保护不等于窗口最坏保护。** 独立防错分支从A3起，已在T0明确确认额外emergency_buffer40,000、valid_from=11月1日；它是未来已确认保护，不能冒充消费。当前保护仍125,000、今日margin325,000；11月1日cash420,000、义务40,000、life10,000、emergency60,000、goal_cash10,000、goal_min15,000，总保护135,000，margin285,000。以后付款抵消同额义务，最坏仍285,000，故safe_idle=285,000，而`450,000-当前保护125,000=325,000`。这验证首页必须读全273点最小值，不能只画当前瀑布图强凑上限。

## 待冻结的依赖与独立验收方式

已可手算的数字是有效完整来源下的财务必要条件，**不是权限/成功保证**。需要后续真实验证后冻结：external事实原协议/migration、完整income发行/消费位置与audit anchors；living覆盖的实际manifest/用户分类来源；唯一catalog/授权/goal关联的normalized配置；server trusted clock E2E runner；dashboard同RR聚合DTO与managed/待对账完整性字段。UUID、原content/input/boundary/request/effect/trace/audit hash和具体原因文字不能靠手算臆造。

选额与原可执行状态虽已给约束和算术，prepare仍必须按现来源、曝光、权限、产品、预留/到达时点复核；本文不能宣称“现接口已经能跑工资/普通消费”或“所有AUTO字段一定如此”。source不足时应按现结果null/空分项展示，而不是拿这张手算表覆盖真实失败。

未来 oracle不用被测函数计算expected：用固定S/C/P/net及literal银行经济腿、原余额、独立91日支付表/目标月计数推导，逐项比对真实API、完整银行证明和Edge页面。核全表读前后相等（GET/preview/解释/刷新）、原opening/原evidence不改、一次经济效果/一次receipt、same原identity重试、旧audit仍可核。持有同一RR/可信now的聚合与oracle应同时验证，不能用多个独立GET拼出“同一状态”。

实际Edge截图/网络/响应身份、全表/银行腿摘要、运行命令exit及源码hash均待实现后取得；现工程health PASS与先前服务测试不计本手算矩阵的业务PASS。此文件没有运行证明，没有实施401/404，也没有改变任何已冻结设计或代码。
