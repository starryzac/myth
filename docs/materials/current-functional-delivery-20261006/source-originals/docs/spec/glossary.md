# 钱途有界：统一术语

适用于代码、API、界面与材料。此为2026-10-06术语合同，跨全产品人工一致性审查尚待；不因词表存在关闭FULL-003。

| 中文 / API术语 | 定义 | 易混点与显示规则 |
|---|---|---|
| 事实 Fact | 有来源、有效时段和观察时刻的账户/账单/持仓/流水状态 | 银行事实与用户声明分别标来源；缺证保UNKNOWN |
| 有效时间 valid time | 事实或策略在业务上适用的时段 | 不等于系统何时已知它 |
| 观察时刻 observed_at | 本系统取得该原来源的时刻 | 历史查询不能看当时尚未知的新事实 |
| 证据 EvidenceItem | 原来源、内容hash、等级和引用身份 | ID相同不证明原内容、owner或当前状态有效 |
| 候选 Candidate | 待严格校验和用户复核的配置或动作建议 | READY_FOR_REVIEW不是CONFIRMED/ACTIVE，也不是银行授权 |
| BANK_CONFIRMED | 模拟银行原件确认的事实等级 | 必须按原bank posting/请求/来源验真；无真实银行接入 |
| USER_CONFIRMED_POLICY | 对确切配置/hash/范围的用户确认 | 不是任意未来动作或新损失的一揽子确认 |
| USER_DECLARED | 用户声明的待复核信息 | 不伪装成银行核实 |
| HISTORICAL_PATTERN | 历史规律候选 | 方差/相似性不是金融安全概率；不会自动生效 |
| MODEL_INFERRED | 模型候选或受约束解释 | 不新增额度、权限、状态或执行结果 |
| 策略 Policy / version | 约束及授权的不可变原版本 | 原版本、当前有效状态、引用状态分别显示 |
| 目标归属 Goal ownership | 某笔资金属于哪个目标 | 资产放置改变不意味着跨目标转移 |
| 资产放置 Asset position | 资金持有何种原产品与流动性 | 本金、到期日、可用日与实际回款分开 |
| 保护需求 protection curve | 截止前硬义务、生活/应急/完整已登记支出的保守资金需求 | 规划上界不是已支付事实；已支付完整覆盖未知要显示限制 |
| 当前自主资金 safe_idle_cents | 有来源当前现金在全部有效硬约束后的可用边界 | 不加入未来工资、条件赎回或未到账本金 |
| 自主边界 AutonomyEnvelope | 财务安全、用户授权、流动性、证据与支持动作五集合交集 | 只看余额或单集合不能证明AUTO |
| 年度阶段点 | 初始日+365未来日期，每日期三个资金阶段，共1098点 | 1098不是天数；付款与本金到账顺序不能混合 |
| 恢复 Recovery | 使保护需求及时有现金覆盖的原范围动作 | 与原key协调丢回执是不同的恢复含义，要写明“资金恢复”或“原键协调” |
| 条件现金 conditional cash | 尚未执行或到账的无损规划回款假设 | 不能填入实际余额/今日自主资金 |
| Dynamic reserve | 用户[min,target,max]与月贡献/剩余目标内的确定性储备节奏 | 只读建议与动态执行分别标能力；逾期/低于原min不默认放权 |
| 介入 Intervention | 动作语义分歧、新损失/收款/权限等需要当前复核的事项 | 普通数字更新不自动弹窗；通知已观察不表示人已见 |
| BoundaryCrossed | 可执行经济动作集合发生变化 | 比较类别、金额、来源/去向、归属、风险/损失、到账与权限，不只比文案 |
| 一次一问 Question revision | 当前有限世界的minimax问题和确切答案原件 | 回答后重算，旧答案/候选不跨新来源沿用；不授金融确认 |
| 幂等键 idempotency_key | 某个原请求经济/命令身份的固定键 | UNKNOWN不换key；同key不同body拒绝 |
| 效果hash effect_hash | 原具体动作金额、范围、损失等经济效果摘要 | 具体确认需复核同一效果，不能只按按钮标签匹配 |
| 原回执 Receipt | 原bank/app/命令效果与身份的留存证明 | HTTP200、页面提示或当前快照不能替代它 |
| UNKNOWN | 原结果或必要来源尚未确定 | 不等于FAILED、0或“可以重新产生一个动作” |
| SETTLED / SUCCEEDED | 分别指原银行经济结算和应用原投影成功 | 必须标所属状态机；应用投影失败不否定银行事实 |
| Outbox / Inbox | 原消息投递记录及唯一消费claim | 生产、投递、claim、ACK、实际真人可见分别证明 |
| EXACT / PREFIX audit | 原完整/指定前缀的typed审计验证范围 | VALID仅对所列完整分母与协议成立，不自动延伸到未验真旧历史 |
| LEGACY_UNAUDITED | 原件存在但缺现代完整typed审计协议的历史 | 保留边界，不回填旧hash或显示VALID |
| 请求内scope | 同session、干净RO/RR、完整行digest相同的重复核验复用 | 不保留跨请求授权或当前银行真值 |
| HYPOTHETICAL | 用户改参数后确定性引擎计算的条件场景 | 不改变原账本/产品条款/实际执行结果 |
| 功能交付 | 当前范围可调用的代码/API/UI能力 | MODULE_ONLY、PG_NOT_RUN、BROWSER_NOT_RUN分别如实标注 |
| 原需求关闭 COMPLETE | 原项所需软件/实验/人工验收证据均满足 | 不能仅复用旧文件或局部测试就关闭编号 |

规范入口：[合并规格](product-spec.md)、[原需求追踪](requirements-traceability.md)、[功能优先修订](execution-amendment-v2-functional-priority.md)。实际界面/材料发现同名不同义时按此表窄修，并记录人工检查范围；不改历史原记录的字符串或canonical字节。
