# 当前保护的 goal_cash 文案候选

当前状态：已在 root 结束真实 Edge 采样冻结后正式应用。Typecheck、仅 App.test.tsx（13 passed）和 lint 实际 exit 0，日志见 20261004T151311Z-typecheck、20261004T151320Z-app-test、20261004T151312Z-lint 的 json/txt。每批源文件前后哈希相同。本次未运行 money/API/bank/DB/Edge 测试，真实新文案页面验证由 root 负责。App.tsx 与 App.test.tsx 此后冻结。

以下是冻结期间的原候选说明，candidate.json 保留当时未应用状态及原始源哈希，未重写旧证据。

确认问题：`App.tsx` 当前把 goal_cash 标为“目标已归属现金”，但该 reason 包含未归属的 GOAL 账户现金。

直接源码依据：

- `apps/api/app/domain/boundary.py:495–497`：goal_cash = 所有目标 cash_owned_cents 之和 + 所有 unassigned_goal_cash.amount_cents 之和。未来已确认目标本金返还后还能转换为目标现金保护；当前卡片只用今天 BEFORE_PAYMENT。
- `apps/api/app/services/boundary.py:705–716`：GOAL 账户余额减去已归属现金的剩余部分，作为 UnassignedGoalCash，并采用真实账户证据。没有 Goal 实体时，余额仍受保护。
- `apps/api/app/domain/boundary_details_types.py:149–179`：cash_owned / allocated 与 unassigned_goal_cash 独立分项。`services/dashboard.py` 原样输出这组不同语义。

最小修改：只将 protectionReasons 的一个标签改为 **“目标现金保护（含待归属）”**。GoalOwnership 卡片的“目标现金”“待归属的目标账户现金”不变。金额、hash、DTO和计算无修改。

`label-only.patch` 是依实际冻结源文件生成的1行替换；`related-test.patch` 是可选的针对性HTTP单元回归候选，构造 owned=0 / unassigned=160000 的明确单元fixture，检查保护卡片不把¥1,600.00称为已归属，归属卡片仍分列已归属零与待归属金额。它不代表实际银行样本或已运行结果，未改既有断言。`candidate.json` 绑定原稿/候选patch SHA256并确认生成时生产与测试原稿未改。

完成当前冻结采样后若 root 采用修改，定向验证只需 frontend typecheck、App.test.tsx 与 lint；最终页面构建/采样记录绑定新的文案源哈希。金额util、后端资金计算与数据库未改，无需为单行文案重跑金融集成或全量验收。旧采样仍应保留其实际旧标签和源绑定，不能把它重标为新文案截图。
