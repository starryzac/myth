# FULL-607 独立模拟银行只读对账

## 功能与原验收状态

2026-10-05 UTC 功能优先执行修订：新增可运行 domain/service/API，根任务已注册 GET `/api/v1/reconciliation/current`，operationId `read_full_current_reconciliation`，使用固定实际模拟 owner、服务器业务 clock、干净 REPEATABLE READ / READ ONLY Session。原 F:1356–1358、710–719 要求仍 **PARTIAL**；本包不凭纯测试关闭九类动作、全部状态或进程硬重启验收。

候选 API 不接受客户端金额、银行事实、owner、clock、回执、权限、修复结果或额外 query；没有 POST 修复接口。`simulation/read_only=true`；`grants_authority/executes_funds/repairs_performed/receipt_is_current_authority/economic_verified=false`。人工待核对只是本次只读推导状态，不写人工任务、修改账面、补造银行事实或自动执行恢复。

## 实际来源和可运行核验

2026-10-06 03:34更新：上述真实PG候选已由Root唯一批次运行通过，W5/actual-audit-scope-original-event-repaired-and-full-readers-20261005T180751Z-fbbaffb2实际3PASS3728.50s、exit0、范围源稳定，包含本607节点及审计/问答两节点。此批未记录单节点耗时；不以整批耗时当607性能。原副本和FAILED证据保留。下方NOT_RUN与候选仅收集是历史状态；全九类、归档/并发/硬重启、浏览器及最终验收仍缺。

同一请求 RR 事务读取实际 User（必须 is_simulated）及八个 owner 表：accounts、asset_positions、goals、action_plans、bank_operations、simulated_bank_redemptions、action_receipts、simulated_bank_postings。每表先取真实 SQL 行数分母，再完整读取限定容量内的原行，记录 actual_count/captured_count/complete。普通表上限10000、postings上限100000；超容量保留实际分母及 UNKNOWN，不把部分账本送入完整核验。输入 hash 包含实际完整原行、owner/as_of、原审计结果、原目标证据/hash和诊断。

复用现生产 `ledger_heads` 校验独立银行连续账本，`validate_bank_projection` 比较应用投影，`verify_audit_chain(..., mode='EXACT')` 校验原 typed 审计。请求内 `historical_ledger_scope` 仅去重本次历史核验，没有跨请求授权缓存、FOR UPDATE、执行调用、commit 或资金写入。审计 wall append clock 与业务模拟 clock 不混比；未来业务 posting/account observation/position 保留具体 MISSING，不借未来入账。

现金和持仓本金差额为**应用减独立银行**；不比较信用卡负债、不把未到账利息或市场收益当现金。未知 bank/head 数值保持 null，差额 null，绝不补零。head entity、dimension、目标 account 和原 metadata 不符时保留原 head 引用、INTEGRITY 诊断，并令子项 MISSING/null；金额恰好相等也不能标 MATCHED。

Goal 读取实际 allocated 和完整 current owned position 清单，核原 `SIMULATED_GOAL_OWNERSHIP` Evidence 的 owner/有效窗口/content hash 和完整 `goal-ownership-v1` 数值与 position IDs，再分别比较独立 GOAL_CASH/GOAL_PRINCIPAL。旧恢复协议未逐次写产权维度时明确 UNSUPPORTED/null，不由应用成功日志补产权真值。

每个原 action 保留原 status/key/request hash/effect hash、expected/actual amount、fees/loss、完整操作/原腿/回执 ID 与状态。泛型结算调用原 `_identity(..., lock_user=False)`、完整 `_legs` 和 `verify_execution_receipt`；旧恢复调用原 `verify_recovery_receipt`。只有真实独立完整效果核验可填实际金额，只有原回执校验通过可填 service_receipt_verified；status SETTLED/SUCCEEDED 字符串不够。未知银行操作/缺原回执/重复 operation、business key、closing position、孤儿腿或引用均保留具体诊断。

银行已结算而应用未投影时，原 SUBMITTED/UNKNOWN 不改：动作返回 BANK_SETTLED_APPLICATION_UNRESOLVED、保留原 key，实际原腿可 verified、receipt 未 verified；现金差额照实返回，并独立记录 pending_application_projection_explained。报告不重扣；`read_original_action_path` 只指向原 action 查询，`query_original_key_only=true`。无银行原件的 UNKNOWN 不是终败，actual=null；只有实际 REJECTED 原操作 identity 已核且零腿/零回执时才标 BANK_REJECTION_VERIFIED/实际0。

