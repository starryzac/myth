# FULL-306 月最低额与期限的明确条件预览

2026-10-06。`FUNCTIONAL_INCREMENT_IMPLEMENTED / MAIN_AND_ACTUAL_PG_PENDING`。这是原 FULL-306 的新有限消费者，保留旧 max 修复、默认求解器、原算法/哈希与原验收状态。没有金融执行、数据库迁移、真人研究或全量验收。

## 可运行接口与来源

新独立 `app.api.v1.full_goal_adjustments.router`：

- GET `/api/v1/planning/full-goal-adjustments`：同次实际 owner/clock 的完整 FullJoint 输入、旧冲突解释和当前原 FULL GoalModel。响应给真实 Goal/base MVP version、monthly min/target/max、minimum guarantee、归属、本月已贡献、deadline、原排他有效期和模型原 evidence/hash。
- POST `/api/v1/planning/full-goal-adjustments/preview`：只收 `expected_epoch_id`、`reviewed_state_hash`、最多8个 distinct Goal 的明确参数范围。每个范围有 `goal_id`、`expected_version_id` 和 discriminated `field`：`monthly_min_cents` 使用 `lower_cents/upper_cents`，`deadline` 使用 `lower_date/upper_date`。没有金额动作、bank facts、user/clock、授权、accepted 或执行成功输入。

UUIDReference/CalendarDate 可实际解析 JSON；整数分、布尔、未知字段、重复目标、范围顺序严格校验。既有 `_current` 在 clean RR/READ ONLY 中取得实际 FullJoint 和原完整保护；每调用 fresh 验真，完整目标/收入/1098保护点分母不裁剪，没有跨请求授权缓存。当前全部 GoalModel 逐一通过原 `read_full_goal_model`，身份/ref/hash/配置必须精确对应原联合输入。未知输入保留 UNKNOWN 和原分母，不填零，不用旧模型代替。

服务函数为 `read_goal_adjustments(session,user,now)`、`preview_current_goal_adjustments(session,user,body,now)`。纯数学函数 `preview_goal_adjustments(original,selection,model_identity_refs)` 只供已验真 server adapter；HTTP 不接受其财务输入。

## 数学边界与版本确认

月最低贡献在原 `_bounds` 中是软偏好，**minimum_guarantee 是不可削的硬约束**。新 min 范围仅支持明确降低；最接近原值的合法值为所选上界。原 `solve_multi_goal_allocation` 逐该候选重算八层结果，另列 `SOFT_PREFERENCE_ONLY`，不能靠它把硬冲突说成已修复。原可行且有软候选时返回 `SOFT_PREFERENCE_PREVIEW`；原仍硬冲突时返回 `NO_PERMITTED_REPAIR`，可展示明确标注的软偏好效果，硬修复候选仍为空。

期限只对当前已到期、非 partial、原 `allow_deferral=false` 的硬期限生成候选。新日期是 `max(user.lower,today_local+1,original.deadline)`，须严格大于原日期、不超 user.upper，整个期限日须被原有效窗口覆盖：下一本地午夜 `<= valid_until` 的排他端点。用原时区计算，不拿 UTC 日期代替；晚于当前期限但尚未产生当期硬冲突的目标不造修复。

旧 `_repair_goal` 要求 `allow_deferral=true`，直接调用它不能修复此类原非延期硬期限。新明确 `conditional-adjustments-v1` 只在假设副本上改 deadline，保留原 allow_deferral=false、target/minimum/owned/source/claims/全部原保护；调用原 solver，旧函数字节/default 不改。先耗尽更少 policy-count 层，再按精确 Fraction 参数偏离、原 deadline 分支的 priority-loss=0、固定候选 UUID 排序。每目标一个临界候选，最多8目标/255非空子集（含原基线最多256）。同层或更小层任何 solver UNKNOWN 都不给已证明最小性或已知候选冒成功。

实际模型预览使用原 `preview_full_goal_model`。响应的 Goal/version/epoch、FULL/base canonical 配置与两份 hash、impact owner/business clock/policy/version 再逐项绑定。只改变用户明确选择的字段；返回缺 `accepted/reason/idempotency_key` 的 confirmation bindings，`ready_to_submit_confirmation=false`。必须进入原完整模型页重新服务器预览、完整复核、双hash明确确认，才能由原生命周期创建新版本。预览不写原策略、不提供 bank permission；多版本确认非原子，每次确认后需要重读再规划。

`permission_ref` 复用旧候选类型的字段名，但此处仅是实际原模型身份；显式 `identity_refs_are_financial_permissions=false`，不把身份源 Evidence 升成授权。

## 新文件及 Root 接线

后台5源：

- `domain/full_goal_adjustments.py`：新严格范围/条件计划、旧基线/solver复用、有限子集排序。
- `services/full_goal_adjustments.py`：实际同次输入、完整原模型与原双hash preview。
- `api/v1/full_goal_adjustments.py`：两个 strict read-only 路由。
- `tests/test_full_goal_adjustments.py`：28 synthetic domain/composition/实际JSON HTTP风险。
- `tests/test_full_goal_adjustments_integration.py`：唯一 Root 实际 PG 候选，尚未运行。

