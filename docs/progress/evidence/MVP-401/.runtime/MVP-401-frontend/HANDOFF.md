# MVP-401 前端定向交付

已实现资金总览首页，唯一 GET `/api/v1/dashboard`。类型完全来自正式生成的 `packages/contracts/schema.d.ts`。首页只读，按钮只刷新查询；原动作、历史恢复建议与计算依据通过本页详情展开。

- 当前保护使用 `current_protected_cents_by_reason` 的今天付款前分层，91 日边界单独展示。
- 原始账面现金、目标现金/本金/合计归属与正式策略管理本金分别展示，并解释交叉归属与手工持仓排除。
- 已知零与 null 待核验区分；流动性风险保留负余量和正缺口；结果未知保留原动作与原经济效果摘要。
- 下一义务保留原到期日和逾期标记，区间上限不是实际扣款；截断列表说明完整组统计和展示条数。
- API 入口校验模拟标记、合同版本、同租户/时区、展示必要字段与递归金额安全整数。金额用 BigInt 的整数商与余数，避免大金额分位舍入。
- 加载错误可重试；刷新失败撤下旧卡片，不将旧快照冒充当前状态。

## 正式源文件

`apps/web/src/App.tsx`、`styles.css`、`api/dashboard.ts`、`features/money.ts`、`features/dashboard-labels.ts`。

单元测试：`App.test.tsx`、`api/dashboard.test.ts`、`features/money.test.ts`，HTTP 测试数据明确标注在 `src/tests/dashboard-fixture.ts`。真实服务工程测试 `tests/e2e/health.spec.ts` 已改为 dashboard 查询；尚未运行，由 root 与三条真实资金黄金场景统一运行 Edge。

## 最终实际结果

| 范围 | 实际结果 | 记录 |
|---|---|---|
| 前端类型检查 | exit 0，2.922323 s | 20261004T135001Z-typecheck-a86b9c78.json/txt |
| 前端 ESLint | exit 0，3.859194 s | 20261004T135001Z-lint-5dab6c21.json/txt |
| 三文件 Vitest | 39 passed，exit 0，runner 6.063957 s | 20261004T135005Z-unit-bdbf7098.json/txt |
| 生产构建 | 78 modules，exit 0，4.784185 s | 20261004T135008Z-bundle-b5500ee0.json/txt |

每个最终命令记录开始/结束时间、真实进程退出码、完整输出与 SHA256、前后源文件哈希。`verify_frontend_capture.py` 另核对这些记录与当前磁盘原稿；实际成功后生成 `final-verification.json`。

## 保留的原始失败

1. 首次直接类型检查：两处 optional BlockingConstraint 金额不能传入 `number | null`；修复为 `!= null` 守卫，未改变合同。工具输出转录明确标记为转录，保留在 first-typecheck-error.json/txt。
2. `pnpm exec vitest` 未找到命令；切换既有 package `test` 脚本。原进程结果保留为 20261004T134415Z-unit-9b106ce3。
3. 受限执行中 esbuild 启动子进程 EPERM；获自动审查允许后执行同一测试范围，保留 20261004T134502Z-unit-229c2cb5。
4. 首次实际 Vitest：35 passed / 1 failed。该断言的文字还包含 period，exact matcher 漏掉已存在的 20 项；改为匹配完整“按策略区间上限保护 · 2026-10”。20 条上限/逾期数量、原到期日、计算支付日、完整组金额断言全部保留。原输出为 20261004T134521Z-unit-0ea815b1。

未运行 PostgreSQL、真实金融 API/Edge验收或全量 `make check`，本交付仅前端模块验证。MVP-401 与完整版是否完成由 root 的真实集成/页面验收决定。
