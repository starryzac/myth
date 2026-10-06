# 钱途有界：30页技术白皮书当前功能版

状态：CURRENT_FUNCTIONAL_DOCUMENTARY_DELIVERY / WORKING_NOT_ACCEPTANCE。
资料捕获UTC：2026-10-06T00:31:08.686725+00:00。全部资金模拟；正式21/92、FULL PENDING不变。

分页由pages.json与实际PDF共同记录，页数不代表内容验收。

## 第1页　01 摘要与版本界限

> 钱途有界当前功能版技术白皮书

### 研究问题

个人资金自主安排不能只由余额或语言模型建议决定。需要将有来源的事实、明确用户策略、截止前现金覆盖、产品流动性及支持动作共同约束，并保留具体动作与原回执。钱途有界给出确定性模拟实现，把自然语言候选、规划、权限和执行分开。

### 当前贡献

系统已有不可变策略生命周期、年度保护、联合目标、有限不确定世界、持久一次一问、原消息当前观察、银行/app分层执行与对账、原键恢复和有界反事实模块。贡献是当前可审查实现路径，不是实际客户收益、全部功能验收或未经测试的算法最优性。

### 证据等级

本书区分SOURCE_IMPLEMENTATION、ACTUAL_SCOPED_RUN、DIAGNOSTIC_WITH_SOURCE_CHANGE、HYPOTHESIS和MISSING。定向纯测试与HTTP夹具不充当资金效果；历史PG与历史录屏不自动变当前源验收。正式关闭21/92，FULL项仍PENDING。

### 文档可重建

本包保存正文、分页数据、源路径/SHA和实际PDF/PPTX。引用ID对应sources.json；复制的public原件按原字节留存。资料与代码在独立开发中可能更新，本书只对捕获时点成立，不宣称整仓运行冻结。


来源：[S01](sources.json), [S04](sources.json), [S05](sources.json), [S38](sources.json)。

---

## 第2页　02 问题定义与术语

> 先确定事实、权限和状态的所属层

### 事实与意图

Fact回答有证据发生的状态，Evidence保原来源、owner、有效时间和观察时刻。用户声明是希望如何安排；确认是同意确切配置或经济效果。MODEL_INFERRED、USER_DECLARED、BANK_CONFIRMED不能互相借用。

### 权限与容量

safe_idle_cents是财务条件下的当前上限，不是授权额度；conditional cash是尚未执行/到账的规划假设，不是实际现金。AUTO必须同时满足五集合，缺任何证据或支持动作均不能默认为允许。

### 两种恢复

资金恢复解决保护截止前现金缺口；原键协调解决响应丢失/投影中断。前者要当前规划和权限，后者要原银行身份与原效果。混淆两者会误用新授权、重复扣款或把原结算当未发生。

### 三个成功口径

SETTLED属于银行经济结算，SUCCEEDED属于应用投影，VALID属于特定范围typed审计。HTTP200不替代这些状态；MATCHED也不替代动作执行或现实经济证明。UNKNOWN不等于终败，null不等于0。


来源：[S02](sources.json), [S21](sources.json), [S22](sources.json), [S24](sources.json)。

---

## 第3页　03 青年场景与使用任务

> 场景是设计依据，真人需求强度仍未知

### 账户时间差

工资、房租、账单和本金回款有不同有效时点；一笔余额能否安排取决于所有保护截止。收入尚未到账、产品尚未可用、消费随后结算时，系统须保留实际时序，不把预测当现金。

### 用途与归属

长期目标的资金不只是心理标签。收入source分和Goal归属要原证据关联，已经属于目标的现金和本金不能因为一般账户缺口被暗中借走。资产放置可变化，但用途归属不自动变化。

### 用户参与点

用户先复核持续义务、准备金、目标与授权，再在新损失、歧义、新收款关系或范围变化时参与具体确认。普通消费只作为事实，不评价生活选择；界面解释保护、条件回款、原权限和拒因。

### 研究边界

当前没有访谈样本、痛点频次、转化率或用户理解改善。FULL806匿名研究工具已准备，但真人0、记录0、NOT_STARTED。任何人群普遍性、市场规模与业务效果均不在当前可引用事实内。


来源：[S01](sources.json), [S27](sources.json), [S32](sources.json)。

---

## 第4页　04 设计原则与不变量

> 确定性、证据充分、未知保留、最小权限

### 确定性决策

金额用整数分，业务时间带时区；同固定输入调用相同版本引擎得到可复算输出。模型只帮助候选与解释，不能决定金额、权限、产品安全、策略状态或执行。服务端选择可信clock，客户端不能提交成功结果或银行事实替代核验。

