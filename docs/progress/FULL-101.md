# FULL-101 双时态事实：只读能力已交付，原验收 PENDING

2026-10-05 功能优先批次。依据原 F:1176–1178、270–296 与原追踪表；本文不关闭需求。

## 可运行功能

`GET /api/v1/evidence/facts?valid_at=...&known_at=...` 对实际当前模拟用户的 EvidenceItem 查询有效时与知悉时。保留原 valid_from/valid_to/observed_at/supersedes_id/content_hash；有效区间为前闭后开。较晚观察到的更正不能覆盖早先知悉状态，不同有效区间更正不能抹掉此前有效事实。未关联矛盾显示 CONFLICTED；来源缺失、哈希错、缺失状态历史、容量超限明确 UNKNOWN。未来 known_at、无时区日期拒绝。请求为 REPEATABLE READ、READ ONLY，不提供资金授权。

实现：`apps/api/app/services/evidence_graph.py`、`apps/api/app/api/v1/evidence.py`；注册/只读依赖为 `main.py`、`api/dependencies.py`。新增 `test_full_evidence.py`。

## 已取得证据

- 9 项直接纯检查通过：`evidence/W2/evidence-bitemporal-provenance-pure-20261005T121040Z-313f1728`。
- 5 文件严格类型通过：`evidence/W2/evidence-bitemporal-provenance-types-registered-models-20261005T121245Z-501fd281`。前两轮类型失败日志及原源保留在 `.runtime/W2-functional-priority-amendment-20261005T1157Z/`。
- 与年度规划合计 2 项真实 PostgreSQL 集成通过，pytest 11.66s/wrapper 13.967423s：`evidence/W2/full-facts-and-annual-readonly-real-pg-20261005T121349Z-37119972`。双时态查询真实保留 101137/109139 两份原事实；当前、历史、未来时间拒绝、缺来源查询实测。该次当时物理24表前后逐行完全相同。
- 上述集成 scoped_source_stable=true，all_source_stable=false，唯一无关变化为 `test_experiment_error_stack.py`；不能描述为全仓冻结。

## 具体未覆盖

已追加 `POST /api/v1/evidence/declarations`：EXPENSE/GOAL_PREFERENCE/NOTE 三类用户声明、原审计期和幂等键、服务器知悉时间、32 KiB内容限制、同用户同来源迟到更正；原记录和原status不变。内容摘要同时绑定来源、有效时与知悉时元数据。声明仅 USER_DECLARED，银行证明/执行授权/专用声明审计事件均为false；不能用此入口给资金来源升级权限。实现 `services/evidence_declarations.py` 和 `test_evidence_declarations.py`。

最新7纯PASS4.92s与3文件最终types、7文件Ruff通过。0008真实新schema声明/事实/年度合计3PG PASS13.37s/wrapper17.347996s，全部27物理表对比，global+scoped稳定：`evidence/W2/full-declarations-facts-annual-new-schema-real-pg-20261005T124531Z-1104c6e5`。声明仅增加两条Evidence；旧Evidence及其余26物理表逐行不变，幂等重放/错误确认与所有只读查询零写。原24表证据保留当时语义。

金融执行适配器尚未全部使用双时态选择；没有可证明的历史可变 status 记录时不重建历史 VALID。银行观察/确认导入协议、专用声明审计事件、历史缺失覆盖、真实跨用户隔离集成、最终集中验收待补。10,000条读取容量不是无限完整证明。

下一前置：确认受支持来源的追加协议与各金融适配器合同，保留原行和原哈希，再接功能。
