# ADR 0012：只追加事件、受保护链头与合成演示轮次

状态：ACCEPTED / IMPLEMENTED / TARGETED_VERIFIED（2026-10-04）。前置MVP-303已完整验收并提交70ef877，总进度17/92。开发验证频率按用户指令覆盖：全量留初版/完整版两个节点，历史RED保留；304定向与正式只读保全证据见MVP-304.md。范围为MVP-304；认证、外部存证和FULL-803强化仍按后续任务实现。

## 冻结选择

采用[域合同](../progress/MVP-304-domain-design.md)、[持久化/reset设计](../progress/MVP-304-storage-design.md)和[真实挂钩设计](../progress/MVP-304-hook-design.md)的最小方案。永久保留AuditEvent、原规范UTF-8文本、reserved User及不可变对象副本；新增audit_epochs与audit_subject_snapshots。事件序号、key/hash/fact唯一性按同用户同epoch，旧epoch封存后不可重开。保留同epoch较早causation，三个到可重置业务实体的审计FK由数据库强制的同epoch原件引用替代，业务之间的同用户FK不动。

只读查询/核验不创建epoch、补事件、重授权、刷新策略或结算。首次真实写入在原事务建立EPOCH_STARTED，以真实观察标明现存未审计历史，不能伪造过去事件。activation的legacy_history=true使整体LEGACY_UNAUDITED，即便当前链本身VALID；不能将这个旧缺口改写为全史VALID。只在当前chain VALID、reference VALID或明确LEGACY_UNAUDITED且无完整性错误时，允许维护性重试/追加或真实封存；原金融权限规则不变，CLI仍非零退出并展示未完整审计分类。无事件/legacy/未知协议/核验预算不足必须明确分类；只有完整已知链和独立head匹配才可返回VALID。可信checkpoint可作PREFIX/EXACT验证，普通hash不是签名。

event BEFORE核原规范协议/hash、head/seq/prev、归属/永久副本/因果；AFTER是正常head推进的唯一通道。历史event、snapshot和epoch的普通UPDATE/DELETE/TRUNCATE拒绝，不提供可自行设置的GUC或普通删除例外。head推进同时核实际新行和允许差分，不能单靠trigger depth。管理员ALTER/DROP/禁用保护能力明确在此保障外；普通非owner和owner普通DML边界分别真实PG验证，不宣称生产API已换非owner凭据。

## 真实事实与事务

必要事件为DECISION_RECORDED、ACTION_CREATED、ACTION_STATE_CHANGED、BANK_ACCEPTED、BANK_SETTLED、ACTION_PROJECTED、RECOVERY_OBSERVED、POLICY_VERSION_CONFIRMED、POLICY_STATE_CHANGED、GOAL_INITIALIZED，加EPOCH_STARTED/EPOCH_SEALED。原303 trace/hash与版本是决策锚；银行实际完整posting set是结算锚；一次回执投影不是第二次银行资金流。205与301同operation只记同受理/结算一次。全部append使用调用方Session、无内部commit；银行事件保留独立银行事务，应用UNKNOWN/等待/失败观察不冒充到账或可靠拒绝。

幂等同时受key与fact身份保护；同原语义返回原事件，金额/关系/原事实时间变更拒绝。重试新observed/appended时钟不改原记录。真正新重验按run身份记录；状态no-op不写事件。PREPARE后收入证据会合法补入请求，EXECUTION_REQUEST锚从最终ACTION_CREATED/银行受理绑定开始；不能把准备中间副本误当最终请求或以此隐藏真正篡改。active epoch中已捕获、被事实引用的原不可变对象必须仍存且内容匹配，已知原件删除使INTEGRITY_ERROR；决策初始就缺失且显式missing_evidence_ids的来源没有被伪造为已知原件。正常生命周期状态变化按原不可变字段合同核验。SEALED epoch只解析旧epoch原件，不查复用UUID的新对象；纯离线域核验的历史副本协议与storage的当前对象存在性检查分开。

HTTP审计GET/POSTverify与CLI真实使用RR/READ ONLY；分页最多100，fullverify先在SQL汇总event/snapshot数量和原文本字节，超预算返回INCOMPLETE而不装载/解析全部原件。CLI最多1000轮元数据，检查点最多65536 UTF-8字节且有界读取；输出使用原规范文本、独占创建，拒绝覆盖原可信检查点。明确展示核验ALL_RETAINED_EPOCHS或SINGLE_EPOCH范围，不能将子集当全部历史。

## 重置与并发

仅预留合成用户可reset。原单事务先按显式19业务表allowlist归档完整真实旧图及snapshot索引/count，追加EPOCH_SEALED封口，再重建业务图、新epoch和实际seed genesis；任何失败完整回滚。事件与User不删除，不递归复制审计表。金融事实/产品seed版本保持mvp-301-v6，seed-summary-v2只将确定性业务图与真实audit元数据分列，成功reset业务相等且audit增长，失败全库相等。

执行/恢复的整个三段逻辑命令持shared reset gate，reset持对应exclusive gate；锁顺序先gate后User/epoch，防止reset插入银行独立提交与应用投影之间。该gate不合并原事务、不释放UNKNOWN声明、不重写原合同，专用连接不可耗尽业务连接池。单事务其它写入遵循相同gate顺序。

## 共享实现接口与归属

域：domain/audit_chain{,_types}.py，严格AuditIntent/Envelope/Subject/Head/Seal/Checkpoint/Verification，规范化/build/verify/same-intent，事件registry与bounded原件协议。

存储：services/audit_chain.py、models、0006迁移、demo_seed及db/audit_guard.py。接口ensure_audit_epoch(session,user_id,appended_at)、capture_audit_subject(session,user_id,epoch_id,kind,entity_id)、append_audit_event(session,intent,observed_at,appended_at)、get_audit_head/list_audit_events/verify_audit_chain；最终参数以owner发布的typed签名为准，不在业务hook内部造独立事务。提供当前epoch/fact只读查找与规范snapshot/ref builder。

root：services/audit_recording.py公共事实录制包装、HTTP audit接口/CLI验证、303审计状态读取、文档/生成合同/最终统一验收。integration owner：原decision/action/bank/projection/policy/goal/recovery文件的精确hook，必须与root包装协调签名，附真实PG事务/幂等/UNKNOWN/T1/reset并发审计测试。每位owner只写分配文件，运行测试前保留真实RED原因，环境/夹具错误另记。

## 验收门

真实PG正常链、并发序号、同键/换键事实、跨用户引用、历史改删/清表、单条重hash、删尾/清空与checkpoint、未知/legacy、原trace/request/receipt锚、独立银行失败/UNKNOWN/T1、GET零写、三次reset+其它用户保留+失败全库回滚+跨事务gate。统一audit-verify正确退出与readonly证明，正式非重置迁移前后原数据核对，源路径集合/hash及实际相关模块、必要集成证据归档后关闭304；版本完整check与完整coverage在初版/完整版两个验收节点执行（2026-10-04用户指令），历史失败不改成成功。
