# FULL-704 多目标中心：当前期联合规划与完整模型只读增量

Root集成（14:20 UTC）：实际GoalsPage两个native details惰性入口已接 FullGoalModelPanel 与 CurrentGoalAllocationPanel，展开才GET，不每个原Goal自动多读；原8资金组件+新惰性入口共9不同相关测试 PASS1.197s，`W6/goals-full704-expanded-module-20261005T134100Z-31793b74`。页面types与单文件lint PASS（133939Z-b4cb0a27 /133940Z-5af250f9）。明确只读与现存旧金融按钮保持原合同；不是浏览器、跨请求共同快照或确认实证，原缺口不关闭。

2026-10-05。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`，原需求保持 PENDING。按用户“功能优先、最后集中验收”修订推进；未改原要求、STATUS、旧失败、正式模拟记录或金融API。

原要求见 `钱途有界_完整开发计划_Codex执行版.md:1039–1048、1376–1378`：目标进度、归属、月度范围、资产、到期、延期、默认不跨目标挪用、修改影响及冲突。八层次序见原计划502–515；本批消费当前真实API，不称原完整问题的全局多期最优。

## 可运行功能及接入合同

`CurrentGoalAllocationPanel` 默认导出，props `{ goals?: readonly Pick<Goal, 'id' | 'name'>[] }`。名称仅辅助显示，真实分母始终来自 `GET /api/v1/planning/current-goal-allocation` 的 `registered_goal_count`、原 included/uncovered 名单。展示全部目标结果、八层词典序原向量、原资金池、条件新增分配、预计归属、最低短缺、延期截尾/天数下界/成本下界、完整收入碎片使用计划、原来源问题、冲突和每个反事实移除见证。

缺模型、来源不足或容量不足保持 UNKNOWN，空金额不变零。9个完整输入因容量门同时出现在未覆盖名单时，保留两组完整原件、明确重叠而不相加分母。solver输出OPTIMAL只表示已登记当前期求解范围；保护范围保持全部原365日最低储备，当前可分配池可能保守，未证明全局多期最优。未完成目标的延期下界不是实际完成日期，0日不能说明完成。旧完成史不重建。

资金仅当前实际新增、尚未归属收入；当前版本确认/生效时点限制碎片可用资格，零计划不能称无收入证据。收入碎片计划不是已执行分配，不生成行动、确认、回拨或银行授权。实际资金归属继续父任务现有 `GoalCard`/dashboard 的核验来源，本组件不会从 `projected_owned_cents` 推断已归属金额。

`FullGoalModelPanel` 默认导出，props `{ goal: Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'> }`。读取真实 `GET /api/v1/goals/{id}/full-model`，严格绑定目标、策略、原版本，显示已确认原FULL属性、原hash/证据/确认时点、当前ACTIVE/CONFIRMED报告。MODEL_MISSING 显示 UNKNOWN，不借旧配置填默认属性。目标/版本切换重建本组件上下文，旧候选不复用。

用户可使用当前原模型或输入完整JSON候选，明确点击后只调用 `POST /api/v1/goals/{id}/full-model/preview`。这是原只读RR预览，body仅 `expected_version_id` 和 `configuration`；无 accepted/reason/幂等键、无confirm。返回规范化FULL与原执行策略双hash和真实财务前后影响；原goal_saving影响不包含额外延期成本/部分满足等完整模型字段。预览不保存模型或生效。编辑候选后旧预览立即清除，原版本已变错误保留request_id、不开自动重试。

两reader只校验响应完整性、原身份/分母/精确整数金额/无授权合同，hash显示不等于客户端重算或外部资金验真。当前规划完整fragment→goal金额守恒用BigInt交叉核对，反事实witness字典保持全部goal分母，不把字典错当标量金额。服务的模拟银行投影报告与外部资金验真分开；FULL模型没有专用审计事件，按原标志显示尚未实现。

## 文件与保全

新增 `apps/web/src/api/full-goals.ts`、`api/joint-planning.ts`及各自测试；`components/FullGoalModelPanel.tsx`、`CurrentGoalAllocationPanel.tsx`及测试；`tests/full-goal-fixture.ts`和局部样式。fixture明确HTTP/组件合成输入，不是确认或求解实证。App、GoalsPage、主contracts、金融后台由父任务独占，未在本批修改。

完整修改前样式与最终源大小/SHA见 `.runtime/FULL-704-readonly-20261005T132153Z/`。原JSON response.text() 文本可查看；这是HTTP响应文本，不宣称网络原字节/长期验收档案。不会改写模型原件或跨请求缓存执行权限。

## 已运行模块检查

使用现有 scoped runner，没有扩展新的验证工具：

- `pnpm --filter @bounded-funds/web typecheck`，退出0，原manifest `docs/progress/evidence/W6/full704-readonly-types-20261005T133445Z-a3b37eba/manifest.json`。
- 9个相关TS/TSX文件ESLint，退出0，`full704-readonly-lint-20261005T133446Z-7822f1b4/manifest.json`。
- 4个reader/组件测试文件，18项不同测试全通过，Vitest3.29秒，`full704-readonly-vitest-20261005T133516Z-34ae604b/manifest.json`。证明范围仅合成HTTP reader/组件/金额及分母负例，不是PG、银行效果或浏览器。

各命令实际源前后、范围稳定性和退出码以原manifest为准。父任务先前因生成合同尚未更新的global typecheck失败原件保留，不能用本批退出0重标旧报告。

## 未覆盖及下一依赖

FullModel正式双hash确认/持久化写交互与共享待核对写门未接入，当前只读预览不算已确认模型。完整跨目标回拨、全局多期最优、实际完成日期预测及完整365日额外模型影响未实现，不造假权限或金额。来源/归属/模型分别读取，不能称共同事务快照。当前方案最多8个完整目标的真实求解；超过容量明确未知。后台真实PG和保护/确认集成由父任务运行，本批未跑PG、真实浏览器、资金链或全量。目标归属、资产配置和冲突全路径真实E2E、手机截图、最终初版及完整版验收仍待最终节点，不关闭FULL-704。
