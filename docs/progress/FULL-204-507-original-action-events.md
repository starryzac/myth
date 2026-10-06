# FULL-204/507 原动作变化事件增量

功能实施：PARTIAL_IMPLEMENTATION。原 FULL-204、FULL-507 关闭状态仍 PENDING。

`POST /api/v1/boundary-events/observe` 接受前/后原决策编号与当前 epoch；读取完整原执行轨迹、原证据与当前审计，重算原财务核验后比较经济效果和自治级别。金额、账户归属、实际到达时点、费用、损失及风险变化影响签名；数值余量、生成编号和等价时间表示变化不产生 BoundaryCrossed。证据不足拒绝，不当作空动作集合。

以 user/epoch/前后 run 固定身份写入新的不可变 DecisionTrace 和既有 DECISION_RECORDED 审计事件；原决策、原哈希和原银行经济事实不变。同一原请求重复读取该原记录，零新增金融或审计行。响应显式声明 `ORIGINAL_SINGLE_ACTION_COMPARISON`、全局动作集合不完整、问询投递未实现、无银行权限。

实现文件：domain/services/api.v1 的 `boundary_action_events.py`、两个直接测试。只向现有服务及领域审计读取器追加新算法的可识别名称；不修改旧 canonical 协议。

检查原件均在 `docs/progress/evidence/W3`：

- `action-change-semantic-domain-modules-20261005T152552Z-7ec131b7`：首4纯测试 PASS。
- `actual-boundary-action-events-and-finite-worlds-real-pg-20261005T152630Z-7693a74d`：原整批 FAILED，1 FAIL/1 PASS，206.14s；事件首次记录成功、原键重读被审计算法未知拒绝；finite 独立节点 PASS。范围稳定、全源因独立工作变化不稳定。原日志不改。
- `.runtime/FULL-204-events/first-pg-failed-20261005T1536Z` 保存原失败源码。原首次 as_of 类型错误、后新增单测 Optional datetime 类型失败以及长行静态失败保留各原输出/manifest。
- `boundary-event-existing-semantic-audit-modules-20261005T153738Z-51bf835e`：5个本模块测试加2个既有未知协议/未知轨迹风险节点，共7 PASS/0.97s；复用原 `_economic_payload`，原未知版本负例未删除。
- `boundary-event-existing-semantic-audit-types-20261005T153738Z-a3c622e8`：9源严格类型 PASS；最终单测类型 `boundary-event-final-test-types-20261005T153814Z-6681d09c` PASS。
- `boundary-event-final-static-20261005T153812Z-9f6c6328`：Ruff PASS。
- `actual-boundary-event-audit-replay-and-full-protection-real-pg-20261005T153844Z-d006f762`：两实际 PG 节点 PASS/209.43s，wrapper 211.987494s。其中一项为本事件，另一项为 FullAnnual；不编造单项耗时。范围稳定，全源因独立建议、GoalUI、contracts变化不稳定。实际证明记录、原键零写重读、原轨迹不改、完整审计、同动作静默、stale epoch拒绝；金融原表不变。

未覆盖：五集合全体动作的自动比较与触发、专用 typed BoundaryCrossed 审计、跨不同但等价 run 的去重、用户问询投递与节流、进程重启后的不重复弹窗。当前去重只覆盖同 epoch 的同一前后 run 对；不能宣称 FULL-204/507 全部完成。下一前置为持久一次一问工作流及全局候选/事件联接。


## 2026-10-06 08:08 原提交后 GLOBAL 生产

2026-10-06 08:08 北京时间：完整版功能优先继续。604 USER 恢复执行已接原 prepare/confirm/reserve/首次银行接收及历史验真，强制 ASK 和专用原用户确认；14 shared FULL-path FINAL 2ebc76f1，142 相关风险 PASS（17 integration deselected），不把纯测试当银行证据。604 API、307 FULL 动作集合新版本已注册；原 v1 及旧单动作协议/哈希不改。GLOBAL 通知 V1/FULL 两个精确源版本已 FINAL c6bad7be，0014 只扩来源 CHECK 且禁止带新行回滚；Root POST 提交后生产已实现，首次/数值静默/UNKNOWN/原键重放均不生新通知，失败只诊断、不替换观察。Root 10-source FINAL 8823a9c6，9 strict/static 和10直接风险通过；actual HTTP candidate只类型已过，银行/PG实证尚待。

实际合同生成 child0、wrapper SOURCE_CHANGED 原件 e358f324保留；独立 --check fd15f23e PASSED。当前唯一真实PG 80546 / W3/actual-global-migration-finite-full-and-http-postcommit-20261006T000656Z-4ce32085 RUNNING，按0014→原v1集合→FULL动态集合→Root HTTP通知串行；尚无最终结果。Main/deps/已注册API/原金融、模型/迁移、audit/通知和204共享源 HOLD，独立604 Web、204资产新producer和新102图谱可并行。后续307/105/604/605/Question实际节点仍NOT_RUN，不因collection或文件存在升级。正式21/92、FULL原项PENDING；真人0/NOT_STARTED、新性能NOT_MEASURED，旧失败及正式模拟历史全部保留。