### 现金保护

自动动作后须满足所有硬义务；未来收入不扩大今日边界；已归属目标不被低优先级动作占用；本金实际可用前不算现金；有损支取没有精确确认不自动执行。这里是规范，不是已对全部分布证明的安全定理。

### 身份与历史

失效版本不能产生新权限，重试不产生第二经济效果。新功能不回退后来成果，不改原hash、原失败或正式模拟历史；旧LEGACY_UNAUDITED保持缺证边界，不能追补伪造起点。

### 检查策略

日常只核变更和直接风险，金融/迁移保留真实集成。初版与FULL最终当前代码集中全量；已通过且未变范围可复用，不把局部通过冒全量。功能完成与原需求关闭分开登记。


来源：[S01](sources.json), [S04](sources.json), [S26](sources.json), [S38](sources.json)。

---

## 第5页　05 事实时间与证据层级

> valid time、known time和实际银行时刻各自有含义

### 双时间语义

事实在业务上适用的valid time与系统取得它的observed_at不同。历史查询不能看到当时尚未知的证据；未来known时间须服务器拒绝。当前scope中重复读复用不能让旧状态跨请求变当前状态。

### 来源验真

同一Evidence ID不证明内容正确。必须核owner、源身份、状态、内容hash、引用及有效窗口；被SUPERSEDED、CONFLICTED或缺失的证据不能继续授予权限。同ID内容冲突保原件并拒绝，不覆盖旧hash。

### 银行与用户分层

BANK_CONFIRMED由实际模拟银行原请求/posting产生；用户声明和模型候选不能写成银行等级。资金来源、目标产权与产品条款均保持独立原引用，展示REFERENCES_RESOLVED也不是金融验真。

### 展示未知

服务返回null/UNKNOWN/issue时，前端应保具体原因，不能从金额相等或存在某个文件推断成功。证据图只绘实际edges；当前图与历史原件分开，断链和缺版本保持缺项。


来源：[S02](sources.json), [S08](sources.json), [S23](sources.json), [S24](sources.json)。

---

## 第6页　06 收入源分与目标产权

> 现金、收入来源、归属和产品本金分别守恒

### 收入源

新增收入由真实原流水/银行来源形成可分配source-fragment。金额单位是分，分配保持来源余额、已保护及已归属的完整分母，防止同一笔工资在准备金与多个目标中重复记账。

### 目标建立与分配

首次创建目标可为零归属；只有真实新增收入和当前有效确认进入后续分配。当前版本确认时点/valid_from之前的旧收入可能不eligible，所以零计划不等于缺收入证据，也不能虚构零回执来满足流程。

### 本金与现金

把目标资金购买产品改变资产放置，不把归属变一般闲置资金。本金到期前只是持仓；已REDEEMED原本金与当前outstanding分开。对账缺银行产权anchor时保UNKNOWN，应用 allocated 相等不充银行事实。

### 原回拨

跨Goal默认拒绝。专用scope与具体动作确认需核sourceGoal、destination受保护现金、当前各版本、底线、累计和单次caps；真实三腿及来源分重验。声明界面与真实执行分别证明，不能把声明视作已经回拨。


来源：[S08](sources.json), [S13](sources.json), [S15](sources.json), [S24](sources.json)。

---

## 第7页　07 完整策略DSL与目录

> 十二模板目录不等于十二类银行执行全实现

### 严格配置

模板候选按实际Schema校验UUID引用、金额类型、有效期、优先级、范围、caps及风险/锁定/到账容忍。额外字段、布尔金额、浮点金额和未知枚举拒绝。配置canonical hash属于确切版本，不用前端临时对象替代。

### 模板边界

Full八模板有持久生命周期，若干模板桥接旧MVP，LongTerm Goal沿目标模型。目录覆盖不自动意味着每模板具备执行、金融差量预览和已验收前端；未支持消费显示NOT_IMPLEMENTED/UNKNOWN。

### 规则与授权

义务与保护描述安全需求；资产/恢复/回拨权限描述允许动作。自动金额不能由“我想多存一点”直接决定；用户具体scope、max及当前来源仍由后续确定性服务重验。

### 复核入口

JSON候选可先validate和change-preview；复核reviewed hash后才confirm/change。预览hash、事实digest和具体动作effect hash属于不同协议，不能互换。候选受理不是ACTIVE或银行grant。


来源：[S09](sources.json), [S10](sources.json), [S11](sources.json), [S28](sources.json)。

---

## 第8页　08 不可变生命周期与顺序命令

> 历史命令是留存证明，当前有效性须重新读取

### 状态推进

