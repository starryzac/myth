# FULL-402/403/404 完整组合执行消费者

状态：生产严格域、只读预览、不可变组合/批次/确认存储、原 purchase 有序消费者及两原门导出已实现；必要纯/类型/静态通过。单一真实 PG 节点仅 collection，实际执行 NOT_RUN；FULL 三编号未关闭。按用户已批准的功能优先执行修订新增本消费者，原规划及全部原失败证据保留。

## 可运行能力与边界

新增 `domain/full_asset_execution.py`、`full_asset_execution_guard.py`；`services/full_asset_execution.py`、`full_asset_execution_store.py`、`full_asset_execution_dispatch.py`；`api/v1/full_asset_execution.py`。另四直接测试和一个真实集成候选，共十一源码。主协调器独占共享三 ORM、0013 迁移、Main/dependencies/contracts 及旧执行/银行接缝，本包没有修改它们。

只读预览读取真实原 Full 资产策略/确认/版本/引用、原 MVP 资产权限及目标当前原版本、不可变目录绑定、原已核现金/收入/exposure/365 日保护及 Full 额外保护。只接受身份及收紧的规划模式，客户端不能输入金额、产品、汇总事实、权限、时钟或成功结果。原旧规划响应及其中 planning-only/NOT_IMPLEMENTED 字段仍原样返回；新增消费者状态单列。

服务器保留原有限组合的所有子产品、整数分金额、来源、原目录版本/record hash/terms digest、原 MVP policy versions、原 Goal 归属及每个完整旧 BankCommand。新 whole hash 与旧 effect hash/command hash 分列，旧 ExecutionEffect/BankCommand schema、dump、哈希及历史均不改。全组合最多四批，各自原可行性成立仍不足以执行：先联合扣除全部真实当前现金/目标现金，检查完整来源碎片分母及整个原/Full 1098 个保护点、总已管理/未决暴露及组合预算。未来工资为零，计划赎回不作为已承诺到款；固定期限按实际 accept 时刻保守重算；不得放松原目标最低或借用另一目标归属。

准备在同一事务先持久 parent 与全部 immutable Batch→Action 关联，再创建原 ASK_ONCE/PLANNED 子 Action 与完整 PREPARE request/typed trace。整组合 PLANNED_UNRESERVED，不伪称已预留。确认显式绑定原 whole hash/epoch/accepted，追加 whole 原确认 Evidence/不可变 Consent，并通过原每动作确认/重验/审计路径记录确切原 effect 确认。它不制造 MVP 许可、不发布自动 outbox。

顺序执行仅调用原真实 purchase 三阶段服务。首次 phase1 和首次银行 accept 分别在原 User 锁内复核原组合绑定、当前 Full/MVP 权限、实际目录/来源/完整剩余组合；bank 阶段只移除经原 action/command/income/exposure 证明的自身预留，全部其他 claim/UNKNOWN 仍保护。两接缝缺真实部署版本立即拒绝。Root 固定查询实际 Batch.action_plan_id，即使 marker 与新 bank key 同时被删除也不能降为 legacy；本 guard 被真实关联调用时缺 marker 直接拒绝。

前批必须原 SUCCEEDED/RECONCILED + SETTLED + 完整核验 receipt 才允许后批。响应丢失保留原 Action/key/银行结果；同 key 原操作恢复不产生新银行请求、不重新授予已撤销权限。每次 execute 明确固定批号及原 action ID，已完整 receipt 的该批返回原只读状态，重放不会执行后一批。多个已提交银行操作不承诺整体回滚，跨操作 atomicity 明确 NOT_AVAILABLE。

## 实际公开接口

路由统一 `/api/v1/full-asset-executions`：

- `POST /preview`：严格 FullAssetPrepareRequest；必须 Root RR/RO Session，只读。
- `POST /prepare`：相同请求；当前 server 计算原金额，持久 whole/key/全部批次。
- `GET /portfolios/{portfolio_id}`：当前/保留历史完整原件，只读，无当前权限承诺。
- `GET /commands/{epoch_id}/by-key/{key}`：原 PREPARE 或原 CONFIRM 查回，严格 owner/epoch，NOT_FOUND_NOT_FINAL 不终局、不准换键。
- `POST /portfolios/{portfolio_id}/confirm`：`accepted:true, reviewed_portfolio_hash, expected_epoch_id, idempotency_key`。
- `POST /portfolios/{portfolio_id}/execute-next`：`accepted:true, reviewed_portfolio_hash, expected_epoch_id, expected_batch_number:1..4, expected_action_id`；固定原批，不自动循环。

