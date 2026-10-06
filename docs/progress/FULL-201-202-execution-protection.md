# FULL-201/202 完整保护的原动作提交前拒绝

真实接线增量已取得：W3/actual-full-floor-bank-boundary-and-retained-intervention-schema-20261005T174905Z-1f66a41d 三节点全PASS114.19s（wrapper117.129229s/scoped稳定，global仅独立源变化）。FULL节点真实经公开路由确认Goal/工资到账/原动作确认，在原应用预留提交后通过真实FULL确认路由新增支出，独立银行接收前409拒绝，无新BankOperation、原effect hash不变；新prepare拒绝所有实际物理表零写。原UNKNOWN丢回执恢复另一个原测试节点PASS，保留一个银行效果与原receipt重放。新增0012保全节点另见FULL-507-intervention-schema.md。仍无专用typed FULL拒绝证明、完整未来逐账户付款及全部新模板资金执行，不将局部PASS升级原编号关闭。

2026-10-06 01:50 北京时间增量：已接原 `prepare_action`、`confirm_action`、未存在独立银行 operation 的新执行，以及 `execution_bank.process_operation` 新 operation 接收前。独立银行使用当前真实上下文与已确认原效果重新验证，排除自己的现金预留；已有 operation 的 UNKNOWN 核对保持原恢复路径。原 effect/hash、确认权限、银行协议与历史未改。新实际候选覆盖应用预留提交后经实际 FULL 确认路由改变支出，再于独立银行接收前拒绝；同时检查新 prepare 拒绝零写。五源 strict 修后 PASS f1283a06，Ruff PASS6e062c42，收集 PASSa523ca9e；首类型失败 cf4abc47 和当时测试副本保留。实际 PG 正在 W3/actual-full-floor-bank-boundary-and-retained-intervention-schema-20261005T174905Z-1f66a41d 运行，尚无结果。专用 typed FULL 拒绝证明未持久化，周期未来逐账户借记仍 UNKNOWN，不能将此能力登记为全金融执行或原编号关闭。

以下保留较早模块时点。

功能状态：模块可运行，共享执行入口尚未接；原FULL编号仍PENDING。用户功能优先修订二有效。

新增domain/service/test `full_execution_protection.py`。原确定性重验已经得到动作后的真实快照和持仓，本检查据当前已确认FULL支出来源重算365日三阶段完整保护，保留原Goal归属、硬义务、其他在途现金预留及原效果hash。只追加BLOCKED/UNKNOWN拒绝，不授予权限、不改原协议或原计算结果。现金不增减的内部转账仍必须原确切确认；Goal贡献侵占FULL支出会被拒绝。周期来源账户未来扣款未完整证明时UNKNOWN，缺证不降成零。

服务在原写调用者用户锁下打开独立RR READ ONLY事务，每请求重新读取真实FULL来源；没有跨请求授权缓存。无FULL表或保护策略时保留原流程。必须在原prepare/confirm/未被银行接受的新execute中集成；已有银行operation的UNKNOWN只恢复原操作，不用新保护检查阻挡原真值核对。

首纯批7失败49通过5.32s，原因是新测试直接赋值冻结模型及漏算已有Goal现金200，原日志与三源在`.runtime/full-execution-veto-first-failed-20261005T1714Z`保留。精确只读输入副本和手算1,200−500−1,100=−400修正后12直接纯测试PASS1.73s：W3/full-execution-protection-pure-repaired-20261005T171437Z-12021a57。原执行domain44项在首批已PASS，未改行为不重复。三源strict PASS b00b7fd8、Ruff PASS9e637359。均不是实际银行/浏览器或全量验收；首次混合批仍FAILED。

未覆盖：共享生产调用/真实PG仍待；FULL新模板准备、真实支出结清绑定、周期未来逐账户借记、多目标联合资金执行、银行侧FULL保护重验和专用typed审计没有完成。本包不将原planning-only来源变成外付/投配授权，未接真实资金。

下一前置：当前四节点PG退出解除源HOLD后，Root窄接原执行三入口，覆盖已有原bank operation恢复绕过新授权判定及保护变更后的旧原动作拒绝；实际失败保原件，只重跑失败和直接资金风险。