CREATE/CHANGE/SUSPEND/REVOKE/RESUME记录原请求和期望版本，原config及history不改。当前状态、到期和引用可影响新权限；版本切换不能覆盖旧回执的原确认配置。

### 幂等与原键

客户端POST前保存完整body、稳定key、intent和hash。server by-key查询核完整原命令链，RECORDED须匹配原key/body/policy/kind/hash/receipt。NOT_FOUND非终局，响应不明和4xx都不能据此换键重新提交。

### 撤销边界

只有证据完整、未提交且物理零效果的动作才可失效。银行在途或已结算动作保原key及占用，通过原身份协调；撤销策略不是银行回滚，也不借当前授权替代原结算确认。

### 未覆盖

完整并发/强杀矩阵、各动作族撤销竞争与终局拒绝恢复仍需真实节点。客户端坏session或存储权限失败锁新写，不能删除pending来宣称恢复；跨页门防第二请求不是服务器授权缓存。


来源：[S10](sources.json), [S21](sources.json), [S22](sources.json), [S26](sources.json)。

---

## 第9页　09 自然语言候选编译

> 有限离线句式，来源与候选保持可复核

### 当前实际能力

Full编译器覆盖十二种有限中文语法，按服务器日期/时区和原引用产生严格配置、缺项、来源片段及假设。未知散文保不支持，默认关闭外部provider，不把有限grammar称通用自然语言理解。

### 可选provider门

server-only provider仅接脱敏且已识别片段；输出受独立JSON大小/深度/节点与严格Schema约束，并须与原规则canonical及引用token一致。模型不能新增银行来源、角色、确认或额度。

### 用户修订

候选应显示原话/结构化字段/差异；修改后重新validate，旧勾选和理由不沿用。首次确认产生原ACTIVE版本，目标还要实际账户与Full模型确认。不能把模板按钮改名成NL链。

### 证据边界

有限句式的直接纯/FastAPI检查是合同证据，不是真实provider、真人表达或完整B链录屏。原模板、引用未服务端验真时保NOT_SERVER_VERIFIED；当前所有确认仍由实际独立用户动作产生。


来源：[S11](sources.json), [S27](sources.json), [S01](sources.json)。

---

## 第10页　10 自主包络与分类

> 五集合交集，不以单个分数替代多层条件

### 集合定义

财务安全集来自保护曲线；用户授权集来自有效版本与具体确认；流动性集来自条款和到账规则；证据充分集核来源与完整分母；支持动作集说明当前引擎可表达的族。交集才是可自主安排范围。

### 分类语义

AUTO_EXECUTE、ASK_ONCE、ADVISE_ONLY、BLOCKED由原确定性服务返回。ASK的确认必须绑定具体经济effect；单一safe_idle或策略auto_execute字段不是全条件证明。原状态不充当前授权。

### 完整性

全局动作集合只有所有已登记producer完整覆盖且可复算时才完整。有活跃未覆盖模板、未知事实或family partial时不能把有限结果称全局complete；数值变动也不一定产生经济动作集合跨越。

### 比较边界

比较对象包含类别、金额、来源/去向、目标归属、风险/损失、到账与权限。事件去重使用原semantic协议，不能只比较按钮文案或任意结果hash。新Full版本另列source/version，不改旧协议原hash。


来源：[S01](sources.json), [S07](sources.json), [S02](sources.json)。

---

## 第11页　11 年度现金流与三个阶段

> 366个日期 × 3阶段 = 1098阶段点

### 有界时轴

初始日0和未来1—365日分别在BEFORE_PAYMENT、AFTER_PAYMENT、AFTER_PRINCIPAL观察资金。硬义务付款和产品本金回款在同日也保有顺序，不能将预计到期本金提前用于付款前保护。旧90日执行视图保持自身算法与来源。

### 保护输入

完整已登记固定/周期义务、生活应急、Goal及占用形成保守需求；缺Seasonal采纳额保null，未来income未接入或恒0不充当前安全现金。已支付覆盖或未登记债务的边界必须明确，不造“所有现实义务已完备”。

### 曲线含义

每点cash/required/margin和全周期最低余量是规划响应，不是所有日期已实际发生的银行流水。conditional本金/赎回的曲线须单独标假设，current事实与future预测不混合。

### 验收重点

检查期初/截止/到期边界、空/缺来源、同日付款与本金順序、可用delay和占用竞争。当前页面按原服务输出展示并保UNKNOWN，真实集中验收仍需最终源全量，不由1098点数量推导正确性。


来源：[S02](sources.json), [S12](sources.json), [S07](sources.json)。

---

## 第12页　12 策略变更金融影响

> 当前支持未来Dated/Periodic两模板的保守替换

### 只读输入