FullAssetPrepareRequest 的九身份/模式字段为 `full_policy_id, expected_full_policy_version_id, mvp_asset_policy_id, expected_mvp_policy_version_id, goal_id, expected_goal_policy_version_id, expected_epoch_id, idempotency_key, planning_mode`。Goal 两字段共同 null 或共同有值；canonical 原请求包含 null/default PORTFOLIO，客户端建议显式发送，client intent hash 与原 server request hash 分列。所有公开 REQUEST UUID 使用原 UUIDReference，JSON UUID 字符串合法，strict bool/int、unknown fields、金额/时钟拒绝规则保留。key 使用原可编码有限字符 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}`。

Lookup 有 `simulation:true, command_kind:PREPARE|CONFIRM|null, original_request, request_hash, original`；同 owner/epoch 的准备与确认不能复用同 key，历史双命中拒绝猜测。original_consent 保留完整原 Confirm body/key/request hash/user/epoch/portfolio/evidence hash/original Evidence。当前 Evidence 真匹配才 current_evidence_verified/旧 original_consent_verified 为 true；SEALED 后缺当前 Evidence 明确 RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING、false，不能称当前权限或独立归档验证。OPEN 缺 proof 仍拒绝。

## 本包实际检查与失败保全

所有下面命令均 `uv run --frozen`，只有必要 pure/type/Ruff/collection，无 PostgreSQL/浏览器/Docker/正式历史操作。日志与失败源在 `.runtime/FULL-402-403-404/`，源冻结 manifest 由最终交付附上；不把分次结果加成一次全量 PASS。

- 域首 21 PASS/1 FAIL 13.18s（错误夹具期待满 cap 800000；原有限优化器在 rounded-yield 平台按换手选择 799792，算法不改）；窄修期望后域 22 PASS13.47s。另新整组合硬保护负例和四分源节点 5 PASS3.12s。
- 原消费者首 35 PASS/1 FAIL77.69s，已结算 position 合成夹具缺原 exposure.counted_position_ids；补真实合成分母分类后，只该失节点+两个新公开拒绝节点 3 PASS7.75s。原失败源/日志不覆盖。
- 公开固定批次合同差量首 16 PASS/1 FAIL17.20s：四实际 HTTP JSON UUID handoff、金额/query/伪 grant/strict batch/原确认负例均过；仅新固定批重放合成测试错误给 frozen ActionResponse 赋值。改用 model_copy 后只该节点 1 PASS9.37s。两个 bound-missing-marker 负例包含于首 16 PASS；新原 JSON marker 两 guard 真 handoff 1 PASS4.49s。测试 sentinel 在实际服务入口停止，没有 mock 金融成功。
- 最终十一源 strict mypy PASS：`public-fixed-contract-v2-final-types.log`；Ruff PASS：`public-fixed-contract-v2-final-ruff.log`。最后格式检查交付日志单列。首次域循环变量类型、两个 Optional UUID、旧测试非显式 export，以及新测试 Optional ActionResponse 和一个 I001 均保留各原日志/源，只窄修类型/导入。
- `public-fixed-contract-v2-actual-collection.log`：下面实际风险节点 **1 collected/3.72s**，没有执行。

## Root 单链实际候选与仍未覆盖

唯一候选为 `app/tests/test_full_asset_execution_integration.py::test_actual_whole_asset_portfolio_keeps_order_and_recovers_original_unknown_once`。它使用真实 generated bf_test fixture、原 payroll bank 事实、原目录登记/Full 规划确认/MVP 生产声明与确认，无伪权限行：实际公开预览零写→整组合 prepare 无银行及资金变动→缺 whole 确认拒绝→真实 immutable Batch 关联下剥 marker+key 两门拒绝且零写→明确 whole/各原 effect 确认→两个原 key 查回→旧 action 后批越序拒绝→真实银行 commit 后 TimeoutError→原 SETTLED/no receipt/UNRESOLVED 保留→后批阻断→固定同 key 恢复/重放零写→用户明确各原后批→完整原 bank key/command/receipt 分母与 EXACT audit VALID。目标是验证原管道，不依据成功字符串替代原银行账本。

该节点尚未真实运行。七类真实产品未齐备（当前实际 T0/T1/30天），七/九十天/LOW_RISK_TERM 不生成补齐；真实 Goal 归属投资/多期限梯度、到期再次配置、并发双组合、进程硬重启与全部 crash 边界仍待各自真实证据。资金效果/实验经济验证始终 false，不因 service receipt 变真。没有全量验收、真人研究、真实资金接口。

Full 资产 lifecycle 605 主动关联失效尚需 Root 接线，当前两 fresh guards 已拒绝失效版本，但这不等于主动 Action 状态重查完成。最小接口建议：`recheck_full_asset_execution_actions(session,user_id,full_policy_id,current_version_id,current_status,command_id,now)`，在 FullLifecycle 新命令同 User 锁/TX 内按 parent 原 FullPolicy identity→完整 immutable Batch→原 Action 取所有依赖，不能把 FullVersion IDs 传旧 MVP `_invalidation` 后声称覆盖。核完整 request/command/marker/原 PREPARE trace；PLANNED/AUTHORIZED 且原 `_unsubmitted_command` 证明无经济/receipt 后，原 `_release_unsubmitted_claims`、INVALIDATED transition/exposure；SUBMITTED/UNKNOWN/任一 bank 原件保原 key/claims 为 inflight，SETTLED + 严格 receipt 为 terminal。原命令 replay 跳过重复重查，immutable parent/batches/consent 与历史 hash 全保留；已 settled 原 key 恢复不重当前 grant。
