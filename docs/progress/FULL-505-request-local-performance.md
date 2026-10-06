# FULL-505 必要同请求读取优化

2026-10-06 03:34更新：旧session98461已退出0。W5/actual-audit-scope-original-event-repaired-and-full-readers-20261005T180751Z-fbbaffb2实际3PASS3728.50s，wrapper3734.677602s；范围源稳定，全源仅独立模块变化。包含实际原审计重复去重/跨Session与篡改拒绝、原FULL607完整实际恢复、Question重启/关闭。原FAILED记录保留，下方RUNNING为历史时点；没有测得相对提速比例，也不替代W0三固定场景或最终验收。

状态：生产接缝已实现，真实问答链待本批结果。功能优先修订二继续；本包优化当前产品读取，不扩展验证工具、不使用跨请求授权缓存。

Root在`services/decision_trace.py`将clean RR READ ONLY父链遍历改成id/parent两列读取；owner筛选、缺失、循环、32层、动作所属祖先与完整原trace/hash校验保留。写事务及不干净Session继续使用原ORM行，保留未flush篡改负例语义。尚未加入跨调用parent图缓存。

`services/audit_chain.py`新增显式`audit_read_scope`，只在当前clean PostgreSQL RR/Serializable READ ONLY调用内复用成功VALID原审计；绑定同Session、事务、nested事务、user、请求epoch、mode与完整checkpoint。第一次仍运行完整原verifier；返回深复制；所有已加载相关ORM完整列（含nested JSON及旧bank codec未投影列）摘要变化、额外加载未捕获相关行、dirty/new/delete或绑定改变均重新核验。调用结束释放强引用和context，不写Session.info、不存授权或财务结果。未证明/失败/legacy不缓存，每个原run的typed锚点仍独立检查。

Question GET包原`_all_records/_fresh`，两个POST只读reader各自包自己的事务，绝不跨事务复用。来源真实性、资金、策略、候选世界、完整分母及权限仍即时重算；原输入/历史hash不改。

四源strict PASS6f31feed；生产三源早期strict85b2bc65及staticc188496c，最终注册五源staticb5d49c0f。94直接Question/原审计domain测试PASS38.29s（W5/question-request-scope-direct-negatives-20261005T175912Z-6c8d8c8d）。新实际负例首批b19b1eea FAILED1/10.09s：测试误将创世事件未引用的Evidence变化等同于已入链原件篡改，原verifier合理仍VALID；其实际复用失效应以完整核验调用计数证明。原失败与四源保存在`.runtime/question-audit-scope-first-pg-failed-20261005T1805Z`。窄修新增真正入链AuditEvent的nested JSON拒绝，同时检查跨Session、savepoint、checkpoint、mode、rollback、返回副本、未审计外用户及全物理零写，不删除旧负例。

当前唯一Root实际PG98461：W5/actual-audit-scope-original-event-repaired-and-full-readers-20261005T180751Z-fbbaffb2，依次新审计范围负例、实际FULL607对账、原Question完整关闭/原键流程。尚无最终结果，不能写成功或性能提速比例。之前17027的完整四节点批次保持3PASS1FAIL1918.29s；Question在原正确SUCCEEDED执行后错误要求EXECUTED，后半close未运行，期望已窄修且原件保留。新增性能范围不替代W0三个固定场景原实测或最终性能验收。