POST financial-change-preview保存当前owner/version和候选配置，服务在干净RRRO快照读取当前实际事实、Goal和持仓。旧版本、当前动作、原资金和历史hash不写；当前请求clock绑定新协议事实digest。

### 支持窗口

明天起的DatedExpense/PeriodicTransfer未来待付可作假设替换；今日、逾期、当期已到期月和其他保护/占用保留。其他十模板、欠源或Seasonal采纳未知时保UNKNOWN，不能给零delta。

### 差量

PROJECTED须before/after及各差量非null，delta=after−before，包括负值。after含1098点及绑定实际input的curve hash；UNKNOWN的after/deltas为null，before可保原已知。旧preview fact digest不能与新协议直接比较。

### Goal/本金说明

current allocation/principal delta=0仅说明本preview没有写事实；未来分配/未来处置为null UNKNOWN。原recorded principal与current outstanding分开，已REDEEMED当前本金0不抹旧历史。curve hash不用于确认或授权。


来源：[S28](sources.json), [S12](sources.json), [S10](sources.json)。

---

## 第13页　13 多目标联合规划

> 保持全部Goal分母与收入资格

### 当前期任务

服务基于当前Goal版本、属性、底线、已归属余额及新收入资格，输出实际8层规划结果与来源。版本确认时点之前的历史收入可能不eligible；零建议要写原原因，而不是断言缺收入或自动创建零动作。

### 优先层与约束

同一来源分受硬保护、底线、目标优先、月贡献和剩余需求约束。当前期确定性次序可复算，但不能称所有未来年份、全部随机流量的全局多期最优。期望目标额不替代已到账可分配资金。

### 执行区分

只读Joint响应不是银行grant。真实新增资金归属由原prepare/confirm/execution路径建立，保存effect与原收入来源；Full额外属性需要明确双hash确认，Goal旧策略与Full模型缺任一版本保持UNKNOWN。

### 当前显示

目标页保留真实已归属，与projected计划分开；365 reserve、延期下界、当前版本和缺模型分别解释。UI synthetic夹具验证reader/展示，不证明银行金额或目标可按期达成。


来源：[S13](sources.json), [S08](sources.json), [S04](sources.json)。

---

## 第14页　14 动态目标节奏与执行

> 用户[min,target,max]约束当前原动作

### 只读节奏

动态储备按原Full模型、目标剩余需求、月资格及真实贡献/收入计算，不从未来预测收入产生当日授权。UNKNOWN/EXPIRED/INACTIVE和未实现执行状态保原理由。建议额与实际已分配分开。

### 实际请求

动态prepare六字段绑定goal、原policy版本、原model Evidence ID/hash、epoch和稳定key；server重新读取来源与计划，客户端不供应金额或proof。原Action执行/确认仍沿旧原接口，新proof不改原历史默认算法。

### 恢复证明

by-key返回原六字段/client hash、完整原Action.request/server hash和ConfirmationGrant。确认核原Evidence时刻，不能把过期consent当live grant；AUTONOMY/status不代替原确认。NOT_FOUND保原请求和未决工作区。

### 未覆盖

新path直接合同与接缝已交，但本材料未纳入最终当前动态动作银行完整节点。时间deadline、当前实际model缺失和未来变化可能拒绝，不能承诺每个Goal当前都有可执行额。


来源：[S29](sources.json), [S08](sources.json), [S13](sources.json)。

---

## 第15页　15 冲突诊断与最小修复

> 候选是解释，不是自动重新分配资金

### 诊断范围

原goal/保护/义务组合出现冲突时，服务读取当前源和完整分母，给冲突位置、目标底线和可行修复候选。缺模型、usage UNKNOWN或candidate null按原响应展示，不推断不存在的可行金额。

### 修复意图

调整目标期限、降低建议、暂缓低优先级任务或申请合法回拨可改变规划可行性；修改事实/目标属性与真实回拨分别须原明确确认。界面暂没有候选就展示原因，不默认选择最省事的方案。

### 跨Goal的门

只有当前已存在CrossGoal规则和专用scope确认允许的来源/目标范围可提出回拨；核owner、epoch、双方原/Full版本、最低保障、受保护现金目的、紧急条件和累计caps。旧声明不是当前可执行授权。

### 精确限制

当前冲突/修复预览不表示全部多期最优解，也不自动调用银行。实际回拨每次具体effect及来源分仍重验，缺真实oracle/全冲突案例时不能声称“所有目标冲突已自动修好”。


来源：[S14](sources.json), [S15](sources.json), [S13](sources.json)。

---

## 第16页　16 资产目录、期限相容与优化

> 原产品条款不随反事实或界面候选改写

