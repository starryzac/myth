# ADR 0014：房租演示原声明与真实到期条件

状态：TARGETED_PG_AND_NATIVE_RENT_VERIFIED；完整七项/三轮仍待验。此项是 W1 预定义合成输入的显式修订，不改原25/67需求编号或到期/收款人安全门。

真实 `w1-20261005T044130Z-4578cb59` 前四项业务通过，第五项 prepare_rent_old_action 因原模板 demo-landlord 没有银行收款身份拒绝，整体FAILED/owned库VERIFIED_ABSENT。原日志/请求/录像保留。服务RED `rent-original-payee-service-red-20261005T050523Z-9a495722` 重现同原因；两份名称包含green的候选 `051025Z-a4f88756`、`051244Z-1b853e15` 实际仍FAILED，分别保留OCCURRENCE_NOT_DUE，不能按目录名称报成功。

新未准备房租声明使用原种子银行交易已有 synthetic-landlord-001。到期日来自本次服务受信 now 在原 User.timezone 的本地日期day；完整配置先展示，再走原明确确认。固定15日在当前5日尚未到期；固定1日在新确认5日之前，也受原权限起始门拒绝。未改时钟、confirmed_at/valid_from、付款历史或安全门，未伪造已付款/成功。

已准备的模板读取原持久 proposal.proposed_configuration，与原statement inputs/hash相等校验。当前模板配置只用于新声明；旧demo-landlord/due15和中间synthetic-landlord/due15继续原字节、原哈希、原确认/策略版本/审计读回。既有v1身份与source_ref保持；登记模板族仍约束金额150000、非自动、原优先级等字段，额外变化拒绝。旧未知房东声明仍不能凭新模板补成可执行银行身份。

实际定向PG `rent-current-day-original-history-risks-20261005T051834Z-2b79c8b7` **5 PASS/34.04s**：当前声明准备真实旧动作、修改后同动作INVALIDATED且无付款/回执；两类旧声明跨日GET/prepare全23业务表不变，未提交的ORM金额篡改被拒绝；原已知/未知/收入/内部/外来收款人两风险节点。原到期、已付历史和精确授权门保持不变。static及4源mypy PASS。不是全量或24×5实验。

同步审计生命周期修订：完整七项验收在最后原快照后，由父进程同步运行 w1_audit_observe，再退出owned临时库。原外部观察 `audit-current-owned-20261005T0457Z` 捕获113098303原bytes及三epoch后，因第五RPC失败导致owned库清理而AdminShutdown，实际FAILED，保留；它不证明全部epoch。新增rent单场景仅PARTIAL_SCOPE_PASSED，不能关闭七项验收。7纯结果范围门PASS，仅TOOL_ONLY。

追加实际原件：`w1-20261005T052041Z-7f15918d` 原RPC准备通过，但 UI PATCH 多传 policy_id 被严格合同422拒绝，FAILED；精确六字段请求修订保留 URI 身份与原哈希/幂等键，实际组件RED/GREEN11及type/lint。`w1-20261005T052939Z-623b0ac5` 房租真实Edge单项与原数据库存活时全epoch只读审计 PASS，源前后相同，owned临时库清理核实。此项为PARTIAL_SCOPE_PASSED，不当七项成功。

未覆盖：完整七场景/三轮最终源验收；午夜跨日新声明若确认日晚于原到期日，原安全门继续保守拒绝，不能自动更新已声明配置；老未知收款人需要新明确声明，不能改旧原件。
