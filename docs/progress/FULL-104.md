# FULL-104：完整模板的持久声明生命周期

## 实现状态与原验收状态

2026-10-05 已实现八种新增 FULL 模板的可运行持久规划声明、严格用户确认、不可变配置版本、连续命令链、暂停、撤销、重新确认恢复、日期生效与到期刷新，以及响应丢失后的只读原键恢复。代码实现与原需求关闭分开：本批直接纯/API及静态验证通过，真实隔离 PG 风险测试已收集但尚未执行；原编号 **PARTIAL，未关闭**。FULL 金融执行适配、未提交动作失效、在途动作复核及持仓/边界实际重算未在本包实现。

本包按用户已批准的功能优先执行修订推进，不等待初版全量；原初版25项、完整版67项要求与证据关闭条件保留。

## 文件与持久边界

- `apps/api/app/services/full_policy_lifecycle.py`：有限生命周期、配置/证据/引用验证、版本链与命令链、原请求重放、历史只读与影响预览。
- `apps/api/app/api/v1/full_policies.py`：严格生产 API。主应用注册及 RR READ ONLY 读取依赖由 root 集成。
- `apps/api/app/tests/test_full_policy_lifecycle.py`、`test_full_policies_api.py`：直接状态、确认、原证据负例、严格接口、编码键与未知结果保护。
- `apps/api/app/tests/test_full_policy_lifecycle_integration.py`：八个实际隔离 PG 风险节点，由 root 唯一金融序列执行；本包未自行运行。
- 共享 `apps/api/app/db/full_models.py` 与 `0009_full_policy_lifecycle.py` 由 root 集成：独立 `full_policies`、`full_policy_versions`、`full_policy_commands` 三表。旧 MVP 模型、Canonical、审计哈希及原生命周期没有修改。

八种模板为 DatedExpensePolicy、PeriodicTransferPolicy、AssetAuthorizationPolicy、RecoveryPolicy、GoalAllocationPolicy、CrossGoalReallocationPolicy、SeasonalReservePolicy、InterventionPolicy。相同的 Recurring/Living/Emergency 继续使用原 MVP 生命周期；LongTermGoal 使用 FULL 目标桥，不重复存储或改写原 `goal_saving` 哈希。八种新增声明的 `execution_support=NOT_IMPLEMENTED`，确认不创建银行授权、资金效果、ActionPlan 或银行命令。

新版本原配置使用已有 FULL_V1 Schema 校验与原 canonical `configuration_hash`；确认必须 `accepted=true` 且 reviewed_hash 精确匹配。实际 SQL 核 owner、当前 OPEN epoch、Goal/Account/Policy 引用及其原版本/哈希；收款关系由原 BANK 身份解析器核验，不能由候选自报。涉及 FULL 资产的新增类别仅为声明合同，不扩大旧执行引擎权限。

版本只追加，按 version_number、previous content_hash 验连续性；命令按 command_number 与 previous result_hash 验连续性，保原 request/result 哈希、同 owner/epoch/version 与原确认绑定。相同业务时刻依靠单调 command_number 排序，不依靠随机 UUID。数据库不可变触发器由 root 的共享迁移提供，版本/命令 UPDATE/DELETE 与策略身份修改应被拒绝。

业务生效/到期使用可信模拟时钟和用户时区；原审计 epoch 的 appended wall clock 不与金融业务时钟比较。窗口右端排除；过期/撤销不可恢复。暂停恢复必须再次明确确认原配置并追加新版本；修改使用 expected_version_id，旧版本冲突拒绝。重复原键仅在原 request 与 hash 完全相同时返回原不可变结果，不把历史收据当当前权限。

## API 与原结果未知恢复

基础路径为 `/api/v1/full-policies`。所有 body 拒未知 owner、clock、grant、effect、result 字段；所有路由拒附加 query。

| 路径 | 能力 |
| --- | --- |
| GET 基础路径、`/{policy_id}`、`/{policy_id}/versions`、`/{policy_id}/commands` | 当前或保留历史的实际链只读查询；返回原版本与命令。 |
| POST `/confirm` | 明确 FULL 规划确认并持久创建版本1与 CREATE 命令。 |
| POST `/{policy_id}/change`、`/resume` | 当前版本并发校验；明确确认后追加版本和命令。 |
| POST `/{policy_id}/suspend`、`/revoke` | 保原版本，追加状态命令，不产生银行操作。 |
| POST `/time-refresh` | 以服务器可信业务时钟登记实际生效/到期，不能输入用户时间。 |
| GET `/commands/by-key/{idempotency_key:path}` | 响应丢失时按固定 owner 的原键恢复；精确查原命令并验证完整版本/命令链。 |

原键长度1至160且非空白。前端对完整键使用 `encodeURIComponent`，path converter 支持编码斜线。`FullCommandLookup` 返回 `status=RECORDED|NOT_FOUND`、`idempotency_key`、`original_request`、`request_hash`、`command`。NOT_FOUND 的后三项为 null，始终 `not_found_is_final=false`，因此不能据此清除未知 pending 或宣称未提交。RECORDED 返回该键原 command/result/version/epoch，而非猜测当前最新版本。所有响应 `bank_authority=false`；命令恢复与执行收据 `receipt_is_current_authority=false`。

用户命令的原请求封套为：

