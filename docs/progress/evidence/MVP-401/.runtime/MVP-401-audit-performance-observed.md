# MVP-401 已有真实链 profile 只读分析

`INSTRUMENTED_TEST_CHAIN_NOT_PRODUCT_SLA` / `READ_ONLY_ANALYSIS` / `OPTIMIZATION_NOT_IMPLEMENTED`。

本文件只读分析 root 第四轮真实 Edge 验收留下的 `.runtime/MVP-401-browser-driver-fourth` 原始文件；本次读取没有启动 pytest、数据库连接、CLI 或新 GET，没有改原始 profile/阶段控制文件。本文不代替三黄金链验收，不将诊断 wall 当产品 SLA。该优化前链最终 COMPLETE，结束于 2026-10-04T15:10:22.924063Z，105 个生产来源 `changed_during_run=[]`。之后 root 单独授权最小优化部署及纯测试，其独立交还见 `.runtime/MVP-401-subject-encoding-handoff.md`；不能混用优化前 profile 和优化后 source。

## 已完成阶段实际热点

下表 wall 是 root instrumentation 记录值，包含该 profile 的保存/整理开销。`self` 为原始 pstats 的自身时间，`cum` 包含子调用；不同累计行不能相加或用作预测节省比例。

| 阶段 | instrumentation wall 秒 | convert self 秒 / recursive calls | capture_audit_subject_data calls / cum 秒 | SQL _exec_single_context calls / cum 秒 |
| --- | ---: | ---: | ---: | ---: |
| goal_allocated | 85.938 | 35.869 / 8,021,292 | 127 / 48.307 | 995 / 5.677 |
| purchase_prepared | 27.125 | 9.653 / 1,868,711 | 37 / 12.696 | 369 / 2.039 |
| purchased | 58.313 | 27.175 / 5,281,661 | 100 / 36.235 | 693 / 3.522 |
| salary | 1.219 | 0.075 / 42,325 | 30 / 0.234 | 208 / 0.491 |
| consumption | 2.828 | 0.464 / 145,747 | 60 / 0.928 | 365 / 0.868 |
| redeem_prepared | 19.250 | 7.140 / 1,589,775 | 43 / 9.103 | 375 / 1.634 |
| redeemed | 55.235 | 22.160 / 4,817,950 | 114 / 30.121 | 972 / 4.862 |

建库 0.235 秒、迁移 1.094 秒、seed 2.312 秒。不能用建库/迁移/seed 解释整轮分钟级耗时。浏览器确认阶段等待在 driver profile 外，未混入 funds 阶段函数树；完整轮耗时仍包含该等待，不能简单从一段推断整轮。

`goal_allocated` 调用 `prepare_action` 累计 24.617 秒，`execute_action` 累计 61.257 秒；`purchase_prepared` 的 `prepare_action` 累计 26.994 秒；`purchased` 的 `execute_action` 累计 58.242 秒。这三份同步 driver pstats 的热点一致：大量含完整决策轨迹的副本反复 canonical/模型校验。它们未显示 `verify_audit_chain` 为该热点来源。实际 `ledger_heads` 在三个阶段分别 30/10/18 次，累计 0.214/0.098/0.116 秒，不能把这些段的主瓶颈归给银行双腿验证或 ledger 遍历。

## 最小可证接缝：新副本编码一次

`apps/api/app/services/audit_chain.py:407–417` 的新副本流程为：

1. `domain.build_subject(**fields)`，完整 Pydantic strict/raw JSON、tenant/identity/money、版本、bank posting/external fact 原件校验。
2. `domain.subject_hash(subject)`，再次 `build_subject`，再 canonical 后带 `bounded-funds/audit-subject-v1\0` 前缀 SHA256。
3. `domain.subject_canonical_text(subject)`，第三次 `build_subject`，再次 canonical 得到文本。

`RawJsonObject` BeforeValidator 自己也 canonical+parse；`build_subject` 末尾再次 canonical 完整 subject。大型 `DECISION_RUN.input_snapshot` 因此反复遍历。该事实由实际 caller 证明：

| 阶段 | build_subject 总次数 | 直接 capture caller | subject_hash caller | subject_canonical_text caller | 其他小副本 caller |
| --- | ---: | ---: | ---: | ---: | ---: |
| goal_allocated | 645 | 127 / 9.481 秒 | 127 / 13.380 秒 | 127 / 10.708 秒 | `_subjects` 264 / 0.374 秒 |
| purchase_prepared | 279 | 37 / 2.895 秒 | 37 / 3.891 秒 | 37 / 2.829 秒 | `_subjects` 168 / 0.222 秒 |
| purchased | 468 | 100 / 7.499 秒 | 100 / 7.907 秒 | 100 / 9.807 秒 | `_subjects` 168 / 0.151 秒 |

