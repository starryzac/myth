# FULL-105：真实确认变更后的未来 DatedExpense 历史证明

2026-10-06。此包是原实际失败的必要功能接缝：用户真实确认 v2 成功后，年度保护因未证明跨版本欠付而 UNKNOWN。原失败、原 v1 gate、配置/确认/历史 hash 保留。FULL-105 完整验收仍 PENDING。

## 已实现的生产能力

新增 `domain/full_future_dated_history.py` 与 `services/full_future_dated_history.py`。服务函数：

```python
prove_current_future_dated_history(session, user_id, full_policy_view, now)
    -> FutureDatedHistoryProof
```

需要调用方现有干净 RR READ ONLY；使用服务端真实时钟与实际 User 日界，不接受客户端资金、角色或结果。复用原 `read_full_policy`、`list_full_versions`、`list_full_commands` 当前源/完整链/确认验真。额外按 policy_id、无 owner 过滤读取版本/命令实际 count，捕获完整原行；每版 evidence_ids 原分母及实际 Evidence 全保留。当前 OPEN epoch 必须等于 `current_audit_epoch`。缺 owner、版本、原确认、Evidence、完整分母、当前引用或 live epoch 返回 UNKNOWN，不给未测分母补 0。

纯函数：

```python
verify_future_dated_history(source, proof, as_of, timezone) -> bool
```

source 是实际 `FullProtectionPolicySource`。函数通过 TYPE_CHECKING 引用避免保护域循环 import。它重算原配置/hash/previous-hash、完整序号、原 command/request/result hash、明确确认与原 Evidence、有效窗口、owner/epoch、本地日和同一 as_of；当前版本/配置/确认/窗口/证据/ref snapshots 必须精确匹配。每个旧版本均须 DatedExpense，且 **window.start 严格晚于本地今天**。当日、逾期、缺版本、重复、旧非 Dated 或当前链变化不能通过。

证明只说明旧到期窗口尚未到今天，不计算已付/未付金额，也不推定过去执行结果。`unpaid_amount_proven/settlement_proven/grants_authority/bank_authority` 均固定 false；没有 `unpaid_cents=0` 等替代事实。完整财务审计/银行/所有其他保护与占用仍必须由原外层 FullProtection 自行验真。

## Root 接入的确切边界

这里只新增模块，不编辑现 FullProtection、Main、contracts、旧 lifecycle 或任何冻结金融源。Root 负责新算法 `registered-full-protection-future-dated-history-v2`，只针对 DatedExpense v>1 且本证明真实通过时接新分支；没有新证明的旧输入继续原 v1/UNKNOWN 负例。

新引用 wrapper：

```python
{"kind": "VERIFIED_FUTURE_DATED_HISTORY", "proof": proof.model_dump(mode="json")}
```

它追加到本请求 `source.reference_snapshots`，不持久化假 version/Evidence、不增加默认字段、不修改原内容/hash。纯 verifier 接受原 refs，或原 refs 加恰好一条上述精确 proof；其它/重复引用不能借此放行。source.evidence_ids 仍须精确等于当前版本原集合。旧版本所有 Evidence 在 proof.evidence_originals；外层可以 union 它们到来源索引，不将旧集合冒充当前版本证据。

仅 VERIFIED proof 可供新分支消费。UNKNOWN/超容量保留原 v>1 gate，不应把失败证明的大 payload 追加为已验证来源。当前容量 128 个版本、256 条命令、proof 512KiB，超限 UNKNOWN；这是显式支持边界，没有删减实际历史分母。PeriodicTransfer 多版本、当日/逾期旧义务、已结算/提前支付金额归因仍未由此实现。

## 实际检查原件

- 两新直接风险文件：44 PASS / 2.36s，全部合成原件/Fake RRRO，非 PG 或金融成功。`docs/progress/evidence/W3/future-dated-history-first-pure-20261006T010228Z-a0b4918a/manifest.json` scoped/global 稳定；唯一 warning 是旧 pytest cache 路径拒绝写入，不影响 44 个用例终态。
- 首类型 FAILED（PROTOCOL 常量被推为 str，DTO 默认要求 Literal），原件 `future-dated-history-first-types-20261006T010228Z-f0f34b02`；格式化后的失败前四源精确副本 `.runtime/future-dated-history-type-red-20261006T010421Z-08275544/`，格式化前首候选也保留。
- 修复仅为常量增加相同值的 Literal 注解；没有变更执行逻辑/默认值/断言。44 用例绑定的是修复注解之前的源码，按 TYPE_ONLY_DIFF 明示，不称它是最终源码上的重测。
- 最终四源 strict types PASSED：`docs/progress/evidence/W3/future-dated-history-final-types-20261006T010423Z-88c89784/manifest.json`。
- 最终 Ruff PASSED：`future-dated-history-final-static-20261006T010423Z-7288e8c6`；format check PASSED：`future-dated-history-final-format-20261006T010424Z-f3e8ffdc`。当前源终检，不重复已通过的 44 个行为用例。
- 未运行 PG、浏览器、原金融链或全量。Root 后续接原实际确认/年度保护失败节点，独占运行真实链；不能据此包关闭 FULL-105。


## Root 新算法与真实终态

2026-10-06 09:26 北京时间：FULL105未来日期历史显式新算法已实际通过原失败节点：W2/actual-future-dated-candidate-identity-history-and-tamper-20261006T012449Z-04268155，1PASS14.22s；确认后实际新version完整1098点/金额/日期/原其他发生项与独立CANDIDATE配置身份金融语义相同，原候选及新实际hash分别保留。完整旧未来版本链逐原件证明；篡改后UNKNOWN/null、注入422、原物理零写通过。旧12.88s缺历史、14.88s身份比较及14.04s Root误认candidate身份FAILED均原样保全，后两次仅修新test精确协议断言，无生产改动/负例删减。72相关pure/4strict、9annualreader/wholeWebtypes/static与当前Schema --checkPASS，Root8源FINAL68a8b097；Periodic v2/当日逾期或缺原件仍UNKNOWN；其他11模板完整资金差量尚有缺口。当前无Root金融RUNNING，Root后续必要新204/102 actual串行。21/92正式关闭及FULL PENDING、两版集中全量待不变。

首次JSONcodec pure 63PASS/1FAIL e96c5699 与type c6536f26 preserved于 .runtime/root-future-history-projection-first-red-20261006T0120Z。新proof原JSON通过严格model_validate_json读取，不弱化strict或改写原件。保留原输入默认v1与原算法hashlabel。所有旧Evidence只union外层source_evidence_ids，current source.evidence_ids维持精确当前版本原件集合。