整体 MATCHED 需八表分母完整、独立 ledger verified、当前 projection matched、EXACT audit VALID 且 issues 为空。prepared/no-bank 原行动可能存在于整体 MATCHED 报告中，这只说明本时点账目吻合，不表示它已执行。INTEGRITY/实际差额进入 MANUAL_REVIEW_REQUIRED；其他缺原件、未支持、pending 保留 UNKNOWN。单个未覆盖项目始终在 uncovered，不缩小分母假成功。

## 直接检查与保留原失败

必要直接检查日志在 `.runtime/FULL-607`，没有运行 PG/Browser/Docker/全量。

- 首 strict 新服务10错（局部 rows 注解/同名 loop 变量类型），第二1错（误插 model_copy 局部），均保原输出，归档首生产源 `first-type-source`；窄修只涉及新源类型。
- 首纯实际 **6 failed / 22 passed，3.13s**，`pure-first.log` 与六源 `first-pure-failed-source` 保留。原因是新核验器误用 strict BankCommand.model_validate 接原 JSON 字符串 UUID；改用与原真实服务相同的 model_validate_json，不改金融门或原请求/hash。
- 修后28 PASS1.97s；最终增加容量、拒绝/未支持动作以及独立审查指出的错误 head 归属风险，**34 个直接 domain/service/API 纯测试 PASS2.01s**，`pure-final.log`。这些用明确 synthetic ORM/verifier doubles，只证明合同/调用门，绝非银行效果、正式案例或真人研究证据。
- 新 integration 首类型2错（原 module 非显式 process_operation export、真实 DTO 是 receipt_id）及1 Ruff 长行已保留 `first-integration-static-failed-source`，仅改新测试引用/名称。最后 **7 strict types、Ruff7、format7 PASS**；日志分别 `types-final-seven-repaired.log`、`ruff-final-repaired.log`、`format-final.log`。
- 唯一真实 PG 候选仅 **1 node collected2.65s**，`collection-final.log`；未实例化 fixture/连接业务 DB，**actual PG NOT_RUN**。

最终七源原 bytes 和检查原件：`.runtime/FULL-607/final-source-20261005T175146Z/manifest.json`，SHA `9e8716a6b0a66a7633cff993f47819e671bbed5ec499b5c69c532d796e62a848`。生产三源保持 HOLD 给根实际检查；这是精确七源范围，其他独立生产/前端并行变化，不能声称全仓冻结。

## 唯一真实 PG 候选与明确缺口

`apps/api/app/tests/test_full_reconciliation_integration.py::test_actual_reconciliation_preserves_bank_truth_unknown_and_original_key_without_repair`。根任务单金融链执行；本 agent 没有运行。fixture 仅在新 generated local bf_test 用原 production private second CASH 初始种子（零开户在 genesis 前），不 reset 正式库、不改原历史、不冒称冻结实验输入。

候选包括原实际 seed 完整现金/本金匹配；真实 ExternalFact INCOME 和公开 Goal 创建/确认/原 goal allocation 后银行产权与原服务回执；原确切 ASK transfer 确认后银行真实 commit 故意丢响应，GET 保留 UNKNOWN、实际两腿/现金差额/原 key，且所有动态反射物理表含 Alembic 前后零写；明确调用原 execute 恢复同原 operation/key，不增加银行效果；故意只在该 disposable DB 改应用现金+7和回执 executed+1，GET 必须原差额和完整 integrity 人工待核对，不能修复。全部无权限/执行/经济旗仍 false，假 query422/POST405。实际原表数量由 physical_snapshot 动态反射，不固定旧迁移数量。

仍未实测：此候选的真实 PG 闭合结果、完整九类动作及所有 ACCEPTED/REJECTED/UNKNOWN/legacy 恢复组合、进程硬重启/并发、完整重置后的归档对账、正式金融案例、真实资金接口和人工处理后的落账功能。信用卡债务/利息估值、旧 legacy 目标产权缺独立完整 postings、超容量原件仍明示边界。功能页由独立前端包接入；没有据本报告开启任何新 FULL 模板银行权限。
