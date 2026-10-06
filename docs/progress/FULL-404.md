# FULL-404 定存梯度

状态：真实目标引用及当前原资金支持的定存批次规划已实现，直接纯检查通过；真实梯度开立/PG/全量验收未完成，项未关闭。原依据：`docs/spec/requirements-traceability.md:79`、原完整版 F:1284–1286 及 10.4 的分批到期、每批限额、近期用款不长期锁定和到期再判断要求。

同批 source/检查记录与失败原件见 `FULL-402.md`，组合数学范围见 `FULL-403.md`。没有新增共享 ORM、改变原金融执行、重写旧产品版本或正式历史。

## 可运行批次能力

实际 FULL 资产声明须 scope=goal 并引用当前用户真实目标。服务核原 Goal 投影/当前 base PolicyVersion/原 `is_version_authorized`，读取该目标实际 cash ownership 及完整原预留，再将原 goal_deadline 作为不能放宽的截止点。新 FULL scope 引用仍是规划确认，没有冒称原 Goal.asset_policy_id 的 V1 银行授权链接或跨目标 grant。

`GET /api/v1/full-policies/{policy_id}/asset-allocation?planning_mode=FIXED_LADDER` 只保留完整固定到期原产品；每批 today purchase_at、product_id/code/version/terms_digest、amount、cash_uses、原 principal_available_at、自然到期计划和原整数收益都有明确返回。原 batch 额度、完整组合额度/复杂度/turnover、起投、锁定、365 日保护和原目标归属同时适用，每批本金必须在 min(原 deadline、用户更早用款日期、规划窗口) 的**日初**前到账。使用日只有日期而无实际时刻时采用保守日初，不反推可更晚使用。

只生成当前已有实际现金支持的今日批次。未来工资为 0；未来月购入不伪装已到账，旧本金和有损提前支取不充当恢复现金。原有损提前支取仍需人工确认，当前批次只走自然到期计划。到期后是否再配置留给当时原策略/新原事实重新判断，未自动滚存。

有两个以上真实不同可用日的批次才返回 `MATCHED_MULTI_MATURITY`；实际仅一类期限则明确 `SINGLE_MATURITY_AVAILABLE`；没有合法固定批次则 `NO_VALID_FIXED_BATCHES`。不能用多条相同到期日或虚构七/九十天产品来称梯度已经实际齐备。

## 已验证和未覆盖

纯字面条款例证明七/三十/九十天三批分别在目标前到账、单批 <=100000 分、原联合保护 READY，五天用款则全部拒绝；单真实期限例保留 single maturity 标签，没有填假批次。它们是纯功能例，不是目录产品或金融实测。原第一次梯度搜索达到容量 UNKNOWN 的失败原件保留，精确界限修订后的 18 项整批通过见 FULL-402。

实际 PG 候选将先按真实原目标动作准备/原后果确认（只有实际 ASK 时）/执行，取得真实归属后，再读取已经 POST 登记的服务器定存目录、确认 FULL 资产规划、核全物理表零写和近期开支拒绝。当前服务器目录真实仅三十天期限，候选预期只证明 SINGLE_MATURITY_AVAILABLE，不能证明真实多期限梯度。

尚缺七/九十天实际目录版本、新批次实际开立执行消费者、真实多期到期/再次配置以及前端完整交互和最终全量。新声明不授予银行权限，原 V1 ActionEffect 的当前支持范围不因规划结果而扩大。FULL-404 未关闭。


## 2026-10-05 实际只读集成补证（原 NOT_RUN 记录保留）

主协调器完成并读取原 `docs/progress/evidence/W4/actual-full-assets-catalogue-and-goal-ladder-real-pg-20261005T142956Z-eb772045/manifest.json` 与日志：`test_full_asset_allocation_api.py` **2 PASS / 93.73 秒**，wrapper 96.72704 秒、exit=0、scoped_source_stable=true、all_source_stable=true、source_changes=[]。实际分母为两个隔离 PG 风险节点：完整目录登记/实际 payroll/当前组合及收紧/全物理表零写/目录漂移 UNKNOWN；真实目标动作产生归属后读取定存批次、短截止拒绝及全物理表零写。新路由 RRRO 已接线并生成合同。

实际服务器只有三十天单期限，结果仅 SINGLE_MATURITY_AVAILABLE；七天/九十天/LOW_RISK_TERM 未提供真实目录源，仍列为 unavailable。不是动态执行或真实多期限梯度证明，仍缺执行消费者、完整前端/真实全产品范围及最终完整版全量。原编号保持未关闭；前文候选 NOT_RUN 属交付时状态，不改其原件。