### 目录身份

产品代码、版本、类、最小申购、延迟、锁定、风险、本金与条款原hash保持原来源。T0/T1/fixed是模拟目录概念，不发布真实在售产品推荐、收益率或工行授权产品承诺。

### 候选过滤

用户作用域、完整财务保护、可用时刻、风险与锁定先决定可适配候选，再在真实允许范围内优化组合。最小申购如未被当前planner消费不可宣称改变结果；无持仓/无适配参数影响应给明确原因。

### 组合证明

Full optimizer与build形成原组合、batches和组合hash。容量只反映规划，不是银行许可；整组版本和确认在prepare及execute重验。期限分桶和续期只读结果不能证明每种期限当前可买。

### 到期再计划

原到期银行operation/action/receipt先验真已实收，再按当前总事实/scope/策略/目录重算新决策。原purchase意图可走当前prepare二次核验；旧成熟回执不沿用为新购买权限，不自动提交。


来源：[S16](sources.json), [S17](sources.json), [S04](sources.json)。

---

## 第17页　17 多资产整组与固定批执行

> UNKNOWN保原组合和原批，后批停止

### 准备与确认

prepare body包含原full/MVP/Goal期望版本、epoch、规划模式及稳定key。完整组合hash来自server，确认保存accepted及原reviewed whole hash；原consent留原body/hash/Evidence，不把history receipt称currentauthority。

### 固定批次

execute-next必须指明expected_batch_number和expected_action_id。该批已有原receipt时返回原read，不推进后批；越序或未确认拒绝。前批UNKNOWN只能读原portfolio/key/action，不能新enqueue、新金额或替key继续。

### 银行与应用

每批保原effect、bank key、receipt和trace；真实银行commit后响应丢失，银行可能已SETTLED而应用无receipt。原GET不能擅自修账；明确协调同原经济身份，后续批只有前批完整核实后才允许。

### 已有诊断边界

旧单资产实际节点完成原组合与UNKNOWN/fixed replay负例，但运行中相关shared源变化，只是诊断。source稳定的新银行节点仍需集中取得；旧500及历史clock门失败原件不改。


来源：[S17](sources.json), [S21](sources.json), [S22](sources.json), [S34](sources.json), [E03](sources.json)。

---

## 第18页　18 资金恢复、损失与deadline

> 今天需要现金不等于产品已经到账

### 无损范围

Full Recovery从当前受保护缺口和实际持仓、scope、版本及quote推导可恢复额，不能接客户端amount/time/结果。当前零损失whole-position T0/T1范围与其他部分/组合/到期分开，未支持保NOT_IMPLEMENTED。

### 损失确认

固定早退的fee/loss绑定原quote和effect。无本动作明确确认不执行；报价过期只能拒绝，不刷新历史quote/hash或借另一笔确认。只读条件回款和真实执行本金到账保持不同字段。

### deadline

规划估计可用时刻与不可变hard bound区分，准备与首次银行受理会复核。trusted固定clock能通过不证明任意下一时刻仍可完成；实际wallclock越界LIQUIDITY_RISK应如实展示，不隐藏为网络错误。

### 身份与恢复

新604仅actual local USER和原scope权限，服务强制ASK及原用户consent。丢确认/执行响应从exact original key读取原request/effect/consent/Action，未知不清门；历史结算重验不授当前权限。


来源：[S30](sources.json), [S21](sources.json), [S22](sources.json), [S26](sources.json)。

---

## 第19页　19 有限不确定世界与反事实

> 世界分母完整，候选不继承旧单次确认

### 输入空间

用户提供小范围变量定义，由server真实base action/context形成完整枚举，最多128世界。每个世界只改允许字段，调用原事实、规划、权限与安全引擎。无剪枝或客户端world结果，不能计算“安全概率”。

### 原确认隔离

原确切动作单次确认只属于原effect。反事实重新产生内存候选，不传旧action_id/effect/exact consent；已提交或UNKNOWN等状态不能作为新反事实执行的base。世界输出没有资金写入、Evidence或grant。

### 未知与拒绝

foreign引用、缺银行来源或不支持配置保持UNKNOWN，仍在全世界分母。BLOCKED是当前条件拒绝，不删除后宣称动作全同。行动类别、金额、来源、归属、损失、时刻与权限共同定义语义。

### 场景模拟器

独立707允许明确有界现金/应急/产品延迟假设，并用server引擎计算；HYPOTHETICAL不能改原产品条款/真实事实，当前未覆盖完整lock/risk/optimizer时必须标限制，不称完整可购性。


来源：[S18](sources.json), [S01](sources.json), [S02](sources.json)。

---

