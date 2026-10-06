# FULL-604 流动性恢复规划

状态：当前真实财务适配的规划函数与 GET 已交付，直接纯/类型检查通过；实际 PG 候选、完整执行/回执和完整版验收未完成，编号未关闭。原依据 `docs/spec/requirements-traceability.md:92` 与完整计划 F:1344–1346 / 12.3:697–708。

本批 source、实际检查分母、所有失败原件及 SHA 共用 `FULL-405.md`，不重复扩大成功范围，不改旧恢复器或正式历史。

## 可运行恢复顺序

新 `plan_full_recovery` 首先保留当前真实现金与完整保护后边界。actual_scope_cash_cents 是原现金账户总额/本目标 cash ownership 的显示值，包含需保护资金，不表示可任意部署金额；原 boundary 已先扣义务、生活、应急及目标保护，因此不再把同一份原自主现金减一次缺口。

实际持仓候选严格按无损 T0 → T1 → 已到期完整定存 → 需原提前支取报价的有损方案排序；每笔原报价、目录绑定、精确购买授权版本、原本金和同目标返本账户明确保留。所有合法无损候选也显示 ASK_ONCE，因为新的 FULL 确认只是规划确认，不授予原银行消费者权限。多候选累计只选择改善原负保护点的步骤，已经覆盖后不添加冗余一般现金步骤。

目标范围只把已经属于该目标的原本金恢复为同目标现金，并按原 Goal 当前确认版本及 deadline 校验；不挪其他目标资产或把该现金当一般缺口解决方案。默认时限为原首实际短缺点（一般范围）或原目标 deadline 日初（目标范围）；query 只允许提前。T1 即使未来减少风险，只要现在尚有负阶段点或不能及时覆盖原现金需求，status 仍 LIQUIDITY_RISK，逐项保留 uncovered_checkpoints、on_time=false 和 first_sustained_safe_point。

无损步骤的总条件回款单独命名 conditional_on_time_recovery_cents，有损 ASK 候选只在单独 conditional_impact_boundary 解释净额和损失，始终从 lossless_steps 排除。缺来源/版本/条款/原报价时 UNKNOWN 且未证明金额 null，不能以零替未知。挂起/已预留本金不重复提交；原到款时间已过而银行投影尚未结算时明确 reconciliation-required，GET 不自行结算。

服务 `read_full_recovery_planning` 一次 clean RRRO 读取并完成原银行、实际 income（有真实来源时）、完整 exposure、同目标归属、FullRecovery/current asset confirmation、不可变目录和当前完整审计验真。不写 ActionPlan/BankOperation/receipt，不生成新的权限、价格或账本哈希。响应 planning_only=true / bank_authority=false / execution_support=NOT_IMPLEMENTED，只有父协调器后续原执行消费者能产生完整回执。

## 范围与下一前置

实际纯证明为新35+旧恢复67项同次102 PASS / 2.58秒，最后仅 current early permission 拒绝理由单节点1 PASS /0.80秒；六文件 strict types/Ruff/format PASS，详见 FULL-405 的原 run id。纯多资产排序/目标保护/迟到/缺来源例不是新的真实产品或银行实测。

主协调器须实际运行两个新 PG 风险候选：原 T0 购买回执→实际外部消费→完整365恢复条件方案且全表零写；原定存购买→实际issued early price→100分独立损失/ASK且不进入无损步骤、过期原件拒绝不重报价。本代理未运行 PG、浏览器、全量或正式 seed/reset。

当前服务仅实际 LIQUIDITY_SHORTFALL 触发；BOUNDARY_SHRINK 的前后原曲线比较、撤销/过期专门触发与原 current permission 更细恢复合同仍需接线，不能冒充已完成。未支持分笔赎回、新 FULL 定时支出/季节储备联合硬保护、真实 T1/到期定存结算链、完整有损人工执行回执及前端完整交互；后续须按这些依赖交付，不以本规划接口代替 FULL-604 全部完成。


## 2026-10-05 实际集成补证（原 NOT_RUN 与整组失败保留）

主协调器实际运行 `docs/progress/evidence/W4/actual-recovery-original-goal-key-and-historical-boundary-readonly-real-pg-20261005T150011Z-7e0c77f9/manifest.json` 的四节点合批：**整体 FAILED，1 FAIL / 3 PASS，118.09 秒**，wrapper 120.307731 秒。两项 `test_full_recovery_planning_api.py` 实际均 PASS；另一个历史边界节点 PASS，唯一 FAIL 为主协调器新增 FullGoal lookup 的业务时钟/审计 opened_at 比较，与这六源无关。不能将原整组改标 PASS，也不能把118.09秒拆成本模块两节点专属耗时。

实际两个节点证明真实 T0 模板确认/购买回执、原工资及消费两银行腿、目录登记和当前 FullRecovery 确认；365原真实缺口与无损条件曲线分开、全物理表零写/重复读取/非法字段与放宽截止拒绝、银行余额篡改后 UNKNOWN。实际定存节点证明原issued quote原件、100分独立损失/49900分净額/ASK不进入无损步骤；当前实际原账本未改变，报价过期后 UNKNOWN、不重新发行。

