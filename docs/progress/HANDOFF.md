# 开发交接（持续更新）

目标：依据两份计划先完成初版，再推进完整版，直到功能、实验和交付逐项验证。目标 active，无 token budget；不得将工程骨架或单模块视为全部完成。

## 当前里程碑与精确下一步

- 已完成 11/92 项：MVP-001/002/003/101/102/103/104/105/201/202/203。最小未完成 MVP-204；其后 205/301…，最后才推进 67 项 FULL。
- MVP-203 完整 check `20261003T201835Z-2ff08f18` exit 0：639 后端（1035.56 秒）、4 前端、1 Edge E2E；103 个源码/配置摘要逐项重算一致，11 条命令全部成功。整体语句覆盖率 96%，目标纯域95%、来源97%、创建93%。证据和 manifest 在 `docs/progress/evidence/MVP-203-*`。
- 本项源码冻结并完成；原 root session 99339 已退出0，全部代理均已归还修改权，无仍在运行的定向测试。types run `20261003T201315Z-93ad5557`。下一步提交203，然后按ADR冻结204接口和来源协议后再编码。
- 203域38、PG服务27、HTTP5、独立13（5×200=1000有效生成样例）均通过。真实修复包括旧来源之后DEBIT水位、本地deadline、未来目标不可执行但基线仍保护；共享boundary上下文原44服务+3HTTP也通过。创建只绑定现有账户、初始归属0，不认领旧钱；实际原子消费和执行幂等留301。
- 实际库仍为迁移0002、种子mvp-202-v3。两次 seed run `20261003T193225Z-b2775ed0` / `20261003T193310Z-33c5ee8f` 业务JSON一致，SHA256 `701a80942c4299e1c2274a89b907859f6d7f6d22621a9e2b2be636487e3f6a66`。129流水、253证据、5账户、3账单/产品/持仓；现金3462400分、本金500000分、未付账单145000分。203未修改正式演示数据。
- 正式演示库最近只读 boundary READY、3157400分、91日/273检查点，16表前后摘要相同，证据 `MVP-202-demo-boundary.json`。203完整检查未用旧dev服务代替源码；Edge自启18000/15173。
- 204预审仅只读：支持general与单goal放置，goal自有cash转同goal principal、allocated/month contribution不变，两条当前有效授权和双向引用均校验。截止日之前一日本金可用；净收益仅排序，不进安全边界。新产品完整条款追加v2/new UUID，保持旧v1/旧持仓；预计seed v4需独立重复验证。
- 204需冻结明确无损退出计划、ACT365整数收益/比较窗、跨旧策略版本和同scope的管理额度占用、完整曝光证明。缺失或UNKNOWN不猜空额度。domain+tests、来源服务+PG、独立oracle可在同一任务内并行；root负责ADR/HTTP/seed/合同/最终验收。204代码尚未开始。
## MVP-203 已接受语义（ADR 0006）

SEM-07 已冻结。新增来源只接受当前适用策略确认/生效之后、实际到账 CREDIT、BANK_CONFIRMED 且银行 economic_role=INCOME；不可由可编辑 category 推断。旧余额、OPENING、内部划转、退款、本金回款、未来预测均不生成新资格。来源必须有完整、同一快照的未消费、已消费及 pending/UNKNOWN 预留证明，不能用工资金额减目标贡献而忽略普通消费。逐来源账户核对，不拿其他账户旧余额补收入资格。未用 lot 可跨月保留，月范围重置不重建来源。

每目标剩余 min/target/max 按当月累计贡献相减并受总目标缺口封顶；默认接近 target，max 是上限。对候选 x 保持合并现金不变，按实际源/目标账户投影，归属和本月贡献各加 x，然后重算 202 的全部检查点。不能只取 safe_idle，也不能手动释放所有未来最低保护。固定快照下可用整数二分，返回值及加 1 分分别独立验证。比剩余最低少时返回明确 MINIMUM_SHORTFALL，不能偷偷改 min；既有风险或来源不足关闭 READY 规划。

只读预览相同输入返回相同计划，不消费来源。例 D 工资 500000 分贡献 100000 后来源仍有 400000；回执后同月重算为 0 是因为 target 已达。实际行锁、原子消耗、账户划转、归属/月贡献/回执、并发不同键争用和 UNKNOWN 预留释放由 MVP-301 实现并验收。不可用纯函数重放冒充执行 exactly-once。分工建议：域＋单测、来源服务＋PG、独立 oracle；root ADR/HTTP/合同/验收。

## 已接受语义与后续边界

- ADR 0005：严格剩余现金保护，day0..90；未来收入零；目标现金与本金拆分；普通周期和账单去重；生活当前估值滚动底线；自然月最低；同日先检查付款再计可用本金。financial_only 不等于 AUTO_EXECUTE。
- 单版本普通策略自然到期保留已生成旧欠款；复杂暂停/修改历史无法唯一恢复时 HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED→INSUFFICIENT/null。不能把 updated_at 当首次停止时点。
- 来源证明同时绑定余额、账户类型、账单身份、持仓归属与时间；累计目标归属与本月贡献必须同一 epoch 且覆盖余额水位。past available_at 但未结清持仓须对账，不能当已到账重复增加现金。产品保证返还条款不得与目录锁定期、延迟、风险或续作矛盾。
- 生活估算 201：前 56 个完整日、14 日 43 个重叠窗口、精确 nearest-rank；来源覆盖或分类冲突返回不足，不能零填未知数据。固定种子不会自动延展成后续日期完整历史。
- 策略版本不可 UPDATE（迁移0002），五个模板确认绑定、锚点和来源校验已实现。确认/修改幂等重放返回历史命令结果，UI 后续应 GET 当前状态再展示/执行。
- 执行 UNKNOWN/SUBMITTED 等在途状态当前只保护并列待复核，实际查单/恢复和账本仍待 301/后续 FULL。不能换键重付。全部产品/账户/证明均为合成模拟，无真实银行连接或签名。
- MVP-501 要求各核心模块覆盖门仍待补；业务 UI 在 401–404 开发，普通 JavaScript number 无法精确覆盖 BIGINT，402 必须解决金额精度。
- 严格按任务顺序，每项 make check＋证据＋文档＋提交。已接受迁移不回改，结构演进新增修订。禁止 LibreOffice。真人研究未开展，不得伪造。

## 环境与精确命令

目录：`F:\学校活动\工行杯\钱途有界\bounded-funds`，PowerShell 使用 `.\make.cmd <target>`（GNU Make 对应同一 scripts/tasks.py）。

```powershell
Set-Location -LiteralPath 'F:\学校活动\工行杯\钱途有界\bounded-funds'
git status --short
git log -3 --oneline
.\make.cmd bootstrap
.\make.cmd dev
# 另一个终端
.\make.cmd check
```

PostgreSQL 16 Docker Compose 项目 bounded-funds，端口 54329，独立数据卷；API/Web 8000/5173。E2E 自启当前代码到 18000/15173，拒绝复用旧服务，默认系统 Edge。旧 dev 进程是否仍运行需重新核对，不能依据交接声称在线。

正式验证会生成 `.runtime/quality/<UTC run_id>/manifest.json` 与子命令日志；正式证据复制至 docs/progress/evidence。缓存、venv、node_modules、.runtime、测试报告和项目 Docker 卷保留，不做无关清理。PG 测试只创建/销毁自身 `bf_test_<32hex>` 临时库，不回滚实际演示库。沙盒内缓存/Docker/浏览器限制按已授权范围正常提权，不将权限失败视为通过。
