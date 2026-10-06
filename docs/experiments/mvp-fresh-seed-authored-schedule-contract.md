# W1 首次隔离初态与作者调度显式修订

状态：SCOPED_IMPLEMENTATION_VERIFIED。只交付这些可运行能力和相关风险，不关闭MVP-501—504/FULL；实际24冻结及120五臂仍NOT_RUN，真人NOT_STARTED。

私有seed扩展 `MVP503_SECOND_CASH_INITIAL_FACT_V1` 仅可信Python driver可传，在创建Session之前要求127.0.0.1:54329、生成bf_test32hex、无reset/replay/旧epoch参数；原DEMO_GATE_KEY独占锁内再核实际current_database、全User空、无当前epoch/业务历史。它在首次bank opening及audit genesis之前增加同用户CNY/CASH/0余额账户，external_ref=mvp503-author-v2:account:cash-second、UUID由原确定性seed生成。完整历史覆盖/银行余额原证据、独立CASH opening0、规范UUID排序的收入scope、执行anchors及genesis都包含此初态。来源Evidence明确合成扩展且creates_authority=false。默认seed路径/5账户/v6/SeedSummary-v3/公开HTTP均不变；默认当前实际银行记账17，扩展18，含已存在income anchors，不误用旧7/8。原source完整8261239b副本在交付档before，没有回写旧历史或hash。

实际验证：10个before-connect拒绝pure；类型/静态通过。`expected-transfer-negative-original-real-pg-20261005T100953Z-1907f4ae` 1真实PG PASS4.95秒：原null目的地422、后续只读继续、全24物理表零写。`second-cash-bank-income-distinction-real-pg-20261005T102232Z-e352582c` 1真实PG PASS44.56秒：首次0初态、真实83047分payroll入账、47143分原ASK_ONCE精确确认与真实SETTLED两腿±47143/净0、原收入身份保全、审计VALID、原键重放全24表零写。该小转账实际优先用旧资本，income_uses为空且目标收入归属0；不称新来源迁移。既有默认seed/epoch拒绝另实际PASS在首修计数run原1FAIL1PASS中，原wrapper仍FAILED。它未取得独立PASSED wrapper，不能把整组失败重标成功。

保留原失败链：首2FAIL旧17/18计数误断言；随后1FAIL1PASS是真正收入scope排序拒绝；规范scope后转账真实成功但新测试错假设47143新源归属；另实入账测试误把总收入可用额当唯一新83047来源。所有原source、日志和manifest留在before/scoped；仅首次生成时排序，旧金融funding算法与历史hash未改。最终实际PASS证明指定范围，不是24正式结果。

新 `scripts.mvp_authored_schedule` 显式 `EXPLICIT_AUTHOR_V2_TO_CASE_V2_WITH_BOUND_CONTROL_SCHEDULE_V1`：完整作者V2文件逐byte外部SHA保留为原件，在Case V2 data_origin及独立schedule中绑定；只转换原scenario_id为case_id、先可信first seed再将runtime mode标EXISTING，原native steps、全部条件节点、五臂原参数、原未测分母完整保留。whole Case byteSHA仍通过原binding方法注入typed fragment，非自哈希。16 TOOL_ONLY pure通过，含全24原作者转译及原NativeAdapter核、非法purpose/控制/forward依赖/初态/原字节拒绝；T02预期422和澄清actor仍pending，不能声称NLU或actor已执行。无纯结果当银行证明。

未覆盖：运行中的条件调度/真实actor原件、B3恢复逐动作确认相位、完整开发输入库存差量、完整seed原件登记、24正式corpus freeze和每次fresh重验、120实际独立库/完整观察/14指标、当前新source Compose离线/全量覆盖/E2E/材料。下一步先接可信conditional driver及独立观察，冻结最终source和原设计/INPUT/RULE/ORACLE后真实运行；初版四命令只在验收节点。交付manifest：.runtime/W1-second-cash-fresh-genesis-20261005T101149Z-4f495413/delivery-manifest.json。