该合批 scoped_source_stable=true；all_source_stable=false，仅 `apps/api/app/domain/finite_uncertainty.py`、`apps/web/src/features/onboarding-draft.ts`、`apps/web/src/pages/OnboardingPage.tsx` 并行新增/变化，原 scoped_source_changes=[]。主协调器已完成本接口注册及提前 RRRO，六源hold已解除。真实T1/成熟定存结算/多产品损失/新消费者/全量仍未实测或未接，原编号不关闭。

## 2026-10-06 新执行生产适配差量（共享接线和实际节点尚未完成）

新增 `domain/full_recovery_execution.py` 与 `services/full_recovery_execution.py`，复用原现代 `RedeemIntent` / `ExecutionEffect` / `BankCommand` / `ActionResponse` / 独立 `BankOperation` / 原投影回执。没有另建资金协议、直接写余额、调用旧 `run_recovery` 冒充新 FULL 消费者，旧规划响应和历史哈希保持不变。

`FullRecoveryPrepareRequest` 仅接受实际 FULL policy、expected version、expected epoch、position 和原 key；UUID 使用原 `UUIDReference` 严格 JSON 接缝。禁止金额、时钟、角色、报价或成功结果等额外字段。原键派生为 `action:full-recovery:<原key摘要>`，历史 GET 返回完整原请求、客户端请求 hash、Action 原 request/server hash、原经济后果/银行回执、原 USER 逐次同意和 OPEN/SEALED 原轮次。`NOT_FOUND_NOT_FINAL` 不表示可以换键，历史同意不表示现在仍有权限。

当前服务器一次 RRRO 读取实际 FullRecovery 当前版本与确认/引用、原完整恢复候选分母、银行/收入/审计、不可变目录、原资产版本/位置/报价和完整已登记 FULL 保护来源。`VERIFIED_SCOPE` 只证明更窄的身份、零费用损失、原整持仓、同目标返本和原到款界限；原 MVP 当前授权、财务重验和 `enforce_full_execution_protection` 必须另外通过，不能由该 proof 替代。源不完整保持 UNKNOWN。调用局部使用已有 `audit_read_scope`，不在 Session 或跨请求保存权限。

所有新动作要求 ASK_ONCE 和可信本地签名 USER 明确确认同一原 effect hash。确认适配先调用原 `confirm_action`，再在同用户命令守卫/写锁内保存专用 `FULL_RECOVERY_USER_ACTION_CONSENT` 原证据与 `DECISION_RECORDED` 原 typed trace；两阶段之间失败保留原动作，银行首次受理必须等完整专用同意，不能仅凭旧公共 confirm。已经存在银行操作时只沿原 Action/key/effect/receipt 恢复，允许原 FULL 声明随后撤销或窗口过期；新受理仍要重新验真当前范围，旧回执不重新授予权限。

`read_frozen_full_recovery_proof` 对新 PREPARE/CONFIRM/RESERVE/BANK_ACCEPT 录制重算 scope proof、原请求/effect、source refs 的实际 typed 原件 hash/owner/时窗/分母；它仅作为原 frozen 财务验证的附加约束。专用同意 EVALUATION 使用同算法名称，但无 execution-context，必须独立按完整原同意和父 PREPARE 验证，不能把它送入赎回财务录制解析器。

可消费函数合同：

- `produce_full_recovery_effect(engine, locked_session, user_id, action_id, body, now)` 返回原 effect、scope proof、原 marker；只在原 User 锁和 capture 内调用。
- `has_full_recovery_binding` 从原 PREPARE 识别绑定，避免删除 live marker/key 后降级到旧路径；识别不是授权。
- `recheck_full_recovery_proof(..., own_action_id=None, require_user_consent=False)` 在原确认前验真；首次 RESERVE/银行受理使用 `require_user_consent=True`。原自身 claim identity 必须精确，不排除其他动作预留。
- `prepare_full_recovery_execution`、`confirm_full_recovery_action`、`execute_full_recovery_action` 是真实原流水线消费者；未安装原 private producer 和两个 `FULL_RECOVERY_GUARDS_VERSION` 时明确 `FULL_RECOVERY_EXECUTION_NOT_IMPLEMENTED`，不造成功。
- `preview_full_recovery_execution` 与 `lookup_full_recovery_execution` 是 RRRO 读取，前者的确定性预览 operation identity 不写成 Action，不可当作最终准备 effect hash。

