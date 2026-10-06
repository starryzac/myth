# FULL-405 提前支取损失与时限规划

状态：新增生产 domain/service/GET 规划接口和直接纯风险检查已交付；两项真实 PG 集成候选未运行，完整执行及完整版验收未完成，编号未关闭。遵循功能优先显式修订；未修改原金融执行、报价发行、旧 hash、失败证据或正式模拟历史。

原依据：`docs/spec/requirements-traceability.md:80`，原完整计划 F:1288–1290，以及 12.3 的提前支取损失转人工、不能及时回款报风险要求。本批与 FULL-604 共用新增模块；没有扩大原 FULL 确认的银行权限。

## 可调用能力

纯函数 `app.domain.full_recovery_planning.plan_full_recovery(FullRecoveryPlanningInput)` 与服务 `app.services.full_recovery_planning.read_full_recovery_planning(Session, user_id, policy_id, now, planning_deadline_at=None)` 接入当前实际完整 RecoveryPolicy 声明、确切引用资产策略版本、原持仓本金和归属、不可变目录、原报价以及原 365 日保护曲线。新增 `GET /api/v1/full-policies/{policy_id}/recovery-planning`；只有 planning_deadline_at 可以提前真实首短缺或目标截止时限，拒绝金额、授权、目标、身份、产品或时钟覆盖输入。

服务要求同一 clean REPEATABLE READ / READ ONLY Session。先复用实际银行投影、收入账本（存在真实收入来源时）、完整 exposure 和原财务上下文验真，核当前完整审计；每个持仓查原 Bank 证明、原购买账户及真实当前 Goal 版本，完整原产品必须匹配已登记不可变 catalogue/source hash。旧目录版本即使当前不再销售，也按其购买时的原有效窗口验证，不偷换为当前产品版本。

报价读取复用原只读 `execution_sources.load_execution_quote`，保留原 quote_id/user/position/product/version/terms_digest/principal/request_at/available_at/expires_at/evidence_ids；不发行、替换或重写报价。实际提前支取原件还核 simulated-fixed-early-price-v1 / CEIL_CENT，费用只支持原显式零费合同。独立用严格整数公式 `ceil(principal_cents × 原 loss_bps / 10000)` 重算损失；不调用原银行损失公式，不从收益名称或测试期望反推数值。Quote 过期、绑定不同、条款/损失不一致或不存在时，金额保持 null / UNKNOWN。

原报价在 `request_at <= now < expires_at` 内保持价格和身份。规划当前假设发起后的最早现金时刻取 `max(原报价回款时刻, now + 原赎回延迟)`，不把过去价格观察时刻当成已经提交或已经到账；这个保守时刻只用于条件规划，原报价不改。

每个候选保留精确 catalogue_version_id / product_record_hash / 原购买授权版本和引用的当前资产版本，检查完整声明单笔限额、费损上限、延迟，以及当前引用资产的类别、单笔、锁定、延迟、风险和提前支取许可。完整 RecoveryPolicy 的 max_fee/max_loss 当前严格为零；有损原报价明确 FULL_LOSS_CAP_EXCEEDED、within_full_planning_limits=false、default ASK_ONCE，不能被输出当自动权限或混进 lossless_steps。

## 真实现金和条件影响边界

响应分别返回 actual_boundary、lossless_conditional_boundary，以及单笔早退 conditional_impact_boundary。所有 planning_only=true / bank_authority=false / execution_support=NOT_IMPLEMENTED。原本金不变；无金融动作、银行请求、回执或实际到账由 GET 产生。

独立条件投影按原 net_cents 回款，立即回款时现金只加净额；目标 allocated/principal/cash 同时按费用与损失调整。延迟回款时使用净本金而非原 gross 本金作条件未来到款，避免把已损失部分计入未来安全现金。任何目标本金只回同一原目标账户，不能填一般现金缺口。有损影响只用于解释待人工选择方案，不能改变原 actual_boundary 或无损条件方案。

## 检查原件