首个必要优化候选为一个纯域内部编码入口：输入原始 fields，在同一次调用中完整验证一次 subject，形成规范 bytes，然后从这份 bytes 同时产生 text 与精确原 namespace digest，service capture 消费这三项。保留现公开 `subject_hash` / `subject_canonical_text` 面对任意 caller DTO 的 strict 重验行为；不可因 `AuditModel.frozen=True` 就相信可变嵌套 `data` 未变。可以把共用校验内部化，但不通过 `model_construct` 跳过初次验证。

这里只复用同一个新原件即时产生的编码结果，不引入跨 request/Session/事务 cache；不根据 entity ID 或 head hash 复用 current 数据，不缓存会变化的 before/after row。`session.get` 所有权检查、原件存在、重复内容精确比对、snapshot 索引、epoch、预算、bank XOR/18–19 field codec/financial validators 均保留。

该候选减少直接 capture 的两次完整 subject 重建，目标分配/准备/执行分别明确存在 254/74/200 次冗余重建；这不是预测 wall 节省量，剩余 trace/原件复制/验资金来源成本仍真实存在。暂不同时改 trace schema、丢273边界点、压缩历史证据、调整金融语义或放宽 tamper。

必要验证应沿现用户定向策略：原件 canonical text+snapshot hash 完全相等（v1、external v2、原坏来源合法历史）；原金额与各状态不变；跨 user/epoch、错版本/金额/bool/raw非有限数、可变data篡改仍失败；原件缺失/索引错配、SEALED不借新live、PREFIX/EXACT/UNKNOWN/完整receipt双腿仍遵守。以直接相关旧节点和必要真实链测量确认效果，不能拿新 benchmark 模拟账本替代。

## GET 的实际耗时与测量边界

已有 native worker profile metadata：initial 0.781 秒、goal_confirmed 0.453 秒、goal_created 0.625 秒、salary 1.922 秒、goal_allocated 56.094 秒、purchase_prepared 78.265 秒、purchased 102.532 秒、consumption 107.766 秒、redeem_prepared 107.109 秒、redeemed 129.891 秒。这些是已完成真实 GET 附近的 instrumentation wall，不是产品延迟 SLA。

后几个 worker pstats 具有不合法函数树归因，不能作为精确 GET 函数累计/节省量：goal_allocated 的 asyncio `_run_once` 累计 211.201 秒超过 wall 56.094 秒；purchase_prepared 对应累计 238.553 秒超过 wall 78.265 秒；purchased 累计 318.432 秒超过 wall 102.532 秒。`build_subject`、`_references`、`verify_trace` 的 caller 出现 asyncio `_run_once`/`select`/`_poll`，同步 `get_dashboard` 只累计 0.010 或 6.062 秒而无 caller。instrumentation 原意虽是 worker-only，现文件本身显示并发调用图污染；本文不猜测 Python/线程底层原因，也不对这些 counts 做精确归属。

记录中仍出现 convert/JSON 编码自身热点，与干净同步 funds profile 的证据相符；但 GET 请求内 token/index/cache 等更广优化应等可靠 profile 归因后单独决定。静态已知的 parse_subject→hash→预算→索引重复校验属于下一候选，不能用本批混杂累计宣称已量化收益。当前不额外读取 GET、不扩大验证范围。

## 原始证据与源码绑定

首次只读查验时 root manifest 的 105 个 `source_before` 路径当前 SHA256 全部相等；最终 manifest 已为 COMPLETE、`changed_during_run=[]`。派生 JSON `.runtime/MVP-401-audit-profile-observed-latest.json` 给出优化前原始 pstats hashes、函数 self/cum/callers；它只是读已有原始文件的分析结果，不覆原 profile/manifest。如下完成 pstats 已读取并绑定：

| 原件 | SHA256 |
| --- | --- |
| funds-goal_allocated.pstats | 7b31543f061e310d4c303c75c957b52c549e64dd7aa21f7e93e49485d07d8737 |
| funds-purchase_prepared.pstats | e35cd55c04bc4901b0dd60c597952aa02dcbb9f86f8f3653567c5f3921426d68 |
| funds-purchased.pstats | 5c957d1bfb94b936bde2d7a9cc90fb4ad3a37d7dc53164708de4b8d6faf69f41 |
| funds-consumption.pstats | 0d1078d0837a22a580e7355cdd5a07248bc7cfc59197681bedefcc5767e9a8ce |

审查源 `domain/audit_chain.py` SHA256 `bd796bd1ede119e2d1381ff0cb18e26aa962ab8e74ee8aed61cc839f4ba05464`；`services/audit_chain.py` `fc9119223e3ce19e37ba1b02b8fe518fbb21cbe267cf5c296d15528c20049bae`。本 child 未改任何这些 source。