本批 30 项直接纯风险 **PASS / 4.89 秒**，wrapper 6.383089 秒：`W4/full-recovery-execution-final-pure-20261005T233437Z-dbc7300d`；四源 strict types、Ruff、format 均 PASS，分别 `...final-types-20261005T233437Z-14786a7a`、`...final-static-20261005T233438Z-006ea541`、`...final-format-20261005T233438Z-f8a79f8c`。四份最终原 manifest 均 scoped/global source stable=true、source_changes=[]。首次类型 2 错及历史纯 29 PASS/1 FAIL、历史类型 2 错均保留，不改标；原源码归档 `.runtime/FULL-604-first-types-red-20261005T2324Z` 和 `.runtime/FULL-604-historical-first-red-20261005T2333Z`。历史纯失败是 owner 负例已被原 `build_trace` 更早拒绝，测试期待范围过窄；调整捕获范围，原拒绝未削弱。

唯一实际 PG 候选 `test_full_recovery_execution_integration.py::test_actual_signed_user_t0_original_bank_response_loss_recovers_once_after_full_revocation` 仅 **collection 1，未运行**（原件 `W4/full-recovery-execution-historical-collection-20261005T233246Z-7a7bc993`）。设计用 actual original purchase/Full confirmation/银行消费、真实本地签名 USER、原 ASK、实际独立银行 commit 后丢响应、UNKNOWN/by-key 全物理表零写、Full 撤销及同 USER 续会话原键投影/回执恢复。不把故障字符串当成真实银行结果，必须核原 bank rows/postings/receipt。该候选 T0 链使用注册隔离业务时钟 NOW，不证明现实墙钟下“今天当前瞬间”时限可用性。

明确限制：目前仅原已授权整持仓的无损 T0/T1 生产适配；MATURE 被原 modern 域要求独立 reconciliation，费用/损失被 RecoveryPolicy 原固定 0 上限拒绝；不能用用户同意覆盖这些限制。一般缺口今日首负点的真实 deadline=as_of，随后墙钟推进即过期，保留不能及时/LIQUIDITY_RISK，不擅自延长。仅 FULL 新保护产生缺口而旧 MVP 无负点时，原 redemption 安全改善守门仍拒绝。GOAL 回款、有限多步/分笔、成熟原对账、有损专项消费者和所有组合实测仍未交付。Root 还须接原 private prepare/所有新 ASK context、捕获原 action_request、新算法支持、原 frozen 附加验证、首次银行 hook 与 API/RRRO 路由，再串行实际 PG。FULL-604 仍 PENDING，完整版/初版全量与真人研究均无本批新增证明。



## 2026-10-06 07:49 Root 原生产共享接缝

已安装私有 typed `_full_recovery_request` 与实际原整持仓意图/独立原键；同键复用验完整原请求，不重做新候选。Root所有新 PREPARE/CONFIRM/RESERVE/BANK_ACCEPT 单独保持 requires_confirmation=True，追加完整新原Action.request捕获，原默认输入/算法/哈希不变。首次 RESERVE 与银行接收均 fresh 重验 FullRecoveryPolicy/原持仓/候选/零费零损/时点，要求专门原签名 USER 同意；原FULL保护独立保留，既有银行原键恢复不调用 fresh grant。

新纯 `full_recovery_execution_trace` 对执行四阶段从原 typed 完整来源+原 financial context重算，PREPARE必须CONFIRMATION_REQUIRED、后续READY、原ASK级别保留。专门USER同意EVALUATION与执行相区别，核原源/role/clock/epoch/hash/标识，不充当新权限。历史服务和原审计白名单+纯验证已经接入，仅新算法生效。当前评估只在当前原RRRO读取，旧FullRecovery source/旧协议原件不重写。

新10风险直接PASS13.18s；包含在142旧执行/审计相关PASS29.01s（17 integration deselected），不相加。11源strict与3新源strict、12源Ruff通过。最初格式问题修正，旧金融失败及原归档碰撞缺失记录仍保留。14 Root源精确FINAL `.runtime/root-recovery-and-global-v1-shared-final-20261005T2349Z/manifest.json`；其中Main/deps新router注册还待实际Schema窄检查，不冒称全源验证。

实际 `/api/v1/full-recovery-actions` 五路由已接Main，preview/by-key精准RRRO，严格新身份请求与每请求实际本地USER；9独立合成API风险通过。新实际银行链尚NOT_RUN，前端正在独立实现，上一段费用/期限/MATURE/多步/局限全部保留。FULL604仍PENDING。


## 2026-10-06 09:15 Root 实际终态

2026-10-06 09:15 北京时间：唯一Root金融63872已真实终态PASS1/1104.83s，wrapper1108.651942s；W4/actual-signed-user-t0-full-recovery-original-bank-key-20261006T005301Z-71383f32，相关scope稳定true/global仅新独立203/706/history变化false。实际签名USER原T0整仓零费用零损失、ASK确认、银行受理丢响应、撤销后同原key恢复及重复全物理零写通过；不证明T1/现实墙钟即时/到期/部分/有损/组合。当前无Root金融RUNNING，共享HOLD解除供Root必要集成；旧失败不改，正式21/92及FULL PENDING不变。下一接日期历史显式新算法，再原105 failed-node与实际新204/102节点串行。