- 首整批 30 PASS / 2 FAIL、2.52 秒：`docs/progress/evidence/W4/full-recovery-original-price-and-scope-direct-pure-20261005T144800Z-8ee5c4de`。失败是夹具把 expires_at 等于 request_at，先触发原报价顺序拒绝，以及空列表断言写错；原失败标签与日志保留。首次类型 Literal list 不匹配的 RED 为 `...types-20261005T144801Z-0b965a27`，修正本地类型，不放宽输入。
- 修正后新直接 32 PASS / 2.06 秒：`full-recovery-price-expiry-and-default-ask-repaired-pure-20261005T144903Z-79a8cb62`。
- 新 35 项 + 原 Recovery 67 项，实际同命令 **102 PASS / 2.58 秒**：`full-recovery-net-future-principal-direct-pure-20261005T145547Z-00f17558`；scope/global source stable 均 true、source_changes=[]。覆盖 365+初始日 1098 个阶段点、排序、原件不变、原报价有效窗口、损失整数上界、同目标/一般现金隔离、迟到部分恢复、过期/原购买版本/当前引用确认/来源缺失、延迟净额防 gross 回款。
- 最后仅补 current early permission 的明确拒绝理由，相关风险单节点 1 PASS / 0.80 秒：`full-recovery-current-early-permission-direct-risk-20261005T145814Z-c6ce11c9`，没有把分次运行改称全量。
- 六文件 strict mypy / Ruff / format PASS：`full-recovery-final-six-types-20261005T145814Z-aa708141`、`...static-20261005T145814Z-cea79c19`、`...format-20261005T145814Z-54e3f21c`。这些是新模块直接纯/静态检查，不是实际金融或完整版验收。

## 源及未覆盖

| 文件 | SHA256 |
| --- | --- |
| `apps/api/app/domain/full_recovery_planning.py` | `01f465ccc7c9fa70cab9045a10d608ecf21b98c6c114b5fc96a82e8f5fbfa25d` |
| `apps/api/app/services/full_recovery_planning.py` | `426ded2761424d0bdbc5553074611533864c46cfb2fff12583468cdcc0b6de90` |
| `apps/api/app/api/v1/full_recovery_planning.py` | `e79f4629e31427b130d189470e14561361c8f32eaa8f01f404b584a15254ade8` |
| `apps/api/app/tests/test_full_recovery_planning.py` | `7db360cd1455be97a16f7fb3c31812b8874c8443be95512d1fee90345d325b6b` |
| `apps/api/app/tests/test_full_recovery_planning_contract.py` | `9b1e41cfc96edb0ce5216b5bbbf960114f2bf183662f1824a69dc352a4101f68` |
| `apps/api/app/tests/test_full_recovery_planning_api.py` | `486acd1335385c93ef452752786504df735ae55f921ba66307ccd693bc1804f7` |

真实 PG 两风险候选 `test_full_recovery_planning_api.py` 由主协调器注册路由/RRRO 后运行：真实 Demo 声明确认和实际购买回执、真实 payroll/消费两银行腿、真实 catalogue POST/FULLRecovery 确认，分别核 T0 原 365 条件方案/全物理表零写/银行篡改 UNKNOWN，以及原早退报价/100 分独立损失/ASK/有效期拒绝且不重报价。当前 NOT_RUN，不能按文件存在判成功。

仅 whole-position 规划，未有分笔银行赎回 DTO；任意费用合同、真实多资产早退/部分执行/银行执行消费者、原件恢复回执、前端及完整版全量仍缺。现保护范围只 ORIGINAL_VERIFIED_365_DAY_CURVE，新 FULL dated expense / 季节储备等尚未统一纳入边界；真实 previous boundary 的 BOUNDARY_SHRINK 比较、撤销/过期触发的 service adapter 也未接，不能以一般流动性短缺代替。原 V1 执行仍按原当前权限/报价/一次性确认独立重验，规划结果不能跳过该门。


## 2026-10-05 实际集成补证（原 NOT_RUN 与整组失败保留）

主协调器实际运行 `docs/progress/evidence/W4/actual-recovery-original-goal-key-and-historical-boundary-readonly-real-pg-20261005T150011Z-7e0c77f9/manifest.json` 的四节点合批：**整体 FAILED，1 FAIL / 3 PASS，118.09 秒**，wrapper 120.307731 秒。两项 `test_full_recovery_planning_api.py` 实际均 PASS；另一个历史边界节点 PASS，唯一 FAIL 为主协调器新增 FullGoal lookup 的业务时钟/审计 opened_at 比较，与这六源无关。不能将原整组改标 PASS，也不能把118.09秒拆成本模块两节点专属耗时。

实际两个节点证明真实 T0 模板确认/购买回执、原工资及消费两银行腿、目录登记和当前 FullRecovery 确认；365原真实缺口与无损条件曲线分开、全物理表零写/重复读取/非法字段与放宽截止拒绝、银行余额篡改后 UNKNOWN。实际定存节点证明原issued quote原件、100分独立损失/49900分净額/ASK不进入无损步骤；当前实际原账本未改变，报价过期后 UNKNOWN、不重新发行。

该合批 scoped_source_stable=true；all_source_stable=false，仅 `apps/api/app/domain/finite_uncertainty.py`、`apps/web/src/features/onboarding-draft.ts`、`apps/web/src/pages/OnboardingPage.tsx` 并行新增/变化，原 scoped_source_changes=[]。主协调器已完成本接口注册及提前 RRRO，六源hold已解除。真实T1/成熟定存结算/多产品损失/新消费者/全量仍未实测或未接，原编号不关闭。

