# FULL-306 最小目标修复建议

状态：**明确登记候选集合的纯修复排序已实现；自动候选生成、真实确认和完整验收待补，编号未关闭**。

## 完成内容与合同

`apps/api/app/domain/multi_goal_allocation.py::propose_minimal_goal_repairs` 对最多 64 个显式目标修复候选，按改变策略数最少、精确有理数参数偏离最小、重要性加权的最低贡献下降最小排序。每个候选绑定当前有效版本及原权限证据；只有该目标 `negotiable_fields` 登记的月 min、月 max 或截止日期可提出变化。截止日仅在原允许延期时延长，不能超过原有效期。

原现金、义务、生活、应急、既有归属、最低保证、目标额与跨目标权限不能改变。对每层改动策略数穷尽登记候选，再考虑下一层；结果显示具体变化和所有不受影响目标。偏离用 `Fraction` 计算，拒绝用浮点权重偷换顺序。修复后的联合分配必须已证 OPTIMAL，否则 UNKNOWN。

结果 `PROPOSAL` 仅包含假设输入/分配，保留 `original_input_hash`，`grants_authority=false`，`requires_new_version_confirmation=true`。不会修改旧配置、旧哈希或已执行历史。SQL 确认必须经原策略变更流程形成并复核新版本后才能参与动作。

## 验证与原件

直接测试与 FULL-302/305 同文件、同源范围。41 PASS / 0.76 秒原件为 `docs/progress/evidence/W4/multi-goal-allocation-direct-pure-20261005T125326Z-25f2a0f5/manifest.json`；严格 mypy、Ruff 同批 PASS。验证原 max 与保证冲突的可行修复、同偏离时优先级损失排序、未影响目标保留、原输入字节语义不变、过期版本/未登记字段拒绝、硬保护不足不能修复。这里只是合成纯输入能力测试，未声明真实银行动作或用户确认成功。

## 限制与下一前置

目前最小性只针对调用方提供且合法登记的有限候选集合，尚缺从真实约束自动生成修复候选及只读/确认 API。允许延期时当前到期完成要求已不作为硬约束，延期候选未宣称能修复不允许延期的硬完成冲突；后者应如实无可调整候选。需接原政策 preview、两份配置复核、新版本确认和真实 SQL 隔离证明。完整十二模板修复及最终全量验收尚未完成。

## 2026-10-06 用户主动选择的新版本修复预览

本节登记 Root 明确授权的 planning proposal-only 差量：实际联合输入的 `negotiable_fields=[]` 不冒充已有提高银行月 max 的许可。GET 保留真实 `NO_PERMITTED_REPAIR`。新增 `POST /api/v1/planning/full-goal-repairs/preview`，仅用户主动提交当前 epoch、review_state_hash、1—8 个精确目标/版本及月 max 调整范围；金额严格整数，bool/float/字符串、来源/结果/权限/时钟注入、extra 字段、重复目标、query 均拒绝。

新的有限搜索输入与原实际输入分开保存摘要。搜索只为规划登记用户选中的月 max，证据引用仅标识当前版本，不把它解释成更大执行权限。每个真实当前 cap 冲突生成范围内最小临界整数：不低于月 target、用户范围下界和“当月原已贡献 + 本次原保证/不准延期到期所必需新额”；超出用户范围无候选。较大 cap 不能改善该下界资金流的可行性，因此每目标一个临界候选足以覆盖本包 max-only 范围；复用原求解器按最少改动策略数、精确有理数偏离、优先级损失排序。不是枚举每分钱，也不放松保证、目标额、期限或任何硬保护。

成功候选调用实际已有 `preview_full_goal_model`，返回真实原 FULL 配置/hash、当前版本、旧/新 max、完整实际双 hash preview 及原 confirmation endpoint/bindings。bindings 不填 accepted/reason/key；必须由用户明确输入后经原 FullGoal confirm 追加新版本。预览零写、不创建行动，不改变当前执行上限。多目标没有原子多版本确认能力，每次确认后必须重新读取当前状态，不能沿用其他旧候选。

### 实施与定向证据

生产三源、四个直接测试文件与 FULL-305 本次差量相同。手算 5 分 max、当月已贡献 3 分、原保证尚缺 8 分，最小候选 11 分；独立小域逐整数可行性比较验证，原输入、硬保护与未受影响目标保持。覆盖两个必需版本、范围不足、负硬池、无来源、stale 版本/epoch/review、原模型 hash、未来生效/过期/完成目标及真实 FastAPI JSON DTO。

本次检查明细与失败原件见 FULL-305：首轮 64 PASS / 2 FAIL 保留，窄修错误语义后受影响 11 + 新增 3 节点 14 PASS；严格 mypy 7 文件、Ruff 均 PASS、scope/global 均稳定。不得把合成预览的结果当实际银行/用户确认成功。新的唯一真实 PG 候选尚未运行。

### 限制与下一前置

月 min、deadline、其他模板自动生成/修复及多期全局最小性未接。所谓“最小”限定于真实当前期财务输入和用户明确所选 max 范围；既有资金来源时刻/权限、完整原 365/FULL 保护及所有目标分母不变。真来源不完整、无当前模型、超范围或基准硬池不足时不给假修复。Root 注册 GET/POST router、POST RRRO 并运行唯一隔离 PG 后，才能登记实际新版本确认及全物理表零写实证；FULL-306 和最终全量仍未关闭。

## 2026-10-06 用户选择修复范围的可运行面板

