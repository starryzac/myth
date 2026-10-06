# FULL-102 证据引用图：只读能力已交付，原验收 PENDING

2026-10-05 功能优先批次。原 F:1180–1182 要求是证据链；成功解析引用不能替代银行与审计验真。

## 可运行功能

`GET /api/v1/evidence/graph/{kind}/{identity}` 支持 EVIDENCE、PROPOSAL、POLICY、POLICY_VERSION、DECISION、ACTION、RECEIPT。仅从实际白名单 ORM 原行读取同用户且知悉时间内节点，保存原列值；按实际 evidence_ids、policy_version_ids、supersedes、决策/行动/回执关联和策略版本产生有向边。缺失引用、循环、未知来源、512节点容量超限登记 issues/UNKNOWN；不存在或不可知根返回404。输出不授予执行权限。

实现与 FULL-101 共用 `services/evidence_graph.py`、`api/v1/evidence.py`，没有生成假银行证明或伪造经济因果边。

## 证据与范围

9 项直接纯检查包含实际回执字段→Action引用的函数检查；5文件类型通过。真实 PostgreSQL 合并2项集成中，本项实测 Evidence→SUPERSEDES 两节点一边、时间与缺来源拒绝，全部当时24表零写。证据目录：`evidence/W2/full-facts-and-annual-readonly-real-pg-20261005T121349Z-37119972`；scoped稳定/global因无关测试修改不稳定，原manifest未改。

## 具体未覆盖

尚未实测完整 Receipt→Action→Decision→Evidence 根路径；图内独立银行posting/审计anchor核验、账户/账单/交易/Goal图、全部反向引用、历史可变状态重建、跨用户实际集成及最终浏览器/集中验收待补。REFERENCES_RESOLVED 只表示所读引用找到，不是事实真实或金融成功。不存在证据时保留缺口。

下一前置：将现有实际验真API/只读历史协议接图中的原引用，并按真实返回覆盖更多节点。

## 2026-10-06 完整原件图 v2 功能增量（原验收仍 PENDING）

新增独立 `domain/full_evidence_graph.py`、`services/full_evidence_graph.py`、`api/v1/full_evidence_graph.py` 与三个直接测试文件；新GET `/api/v1/evidence/full-graph/{kind}/{identity}` / `persisted-full-evidence-graph-v2`。旧七类graph-v1、原共享canonical/模型/历史与正式模拟数据均未改。

当前35张实际业务表按owner/原知识时间登记全分母，包含账户、交易、账单、Goal、持仓、独立Bank操作/流水/外部事实、审计期/事件/副本、完整策略/版本/命令、实际FullGoal模型证据以及执行/介入队列、资产组合批次/consent。真实FK及明确typed JSON产生双向导航，未知引用/owner/hash/时间/源容量不足明确UNKNOWN；同请求一次完整审计复用原getTrace/FullReconciliation/FullPolicy/FullGoal/产品目录验真。原审计hash和银行codec没有修改，查到ID、规划确认与历史回执均不升级为执行权限。

可运行合同与全部限额见 `docs/spec/full-evidence-graph-v2.md`。过去可变内容无完整原副本时null/UNKNOWN，专用金融proof内部关系与历史完整世界尚未穷尽；当前登记字段之外不扫描JSON猜UUID。64MiB源/120000行、2048节点/20000边及每请求64typed决策/32Full策略预算超限不缩小原行分母冒成功。

本次实际定向证据（均非全量）：

- 29直接纯/HTTP检查PASS2.70s：`evidence/W2/full-evidence-graph-final-pure-20261006T002516Z-2621a39b`。
- 新Full确认链接差量4检查PASS2.28s（其中2相邻旧路径复核），31个不同直接节点：`evidence/W2/full-evidence-graph-confirmation-delta-pure-20261006T002943Z-40b8a825`。
- 最终6源strict、Ruff、format PASS：`evidence/W2/full-evidence-graph-confirmation-final-{types,static,format}-20261006T002943Z-*`；实际manifest都scope/global稳定。
- 唯一真实PG节点仅collection：`evidence/W2/full-evidence-graph-actual-node-collection-20261006T002944Z-a6fc7549`，不能算实测。需Root注册Main后统一执行。
- 首纯16FAIL/8PASS、类型41错误与第二纯13FAIL/11PASS/行长错误都原样保留；失败源码分别归档 `.runtime/full-evidence-graph-first-red-20261006T0020Z`、`.runtime/full-evidence-graph-second-red-20261006T0023Z`。最初未scoped的格式诊断只保工具输出，未声称有完整该版源码归档。

下一前置：Root注册router，运行唯一隔离PG候选（真实Account/Bank/Audit/Full源/全表读取零写/篡改UNKNOWN），取得actualSchema后新前端reader/panel消费。当前不能据源码存在或纯fixture将FULL-102关闭；实际Receipt/FullGoal模型图/跨用户集成和完整最终验收仍待。