## 第20页　20 最小介入问题与持久revision

> 答案筛选世界，不替代金融确认

### 问题选择

在有限动作分歧上计算minimax问题及固定tie；只问当前影响动作的问题。一个世界完整枚举仍不代表现实不确定性完备，额外未知来源和未支持动作保限制。静态阈值概率或模型置信度不替代该选择。

### START / ANSWER / REFRESH / CLOSE

每个原命令保存request/epoch/revision/key和原receipt。回答后重新计算，来源变化产生当前revision或REBASE，原答案不跨旧source充授权。CLOSED保原历史问题但current question/fresh source为空，不再refresh/answer。

### 响应丢失

两个只读lookup覆盖START及session内命令，返回精确原envelope/hash/receipt与当前revision分列；不能用latest清早先pending。NOT_FOUND_NOT_FINAL与replacement_allowed=false保原键，不自动POST或制造第二session。

### 解释界面

一次一问显示当前问题、原答案/历史和具体未覆盖；所有authority/execution标识false。关闭、ACK或“已回答”都不是具体资金效果确认。最终source的六E2E与真人负担结果仍缺。


来源：[S19](sources.json), [S18](sources.json), [S02](sources.json)。

---

## 第21页　21 介入通知与当前观察proof

> 原payload留存，当前来源证明单独追加

### 生产接缝

Question command提交后独立producer读取fresh RRRO当前ASKING workflow和原VALID Trace，再调用原observe。稳定key绑定epoch/session/revision/run/pending question hash；生产失败仅诊断，不掩盖已commit问答，也不换新观察key。

### 同语义refresh

新有效观察可证明同semantic question当前仍成立，旧payload/hash保原件。只要原PENDING尚未claim/ACK且新proof真实有效，消费当前观察；旧INVALIDATED/ACK/已claim终态不复活，明确LEGACY_TERMINAL_SOURCE。

### 一次消费

observe、deliver/claim、ACK、answer各自原命令。首次present_once才提示，再启不二次弹；GET不claim。新proof不自动DELIVER/ACK或宣称人已见，原message来源与当前trace/session/revision字段分开展示。

### 全局集合变化

新GLOBAL精确版本比较完整可执行经济集合，纯数值静默不生产通知；旧有限v1与Full sources分列。活跃未覆盖family或proof未知不能产生假的全局complete/当前有效；实际HTTP新批失败继续保留。


来源：[S20](sources.json), [S19](sources.json), [E04](sources.json), [S02](sources.json)。

---

## 第22页　22 执行状态与安全协调

> 预留、银行受理、结算、应用投影是不同阶段

### 准备

server根据原planner创建Action/effect/request/DecisionTrace，preserve owner、epoch、source和policy/product版本。ASK需确切effect确认。prepared不表示已受理，预留仍保护未来截止；客户端不能提交bank result或new clock。

### 新受理门

首次bank accept前重核完整当前权限、边界、quote、产品和源。失效策略不产生新权；原银行已接受的同key动作继续协调，而不是用新版本重新创造经济身份。原hash/canonical不为新门改写。

### UNKNOWN

网络丢失和应用rollback后不能判断银行没有发生。原BankOperation、posting、fee/loss和receipt逐层核验；SETTLED应用未完成只修原投影，不第二扣款。没有原件的UNKNOWN保等待/人工核对，不填FAILED或0。

### 失败保留

真实拒绝、篡改、projection错和trigger拒绝都保存原日志/source。只修源代码再新run，不能重标旧FAILED或删负例。全部并发/kill/跨归档矩阵尚缺，当前定向链证据不作普遍可靠性保证。


来源：[S21](sources.json), [S22](sources.json), [S23](sources.json), [S26](sources.json)。

---

## 第23页　23 审计、溯源与请求内复用

> 采用原typed协议，不为性能降低验真边界

### 原链算法

审计按原canonical/event/subject/reference/checkpoint/tail方案验证，历史epoch串联OPEN与SEALED。不能用一般SHA串行拼接替代服务协议，也不借当前VALID标旧LEGACY_UNAUDITED。证据图只展示真实引用edges。

### 分母与只读

完整核验保存全部业务表、独立alembic元数据、physical tables和原行计数；RO/RR before/after数据摘要相同才说明该观测未写。超预算必须显式FAILED/UNKNOWN，不能截断历史或把样本当全库。

### 同请求scope

audit/history复用限同干净RRRO Session、同事务和完整行digest未变；原地JSON改动、dirty/write或scope退出必须失效。此优化不保存跨请求授权、原bank当前真值或用户principal cache。

### 可解释而非证实经济