新 `GoalConflictRepairPanel` 初始不选择任何目标、不填写金额。用户主动选择已核版本并输入整数分的月 max 上下界后，调用实际只读 POST preview；不得提交 float/bool、extra 字段、错误版本或旧 review_state_hash。页面展示服务实际生成的有限候选、不受影响目标、精确偏离分数、优先级损失、原配置/hash、真实 FULL/base 双 hash 及完整原响应，注明仅当前期 max 范围内的规划候选。

新 reader 逐项核对选择范围、实际候选集、旧 max、完整未受影响目标分母、假设分配身份、实际 FullGoal preview 和原确认 endpoint/bindings。bindings 不能出现 accepted/reason/idempotency_key，且原 `ready_to_submit=false / current_permission_changed=false / grants_authority=false` 必须保持。UI 无自动确认或执行按钮；输出用于现有 FullGoal 工作区重新读取、双 hash 复核并明确确认的绑定，尚未向该工作区注入候选。现有共享 `FullGoalModelPanel` 及原持久 pending/by-key 恢复链保留，后续接线由 Root 负责，每次实际新版本确认后仍须重新规划。

面板复用原 FullGoal 写门观察 hook 和瞬时 write-flight，跨族阻挡由宿主 mutationBlocked 传入；预览本身不新增持久金融命令族。编辑、版本变化、网络失败、GET 失败或新 UNKNOWN 原件不允许继续展示旧候选为当前成功。未取得实际来源、当前版本、全分母或双 hash 时明确不足，不填假修复。

本次直接验证与 FULL-305 同批：reader 22 + panel 11 = 33 PASS，定向 ESLint PASS；原件和 source 稳定字段详见 FULL-305。合成 HTTP fixture 仅为直接交互测试。真实 schema 已由 Root 生成，整体 Web types、GoalsPage 接线、实际隔离 PG 及浏览器本包尚未验证；不视为确认/执行成功，不关闭 FULL-306。

首次统一 types 的合成 JSON 八元 tuple 类型错误保留在 `W6/goal-reallocation-ui-generated-types-20261005T191802Z-540b5eb6` FAILED；修订仅 fixture 类型桥接，生产解析/交互不变，原 FINAL 归档不覆盖。定向 fixture lint PASS，新 whole types 等 Root 唯一执行；具体 TYPE_ONLY_DIFF 与原件路径见 FULL-305。

## 2026-10-06 修复候选进入原完整目标确认流程

本次显式功能差量替代上一节“尚未向工作区注入候选”的限制，原文字和证据保留。每个实际修复结果只在独立当前 Goal 的 id、policy_id、policy_version_id 与原 preview 一致时，提供用户手动展开原 `FullGoalModelPanel` 的入口；未提供当前 Goal 原件时仍可读候选，不能打开确认。共享 GoalsPage 的既有 props 无需扩展。

`FullGoalModelPanel` 新可选 `reviewCandidate` 接收实际 reader 已核的 `FullGoalPreview`，再次检查目标、策略、版本与当前 GET 的 epoch。初始不填候选、不发 POST、不勾确认。用户点击“使用修复候选并重新只读预览”才采用完整配置并调用原服务器 preview；显示新返回的完整规范化配置和两份 hash，旧修复预览的影响/hash 不直接用于确认。新 preview 的 epoch 与当前 model 不一致时停止，必须重新读取。原明确理由、accepted、原 body/hash/key 持久保存及独立 by-key 原件核对链保持；收到 HTTP 成功或响应丢失都不清 pending，不把历史回执当当前授权。

编辑范围、刷新 GET、当前目标/版本或传入候选变化会停止沿用旧候选与复核；预览异步返回在刷新后的旧 generation 不再展示。确认/原键核对之后同时失效当前冲突查询，下一次需重新读取。没有自动采用、确认或资金执行，不放松硬保护，不写历史 hash。多目标仍逐目标确认，每次新版本后重新规划，不提供原子多目标确认。

本次直接组件测试共 **31 PASS**（FullGoalModelPanel 18、GoalConflictRepairPanel 13），原日志原生 5.87 秒，wrapper 6.949867 秒；四源 ESLint PASS，wrapper 2.784124 秒，两者 scope/global 均稳定、source_changes=[]。原件：

- `docs/progress/evidence/W3/repair-candidate-original-confirmation-direct-unit-native-20261005T195421Z-a6422424/manifest.json`
- `docs/progress/evidence/W3/repair-candidate-original-confirmation-static-repaired-20261005T195415Z-89c20a00/manifest.json`

首轮 Vitest 在 esbuild 启动前遇到 `spawn EPERM`，测试未执行；首轮 lint 将 `useRepairCandidate` 事件处理函数误识别为 Hook，窄修函数名后通过。两份 FAILED manifest 与对应四源原字节保留于 `.runtime/W3-repair-candidate-first-red-20261005T195354Z-971f2f55`，不得覆写为成功。直接测试使用明示合成 HTTP fixture；本包真实数据库确认、浏览器操作和最终全量仍未运行，whole Web types 等 Root 唯一终态检查，FULL-306 未关闭。


## 2026-10-06 04:30 Root增量

六实际PG批终态PASS/1600.41s，wrapper1611.67274s；见 `evidence/W3/actual-release-sources-consent-repair-and-current-maturity-20261005T195334Z-810817b1`，范围来源稳定/全源独立变化。仅相应节点实证，非完整版本验收。具体旧缺口与原失败保留。