```json
{
  "protocol": "full-policy-command-v1",
  "kind": "CREATE",
  "user_id": "实际服务器用户UUID",
  "policy_id": null,
  "body": {
    "template_name": "InterventionPolicy",
    "configuration": "原始严格请求中的配置对象",
    "reviewed_hash": "原审核配置hash",
    "accepted": true,
    "reason": "原用户原因",
    "idempotency_key": "原用户键"
  }
}
```

示例字符串仅说明字段，不是可运行配置或成功原件。CHANGE/RESUME/SUSPEND/REVOKE 的 policy_id 为实际 UUID、body 为相应原严格 DTO。REFRESH_TIME 是服务端登记的 version_id/target/boundary_time 封套，不接用户自报时钟。

## 历史保全

旧 SEALED epoch 只读，不能修改、恢复或成为当前规划权限。不可变版本与命令仍验证原配置、确认封套及原请求/结果链。若 reset 后当前 EvidenceItem 已不存在，状态明确 `RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING`；没有查询并校验原 AuditArchiveSnapshot，不能声称归档证据已验证。此时 current planning=false、bank authority=false。当前 OPEN 缺原确认 EvidenceItem 则拒绝，不能沿用历史封套当新证据。

## 已运行检查及原日志

原日志在 `.runtime/FULL-104-105-pure-contract/`，此前 Ruff/import RED、各阶段纯测试与类型输出保留。最终源码原件与精确 SHA 在 `source-freeze-20261005T135412Z/manifest.json`，不是全仓冻结。

```powershell
uv run --frozen python -m pytest apps/api/app/tests/test_full_policy_lifecycle.py apps/api/app/tests/test_full_policies_api.py apps/api/app/tests/test_full_policy_configuration.py apps/api/app/tests/test_policy_configuration.py -q -p no:cacheprovider
uv run --frozen mypy --explicit-package-bases apps/api/app/services/full_policy_lifecycle.py apps/api/app/api/v1/full_policies.py apps/api/app/tests/test_full_policy_lifecycle.py apps/api/app/tests/test_full_policies_api.py apps/api/app/tests/test_full_policy_lifecycle_integration.py
uv run --frozen ruff check <上述五个本包文件>
uv run --frozen ruff format --check <上述五个本包文件>
uv run --frozen python -m pytest apps/api/app/tests/test_full_policy_lifecycle_integration.py --collect-only -q -p no:cacheprovider
```

- 直接纯/API及原 Schema/hash 兼容：**442 PASS / 4.37s**，原日志 `pure-final-with-recovery.log`。
- 严格 mypy：**5 文件 PASS**；Ruff PASS；format 5 文件 PASS，各 `*-final-with-recovery.log` 保留。
- 实际风险候选：**8 节点 collected / 1.78s**，`pg-collection-with-recovery.log`；**NOT_RUN**，不当实际 PostgreSQL 成功证据。
- 测试含当前 TestClient 的原依赖弃用提示，未变更依赖。
- 旧 `policy_configuration.py` SHA 为 `e3645cd3faa23921b765bc7e952d4953489d508eb39e88065632e24d49c94657`，本包未修改。

root 已报告共享0009/0008迁移定向 **2 PASS / 8.17s**，证据 `evidence/W2/full-policy-additive-migration-and-original-delivery-real-pg-20261005T133131Z-c5a42f5f`：旧26 typed表逐行与哈希保全，新三表空，物理30表/ORM29。此迁移证据不能替代本服务的八个实际生命周期风险测试。

## 具体未覆盖与下一前置

原 FULL-104 的未提交失效、在途动作复核、撤销后的持仓/边界恢复和冲突动作集合重算尚未接新 FULL 模板；响应明确 `action_dependencies_supported=false`，空 invalidated/inflight 数组不能表示已验证没有相关动作。FULL 专用 typed 审计事件尚未纳入原审计 subject 协议，`dedicated_audit_event=false`；新追加版本与命令链也不冒称完整 typed 审计验证。

待 root 串行执行八个隔离 PG 节点，保留全部失败并按实际原因窄修，再记录前端真实流程。随后需将已确认 FULL 版本接入确定性的 FULL 规划/执行依赖，才能关闭原 FULL-104。验收全量仍待最终集中节点。

## 2026-10-05 13:55 UTC 实际数据库验证追加

root 已实际执行本包全部八个隔离 PG 节点：**8 PASS / 27.34s**；原包装命令耗时 **29.367692s**、exit0。原件 [manifest](evidence/W2/full-policy-lifecycle-preview-history-original-key-real-pg-20261005T135521Z-7b52013c/manifest.json) 记录 `all_source_stable=true`、`scoped_source_stable=true`、source_changes=[]。实际范围包括八模板持久声明/旧金融行保全、版本与状态/原键重放、真实生效与到期、同owner与冲突/缺证据零写、不可变数据库触发器、只读预览全表零写、真实reset后的SEALED链保留及非当前authority、原CREATE键在后续CHANGE后恢复原request/hash/version/receipt。

此前 NOT_RUN/collection 是运行前的真实记录，保留不改。现在解除本包五源的此轮运行冻结；后续改动须另登记。该实际模块命令不是初版或完整版全量验收；上述 FULL 金融执行、动作依赖与独立归档原件核验缺口仍保留，原 FULL-104 未关闭。
