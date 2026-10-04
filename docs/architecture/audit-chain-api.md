# 只追加审计与核验 API（MVP-304）

状态：实现及定向验证中，统一验收尚未完成。全部用户、业务、金额和银行操作均为合成模拟。语义与保障边界见[ADR0012](../adr/0012-append-only-simulated-audit-chain.md)，真实运行记录见[MVP-304](../progress/MVP-304.md)。

## 读取合同

| 接口 | 内容 | 输入限制 |
| --- | --- | --- |
| `GET /api/v1/audit/events` | 当前轮次或指定轮次的原规范事件、分页游标 | `epoch_id`可选；`limit`为1–100；游标绑定当前用户、轮次和原事件 |
| `GET /api/v1/audit/head` | 数据库保存的预期genesis/count/tail与OPEN/SEALED状态 | `epoch_id`可选；head存在不等于整链已核验 |
| `POST /api/v1/audit/verify` | chain/reference/checkpoint诊断及实际/预期count/tail | 可选`epoch_id`、完整`checkpoint`、`mode=PREFIX或EXACT`；不接受repair/user/clock等字段 |

用户来自原MVP服务器端合成身份依赖，请求不能更换用户、授权或时钟。未知轮次404，无效参数422。列表返回`simulation=true`及`COMPLETE/LEGACY_UNAUDITED/UNSUPPORTED_VERSION`原件分类，未知版本不被改写为新版本。列表完整性只描述所显示事件的格式；整体核验由verify提供。

三接口的实际PostgreSQL事务均为`REPEATABLE READ`与`READ ONLY`；读取不创建轮次/事件、不补head、不结算、不刷新策略。POST用于提交检查点核验输入，不提供修复操作。前端生成类型来自真实OpenAPI。

## 诊断与范围

`AuditVerification`显示`status`、`chain_status`、`reference_status`、`checkpoint_status`，实际与预期count/tail及已核至的序号。错误最多100条并标明截断。结果可为`VALID`、`INTEGRITY_ERROR`、`UNSUPPORTED_VERSION`、`LEGACY_UNAUDITED`、`INCOMPLETE`；当前完整性错误不能被历史缺口掩盖。

首次激活如果已有未审计业务或旧审计行，实际genesis标记`legacy_history=true`，整体保留`LEGACY_UNAUDITED`。当前已记录链即使可核，也不能证明激活以前全部事实。只有当前链及原件合法且无完整性错误，才允许原事实幂等维护或真实封存；该处理不增加任何资金权限。

OPEN轮次比较当前已知不可变原件，删除已捕获原件会报告完整性错误；正常状态变化按不可变字段合同核验。决策当时已经缺失、显式列入`missing_evidence_ids`的来源不会补造。SEALED轮次只核同轮次永久副本及完整归档manifest，不读取复位后相同UUID的新对象。

决策响应顶层`audit_chain_status`表示当前实际核验状态。原历史`explanation.audit_chain=NOT_IMPLEMENTED`保留原生成内容；读取不会修改历史解释或原trace hash。

## 命令行

```powershell
.\make.cmd audit-verify
uv run --frozen python scripts/verify_audit_chain.py --checkpoint-output .runtime/audit-checkpoint.json
uv run --frozen python scripts/verify_audit_chain.py --checkpoint .runtime/audit-checkpoint.json --mode PREFIX
```

默认核所有保留轮次；`--epoch-id UUID`只核明确指定轮次，输出分别标示`ALL_RETAINED_EPOCHS/SINGLE_EPOCH`，同时显示已注册/已选择轮次数。`--user-id`是本地只读核验范围选择，不是HTTP身份入口或资金权限。一次事务核当前数据库快照，核验同时检查轮次ordinal/前轮seal链接。

CLI仅完全VALID退出0；未完整核验退出1；配置、文件、数据库不可用或轮次预算问题退出2。无事件、历史缺口、未知协议、预算不足和篡改均不可宣称成功。迁移旧库不伪造genesis；正式旧库没有新审计轮次时，核验明确未审计，不能把空链当VALID。

检查点包含实际genesis/count/tail、前轮seal和摘要。PREFIX允许其后真实追加；EXACT要求当前完整head相同。检查点是调用者提供的可信原文件，hash不是签名或外部存证。导出写原规范UTF-8文本，使用独占创建，原文件已存在就拒绝覆盖。输入最多65536字节并有界读取。CLI最多装载1000轮元数据；单轮fullverify在SQL先汇总10000 events、20000 snapshots与64MiB原文本预算，超限返回INCOMPLETE。

## 写入与复位

十类业务事件在真实决策、动作、银行独立提交、投影、恢复、策略及目标接缝录制，另有实际EPOCH_STARTED/EPOCH_SEALED。事件使用调用方事务，银行结算与应用回执仍分属原独立事务。一次投影不产生第二次银行资金流；UNKNOWN、T1等待和投影失败保留真实观察，GET不触发结算。

普通owner与非owner UPDATE/DELETE/TRUNCATE受数据库保护；head正常推进只来自实际新增事件。管理员ALTER/DROP/禁用保护在保障边界之外，真实故障注入测试验证其后的异常检测，不能将hash链称为管理员不可篡改存证。当前MVP仍使用原本地模拟数据库连接，未宣称已部署生产非owner API角色；认证与更强审计按FULL计划实现。

`make seed/demo-reset`真实封存旧轮次、归档显式业务图，再建立新业务与新轮次；整个动作单事务失败回滚，跨三段执行/恢复的共享gate使复位等待整个命令结束。金融种子保持`mvp-301-v6`；`seed-summary-v2`业务摘要独立于增长的审计历史。seed命令另输出只读观测的`audit_metadata`，包括真实head、保留轮次/事件/副本数和观察时刻，不把head元数据冒充核验结果。

禁止用复位删除审计历史。旧库未审计缺口即使封存也保留，不能通过重新初始化将全部历史改标VALID。