Trace和Receipt证明特定原服务判断/投影范围；经济实证还需独立银行原件、完整腿与一致性。服务receipt_verified不应展示成economic_verified，外部签名锚与生产运维特权攻击尚未证明。


来源：[S23](sources.json), [S24](sources.json), [S02](sources.json), [S35](sources.json)。

---

## 第24页　24 三账只读对账

> 应用减银行，缺原件保持null

### 观察范围

当前报告在RRRO读取八owner表，先核actual_count，再完整captured_count。普通表和postings有明确容量门；超容量保原分母和UNKNOWN。银行连续ledger、应用projection和EXACT audit分别调用原验证器。

### 现金/本金/归属

应用余额−bank余额是difference；wrong head identity保原引用但子项MISSING/null，不因金额巧合标MATCHED。Goal owned position与原产权Evidence单核，legacy缺银行anchor保UNSUPPORTED；信用卡债务与收益估值不是本比较范围。

### 原动作状态

BANK_SETTLED_APPLICATION_UNRESOLVED保原key和实际腿；PENDING_BANK、PREPARED_NO_BANK_OBSERVED、NO_EFFECT_OBSERVED_NOT_FINAL分别展示。银行原REJECTED且核零腿/零回执才能填actual0，不能由无记录假定终败。

### 人工核对

报告没有修账/retry POST；MANUAL_REVIEW_REQUIRED只是本只读推导，不声称人工已经处理。MATCHED需要完整分母、ledger/projection/audit与issues为空，仍不表示所有准备动作已执行或现实经济效果。


来源：[S24](sources.json), [S31](sources.json), [E02](sources.json)。

---

## 第25页　25 安全、身份与隐私

> 本地USER是模拟主体，不是完整生产身份系统

### 服务器身份

固定本地username/secret在server验证，签名HttpOnly SameSiteStrict短会话只含模拟USER。公开请求不能选择SYSTEM/DEMO_ADMIN，cookie不充银行授权。secret不存session/localStorage、日志或DOM回显，未配置诚实401。

### 权限最小化

每次金融请求核原用户、scope、版本、Evidence和具体效果；没有跨请求授权cache。Agent不新增收款关系，临时外付与有损行为须真实USER明确同意。新UI pending只防第二写，不替代server权限判断。

### 事实与模型隔离

provider默认离线；敏感银行原件不直接外发，识别片段脱敏。模型输出受严格schema/source/config校对，提示注入不能生成角色、金额、permission或成功。有限PII规则不等通用隐私保证。

### 实际风险

现security-check扫描Ruff S规则报告106项，未静默豁免；当前无完整依赖漏洞扫描、渗透/角色管理、真实生产审计或外部锚验证。部署离线与容器出口需真实probe，pull_policy或内部网标签本身不证明整体断网。


来源：[S25](sources.json), [S26](sources.json), [S33](sources.json)。

---

## 第26页　26 工程交付、部署与页面

> 本地模拟可调用模块与最终验收分别登记

### 模块组织

单仓API/Web/contracts，models/迁移、domain确定性算法、services事实/权限适配和API strict DTO分层。当前实际OpenAPI由Main生成并独立check；手写client成功shape或模拟bank回执不能替代真实合同。

### 客户端写入门

资金family POST前持久完整原body/key/hash/owner/epoch，网络/解析/4xx不自动清门。原GET核确切receipt后才解除pending，未决PLANNED工作区仍阻其他资金/演示reset。坏storage锁新写；自己的只读恢复可越自身门。

### 部署保全

隔离owned bf_test项目/容器/卷，init先严格DSN guard；全空才seed，已有合法历史保留。原正式模拟库不reset/migrate，旧卷和失败保留。容器当前image/source与实际egras/DNS探测分别证明，旧镜像不称当前源。

### 页面与未覆盖

独立readers逐字段核actual flags/null/金额，手机/键盘增量保持原操作。实际手机、screenreader/contrast、最终当前浏览器和离线三链尚缺。最终九项命令/材料/真人分别验收，CI配置存在不称Actions已跑。


来源：[S27](sources.json), [S33](sources.json), [S26](sources.json), [S04](sources.json)。

---

## 第27页　27 实验设计与统计口径

> 预登记完整输入、机制、oracle与全部失败分母

### 初版实验

原24案例配额6/6/4/4/2/2，五臂B0/B1/B2/B3/P真实不同机制。原作者输入需与DEVELOPMENT whole flow分层、检查同构新颖性；改金额/标题不算独立案例。局部provider风险登记不能冒完整Scenario。

### 独立oracle

保护时轴、source分、版本/确认窗口、safe-auto机会与required evidence由独立输入推导，不导入P的判断当答案。候选违规、共同网关拒绝、实际违规分开，外生消费缺口不混系统动作造成缺口。

