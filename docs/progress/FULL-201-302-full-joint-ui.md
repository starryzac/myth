# Full 当前期目标规划只读前端

2026-10-05 UTC，功能优先修订下交付新严格 reader、独立只读 Panel 和两个直接测试文件。实现状态 PARTIAL，原 FULL-201/302 编号与初版/完整版验收不因本前端包而关闭。

## 接入合同

`FullCurrentGoalAllocationPanel` 默认导出，唯一 props 为 `goals: readonly Goal[] | undefined`。根任务在 GoalsPage 传入实际 `/goals` 结果；本包没有修改 GoalsPage、旧 CurrentGoalAllocationPanel、App、generated 合同或任何持久存储。`getFullCurrentGoalAllocation()` 只发 GET `/api/v1/planning/full-current-goal-allocation`，不发送 body/客户端事实/时间。Query key 为 `['full-current-goal-allocation']`，关闭 query 重试和结构共享，完整本次 JSON 文本保留在 response WeakMap 中可展开查看；它不缓存或授予任何跨请求金融授权。

复用现有 Joint 和 FullAnnual 严格 reader，实际 generated `FullJointPlanningResponse` 作为类型底座。新增字段校验实际 owner、aware as_of、版本、完整目标分母、1098 时点索引/日期/phase、全部五原保护分量、额外 Full floor、条件现金扣款和收入 origin/fragment/account/bank hash 绑定。整数分汇总使用 BigInt；拒绝不安全整数、削减原保护、缺原来源、金额与预算不一致、借用其他收入碎片及未来/已过确认窗口收入。前端展示原服务 hash，并做字段/数值交叉核对；不声称重新计算 SHA 或独立证明金融效果。

实际 Goal 列表仅为独立 GET 的身份/版本/allocated/账户/目标金额交叉核对。未提供时明确提示未核当前 Goal 行；不把两个 GET 说成同一数据库事务。当前 Goal 列表已变或缺行时隐藏旧计划并提示刷新。网络刷新失败也隐藏旧计划，不能让旧 query data 冒充本次成功。

## 可运行展示与未覆盖

保留原登记、included、uncovered 三分母，展示原 Joint 与 Full 后当前期预算、八层原词典序向量、已归属/本月已贡献/条件新增/预计归属/最低保障缺口、原收入使用计划及每日三相位五原 floor 和三个 Full 附加 floor。UNKNOWN 来源或求解器容量未知时保留 null/完整分母，不补零，也不把无计划当没有实际收入。来源未知不显示其未验证 captured owned 数值为成功事实。

所有金额仍是当前期条件规划，没有确认、执行、资金分配按钮。未来指定账户扣款、历史旧版本欠付、跨目标产权重分配、多期全局最优及银行执行继续明确未覆盖。`binding.status=VERIFIED` 只展示原服务的绑定状态，不是银行 grant。执行能力仍 NOT_IMPLEMENTED，未来收入不用于今天分配。

## 必要验证与原失败

范围为五个新增文件及其实际 TypeScript 导入依赖。全 Web 并行变动的类型错误单独保留；本包不修改他方源码或据局部结果称全仓稳定。

- 初次本地 Vitest 受限环境 esbuild 子进程 EPERM，保留 `direct-first.log`；随后允许必要本地子进程运行，未访问数据库、浏览器或银行。
- 首实际两个直接文件 2 FAIL / 39 PASS，原 runner Duration **4.88s**：合成夹具多字段共用同一 JS 对象，hash 篡改会同时改变参照；另一个 helper 默认参数把显式 undefined 改成一目标列表，误阻止容量报告。原五文件 byte/SHA 及日志保留在 `.runtime/FULL-joint-goal-ui/first-direct-type-failed-source` 与 `direct-second.log`。修为真实 JSON wire 的独立对象并显式传入未提供 Goal 列表，解析来源门和全部负例未削弱。
- 修后 41 直接 API/组件测试 PASS，Vitest Duration **6.00s**；synthetic fixtures 仅为前端工具/显示风险测试，不是 actual bank、solver 或经济指标证明。
- 局部五源 ESLint PASS。类型与 generated 合同不相容的初始宽 string/字段数组已收紧到原有限 Literal。整体 types-third/fourth/fifth RED 原件保留；fifth 剩余错误属于并行 full-products/spending-evidence-fixture，不由本包修改。
- 相同项目 strict/noUncheckedIndexedAccess/noUnused 等完整编译参数、仅五源及自动导入依赖的 `types-owned-scope.log` 实际 PASS；全 Web 最终类型检查由根在各并行前端稳定后执行。

最终冻结与最后源对应的直接检查附在 `.runtime/FULL-joint-goal-ui` 新 final-source 目录。根 GoalsPage 接入和失效 Query key 接线、真实浏览器、端到端、全量验收仍未在本子任务运行。后端实际 PG 由根单链执行，其证据独立记入 `FULL-201-302-full-joint-consumer.md`，不能用本 41 synthetic PASS 替代。

## 最终五源对应检查

最终档案 `.runtime/FULL-joint-goal-ui/final-source-20261005T1712Z/manifest.json`。最后对应五源 41 direct PASS5.93s、ESLint5 PASS；与项目 tsconfig 完全相同的 strict 编译选项（含 noUncheckedIndexedAccess/noUnused 等），五源及实际自动导入依赖类型 PASS，日志 `types-owned-scope.log`。五源前后 SHA 一致；没有声称并行整个 Web 或仓库稳定，根后续整体 types 结果独立保留。
