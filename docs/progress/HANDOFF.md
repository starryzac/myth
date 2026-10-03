# 开发交接（持续更新）

目标：依据两份计划先完成初版，再推进完整版，直到功能、实验和交付逐项验证。目标 active，无 token budget；不得将工程骨架或单模块视为全部完成。

## 当前里程碑与精确下一步

- 已完成 10/92 项：MVP-001/002/003/101/102/103/104/105/201/202。最小未完成 MVP-203；其后 204/205/301…，最后才推进 67 项 FULL。
- MVP-202 最终 check `20261003T193641Z-758fbfbb` exit 0：556 后端、4 前端、1 Edge E2E；95 个源码/配置摘要逐个重算一致。整体语句覆盖率 95%，纯边界 96%、DTO 98%、来源服务 87%。原始日志、manifest 与定向红绿记录在 `docs/progress/evidence/MVP-202-*`。
- 所有 202 分工已冻结交还 root，没有仍在运行的测试 session。95 个文件摘要均在提交前验证；最新提交号以 Git log 为准。
- 实际库为迁移 0002、种子 mvp-202-v3。两次 seed run `20261003T193225Z-b2775ed0` 与 `20261003T193310Z-33c5ee8f` 业务 JSON 完全相同，SHA256 `701a80942c4299e1c2274a89b907859f6d7f6d22621a9e2b2be636487e3f6a66`。129 流水、253 证据、5 账户、3 账单/产品/持仓；现金 3462400 分、本金 500000 分、未付账单 145000 分。v1/v2 证据保留为历史，不能当作当前校验和。
- 正式演示库的只读 boundary 为 READY、3157400 分、91 日/273 检查点，16 表前后摘要相同；证据 `MVP-202-demo-boundary.json`。最终 types run `20261003T193139Z-54f7f897` 已包含 calculation_notes。
- MVP-203 尚未开始实现，三位代理已完成只读预审。建议单目标 allocation-preview，其他目标继续通过完整 202 边界保护；FULL 多目标联合优化不提前实现。

## MVP-203 预审结论（需 ADR 冻结）

SEM-07 尚 OPEN。新增来源只接受当前适用策略确认/生效之后、实际到账 CREDIT、BANK_CONFIRMED 且银行 economic_role=INCOME；不可由可编辑 category 推断。旧余额、OPENING、内部划转、退款、本金回款、未来预测均不生成新资格。来源必须有完整、同一快照的未消费、已消费及 pending/UNKNOWN 预留证明，不能用工资金额减目标贡献而忽略普通消费。逐来源账户核对，不拿其他账户旧余额补收入资格。未用 lot 可跨月保留，月范围重置不重建来源。

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