### 完整指标

14指标保存value/numerator/denominator/applicable_units/raw_refs；NOT_RUN或MISSING为null，零分母NOT_APPLICABLE。失败与未尝试预登记机会不消失，未完成恢复保censored而不是零时延。B0人工actor不称真人耗时。

### Full扩展

原FULL803/804/805扩大场景、基线及消融实验仍须原计划完整范围；本包不拿24槽位替代完整实验。数据缺失不画柱形、不写百分比、不作显著性结论，统计模型与不确定区间待实际设计与重复。


来源：[S06](sources.json), [S39](sources.json), [S03](sources.json)。

---

## 第28页　28 已有运行结果与性能边界

> 实际定向结果不是收益或全局安全证明

### 固定付款

E01的两个真实隔离PG节点原AUTO与ASK通过，command记录实际两个parameter节点，relatedscope稳定true/allsourcefalse。完整四legs、原键协调与只读尾部有原日志。848.02秒是pytest批时长，不能当单付款时延或benchmark。

### 其他证据

E02对账所属三节点批通过，但没有单项时长；E03资产node2826.45秒且relatedshared运行中改变，只作diagnostic。E04原0014/有限/Full/HTTP batch exit1、FAILED；后续未执行节点不能补成通过。

### 历史性能

W0三个固定开发场景每格n=1，同输入hash/零写核；长链API101.635072→19.960296秒，扩大API112.629382→138.810674秒变慢。Python峰值长链249640231→280724062字节亦增。没有P95/P99、稳定倍数或当前Full SLA。

### 当前空白

24×5和14指标正式结果、消融、当前新功能性能均未纳入验收。不能把全源变化忽略为stable，也不能将TOOL_ONLY合成检查数累加成产品能力百分比。所有本章数字都限定原run与量的定义。


来源：[E01](sources.json), [E02](sources.json), [E03](sources.json), [E04](sources.json), [S35](sources.json)。

---

## 第29页　29 真人研究与失败案例

> 研究0，失败是约束和修复的具体证据

### 真人研究

当前成年模拟研究招募、知情同意、原任务日志和访谈均未开展。匿名记录工具及研究材料只有工具证据，0参与者/0records。主观信任、理解、介入负担与接受度没有均值/评分，不能由自动化UI测试代替。

### prepare500

整组asset旧fixture历史NOW早于actualOPEN epoch，0013元数据触发器拒绝，公开500保留。新candidate仅在自己的actualseed后固定serverUTC并核opened_at，未改trigger/hash/其他global fixture。该修复只证明当前OPEN用法，不证明历史clock。

### 丢回执与不完整源

bank commit后应用无receipt应保UNKNOWN和原key，不能判现金未变；wronghead identity须MISSING/null。资产诊断通过但relatedsource变动不作稳定验收，GLOBAL批实际FAILED保原状态。拒绝与失败说明边界，不作为违规成功或全安全证明。

### 当前改进条件

每个已知问题在相关源码窄修并新run保before/after；篡改/负例不删。下一全量、真实三链、全部对照/消融、用户研究与当前性能仍需独立原件，不以材料文字填补。


来源：[S32](sources.json), [S34](sources.json), [E03](sources.json), [E04](sources.json), [S24](sources.json)。

---

## 第30页　30 工行落地假设、限制与下一步

> 范围可复核，外部效益待实证

### 落地假设

同体系账户/账单/目标/低风险产品的来源一致性，可能降低用户理解成本和机构复核成本；明确授权范围可能减少重复介入。当前无工行合作、真实接口准入、客户规模、业务收益或风险降低数据，这些假设需要独立业务校核。

### 前置验证

先完成最终当前源初版与Full软件/集成/浏览器/安全、冻结实验/消融、真人研究与材料一致性。真实银行API、身份KYC、运维权限、外部审计锚和合规均由机构独立评估，不在本地模拟中默认成立。

### 已知限制

有限中文语法、有限世界/registered actions、部分模板金融影响/执行、legacy产权与审计、条件现金和新当前路径实证仍有缺口。算法容量/目录存在不代表权限，历史素材不代表当前录屏，页数达标不代表竞赛材料内容验收。

### 交付清单

12页企划、30页本书、10页答辩、实验报告和240秒脚本及来源索引以新路径保存。903无真实完整结果、904未录当前四分钟、906外部事实验证/人工一致性仍缺；FULL901—906全部继续PENDING，旧原件不覆盖。


来源：[S01](sources.json), [S03](sources.json), [S04](sources.json), [S26](sources.json), [S38](sources.json)。

---
