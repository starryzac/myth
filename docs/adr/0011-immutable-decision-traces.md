# ADR 0011：冻结真实决策输入与历史解释

状态：ACCEPTED / IMPLEMENTED / VERIFIED（2026-10-04）。MVP-303完整check20261004T051331Z-0c360f9c的11命令exit0，1229后端/4前端/1真实Edge与185源码集合/hash核对通过；详见MVP-303验收记录。前置302提交82e5b8a。范围为MVP-303，跨事件审计链属于304。

## 记录与事务

复用DecisionRun及DecisionConstraint，迁移0005增加可空parent_run_id和subject_action_plan_id，两者均以同用户组合外键绑定；动作引用的延迟DDL解决既有动作到run的反向依赖。冻结轨迹保存在input_snapshot.decision_trace，原snapshot_hash覆盖整个输入；恢复原preview/idempotency_key继续保留。通知、动作状态和银行进度仍在各自原实体中，不改写当时判断。

301准备、明确确认、预留前重验、独立银行受理前fresh重验分别录制PREPARE/CONFIRM/RESERVE/BANK_ACCEPT，随原事务提交。每次从实际返回的ExecutionContext、ExecutionValidation和同次来源副本构建记录，规划调用同时保存原候选与FIFO计算。重验事件身份绑定阶段、时钟及真实输入/结果摘要；同一真实判断重试幂等，事实改变的重验有独立子run。已受理银行重试不新增受理判断。

205恢复保存实际preview的完整输入/结果与候选；自然到期另存CONTRACT_SETTLEMENT，明确为原合同结算，不补新权限。旧恢复银行保存它实际采用的本金、账户、仓位、权限和独立账本验证输入；该阶段不宣称重新运行301资金算法。实际与预计到账、银行经济时钟与应用观察/对账时钟分开。资金事务回滚时轨迹也回滚；不为保留失败日志提交资金变更，已独立提交的银行事实不受应用失败影响。

302原assess和所有GET保持只读。单独POST /decisions/assess是明确保存命令，可记录BLOCKED/ADVISE/ASK/AUTO判断，无动作、预留或持续权限；有限金额偏好只用原同库REPEATABLE READ/READ ONLY可信生成器。完整worlds保存在记录中，HTTP不接受银行事实或权限注入。已知权限否定后的财务建议保存原拒绝与financial_advice_context用途，不能形成执行授权。

## 严格冻结合同

decision-trace-v1使用严格类型与有界JSON（10MiB、递归深度32、节点数上限）。受信输入、候选、计算和结果中的金额仍为整数分；合法amount_options_cents保留2–8个正整数分。真实quantile等非金额参数允许有限float，NaN/Infinity拒绝。仅原证据content与策略configuration使用同样有界的原始JSON副本，能保存导致BLOCKED的布尔/浮点金额或错误user_id声明；typed行owner与受信图仍严格同用户。原始副本不产生资金事实或权限。input_hash绑定输入、来源/策略副本及算法；trace_hash绑定整个冻结envelope。与经济确认effect_hash、financial boundary_hash和候选稳定性签名分开。

证据保存当时身份、等级、来源、完整内容、原宣称hash、实际captured_content_hash、有效窗、观察时间、状态和supersedes。原声明hash与实际副本不一致时完整性标INVALID，仍保留真实副本供解释BLOCKED；不能补造有效来源。VERIFIED仅表示hash一致，金额类型或owner声明不合法仍由原source_issues与结果判定BLOCKED。策略同样保存原版本/配置/声明hash与capturehash。全量边界检查点、三态满足情况、负margin、未知null保留；DecisionConstraint是冻结约束的查询投影，读取核验行数、键和全部字段。

## 历史查询与解释

GET /decisions、/{run_id}、/{run_id}/explanation及/actions/{id}/decision按用户读取；动作/回执关联进行已有301或新增205只读完整性核验。解释只消费冻结字段和确定性模板，不调用LLM或最新资金算法。正常SUPERSEDED、撤权等状态变化独立附注，原内容/配置或约束投影变化409。旧run返回LEGACY_PARTIAL，未知schema/算法返回UNSUPPORTED_VERSION，不重算或回填当时不存在的证据。列表使用稳定(as_of,id)分页。

本项内容hash只能检测不一致，不能抵御同时更换内容与hash的管理员；audit_chain/audit_chain_status明确NOT_IMPLEMENTED，304另做跨事件只追加链。

## 验收要求

五类301动作、205恢复/自然到期均能从回执查原run与阶段；ASK保留人工来源。验证原规划候选、原计算、有限世界、坏源BLOCKED、后续合法状态变更、单项篡改、跨用户FK、真实READ ONLY、UNKNOWN重试、迁移往返与完整check。只有全量检查、源码路径集合/hash及文档同时通过才把303标为COMPLETE。
