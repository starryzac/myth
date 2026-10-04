# MVP-304 审计哈希链实施前审查

状态：PREFLIGHT_ONLY（2026-10-04）。303源码/测试已冻结，完整check运行中、未完成。本文只读预审，不代表304实现或验收；303完整验收并提交前不得修改304源码/测试。

初版MVP-304要求只追加审计事件及验证脚本，篡改一条历史记录后验证失败。初版8.6与完整计划13.1要求可定位原run、来源/策略版本、约束/候选、自主等级、动作、请求/回执和对账。304复用既有AuditEvent表与303冻结轨迹，不另造资金账本；FULL-803更强审计防篡改和证据导出后续实现。

## 当前可复用与缺口

- AuditEvent模型已有同用户sequence/idempotency/event_hash唯一、previous_hash、payload/version、causation/correlation、run/action/receipt关联，但尚无正常服务追加与完整验证器。
- scripts/tasks.py已有audit-verify入口，scripts/verify_audit_chain.py尚不存在；不能把预留命令当通过。
- 303记录准备/确认/预留/银行受理等真实阶段，input/trace hash只能检测不一致，不能抵御同时更换内容及hash。audit_chain状态仍NOT_IMPLEMENTED。
- demo_seed._clear_demo当前直接修改AuditEvent.causation_id并删除该模拟用户事件。加入只追加保护前必须冻结演示重置的显式机制，不能悄悄移除保护或让普通调用方绕过。
- 恢复和执行保留独立银行事务；审计不能为记录失败改变原经济提交/回滚或UNKNOWN资源占用。

## 待冻结语义

规范化event hash应绑定用户、sequence、previous hash、事件类型/version、关联IDs、原payload和经济/观察时间。单用户串行追加并验证现有head；同键重试返回原事件，payload改变拒绝。验证必须覆盖排序、缺号、重复、断链、内容/hash、关联租户以及可追溯的原303 trace hash，而不只比较相邻hash字符串。未知event协议明确不支持，不用最新规则补算原金融决策。

事件时点包括真实策略确认/生命周期变化、决策录制、动作确认/提交、银行受理/结算、投影/回执及对账。只读GET不追加；正常重试不得制造重复经济事件。已有历史run不回填伪造当时日志；如导入旧记录应标明导入观察时点及LEGACY来源。

需明确应用写入防护与数据库角色/管理员边界，不能把一般内容hash称为签名或外部锚定。演示reset仅对保留身份的合成用户，需保留可解释的新审计epoch或其他已冻结可审计重置机制；外用户数据与原始质量证据不清理。

## 待实现验证

真实PG追加/幂等/并发、正常链验证、单条内容修改/重hash/删尾/中间删除/排序或关联篡改失败、跨用户拒绝、304策略和动作真实链、UNKNOWN/投影失败独立提交、只读查询零写、reset三次与其他用户保留、schema/协议未知、verify脚本正确退出码。每组真实红绿与完整check、源码集合/hash归档后才能标COMPLETE。