Web6源为 `api/goal-adjustments.ts/.test.ts`、`components/GoalAdjustmentsPanel.tsx/.test.tsx`、`tests/goal-adjustment-fixture.ts` 和明示 `SYNTHETIC_DIRECT_TEST_ONLY` 的原 service-double JSON。没有改宿主 GoalsPage、原 FullGoalModelPanel、共享 operation/store、App 或 contracts。

Root 注册 router，并把 GET 前缀和 POST `/preview` 都纳入 RRRO；之后生成真实 OpenAPI/schema。新 Web 目前显式按真实 DTO 声明，不假称已生成 alias。

`GoalAdjustmentsPanel({ownerUserId,goals,blocked,onReviewCandidate})` 要实际 owner 与当前 Goal 列表。它不自动 GET/POST，先明确读取，再选择一个 Goal 与参数范围，最后明确只读 preview。编辑范围、切 owner 或改变任意当前 Goal version 后旧结果隐藏。`blocked` 不阻 GET，只阻新 preview/采用。完整原 JSON 留存；月 min 只显示软偏好，null 显示未知。采用只回调 `(goalId,actual_existing_preview)`，Root 将其传给原 `FullGoalModelPanel.reviewCandidate`；新组件不发送 confirm/execute。Root 的原模型页负责再次 preview、理由/checkbox、双hash确认及原键恢复。

## 实际必要检查与原失败

- `W3/selected-min-deadline-final-original-preview-binding-pure-20261006T015345Z-9b6750ec`：28 synthetic 风险 PASS，2.81s；wrapper PASSED/exit0、all/scoped stable。随后仅测试获取 monkeypatch 函数的类型表达与 PG 候选附加金融原件断言变化，运行时数学/服务源无变；不重复已过28风险。
- `W3/selected-min-deadline-final-five-python-types-20261006T015615Z-8f7b0a2f`：当前后台5源 strict mypy PASS。
- `W3/selected-min-deadline-final-five-python-ruff-20261006T015615Z-a3ce7691`：当前5源 Ruff PASS；owned formatter 已通过。
- `W3/selected-min-deadline-exact-witness-web-direct-20261006T015033Z-6d201a88`：reader22+panel5，27 PASS/3.09s；wrapper PASSED/exit0、all/scoped stable。
- `W3/selected-min-deadline-final-witness-related-types-20261006T015346Z-ce1c4246`：新5 Web 源及其真实依赖的 related strict tsc PASS。使用 `.runtime/FULL-306-adjustments/tsconfig.related.json` 继承原 tsconfig，不放宽 strict/noUnchecked/类型设置；没有重复 whole-Web 或冒全前端通过。
- `W3/selected-min-deadline-final-exact-map-web-lint-20261006T015615Z-0aa6453b`：新5 TS/TSX lint PASS。
- `W3/selected-min-deadline-single-pg-collection-20261006T015030Z-0c3be002`：唯一候选1 collected，**actual NOT_RUN**。之后该同名节点加强原 bank/income Evidence、reservation 和 Goal归属 before/after断言，当前类型/静态已验证，不假称已执行新的金融断言。

原 `first-types-416c1cf3`、`final-five-types-2d51cd74`、`web-related-types-8312c4a0`、`original-preview-binding-types-f1b78763` 均保留 FAILED；对应源保存在 `.runtime/FULL-306-adjustments/first-type-red-*`、`second-type-red-*`、`third-type-red-*`。它们为严格类型声明/fixture 空值收窄错误，不重标为 PASS。`web-direct-73c9924f` 的原20PASS/6FAIL与对应源保留在 `first-web-red-*`：新通用 `_cents` suffix 检查错误地把原 conflict.witness_amounts_cents **字典**当一个单金额。窄修新 reader，复用旧 reader 逐分项核原字典；新增字符串/小数/unsafe integer负例全部仍拒绝，未改 shared money helper，也未删除原 witness 原件或负例。HTTP error fixture 的 mock queue 也窄修为真正409，负例保留。

## 未覆盖及唯一下一实际命令

只证明当前期所选参数的条件解，不证明多期/所有未来账户扣款、新模型整个未来保护的最优放松。原365/FULL保护原件完全保留，规划确认不是银行 grant，真实资金当前仍走各原消费者重新验真。仍不支持 target/importance/minimum_guarantee、其他模板修复、min上调、期限提前、跨Goal资金移动、跨版本原子确认。Web首包每次选择一目标；后端最多8目标子集已可调用，多目标同时选择的表单尚未接。

根唯一候选 `test_full_goal_adjustments_integration.py::test_actual_selected_deadline_and_soft_min_preview_new_version_preserves_money`：生成 bf_test，原目标/FullModel真实确认、实际 new income原腿→GET与preview全physical零写→明确原双hash新版本→原银行/资产/账单/现金/income Evidence/claims/Goalowned保持→原 key/旧版本不覆盖、旧review409→软min仅偏好→原 audit VALID。现在只有收集和静态，不标实际金融/新版本链成功。Root 注册完成且当前唯一金融链退出后才运行；浏览器、真人、完整验收与 FULL-306关闭仍待。
