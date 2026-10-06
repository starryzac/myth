# FULL-301 完整长期目标模型

状态：**已有目标的 FULL 模型服务及 HTTP 路由已实现，定向纯检查通过；真实集成及完整验收待主协调器，编号未关闭**。本批遵循功能优先排程，不声明全量验收完成。

## 实现与文件

- `apps/api/app/services/full_goals.py`：`canonical_goal_bridge`、`read_full_goal_model`、`preview_full_goal_model`、`confirm_full_goal_model`。
- `apps/api/app/api/v1/full_goals.py`：已有目标的 `GET /api/v1/goals/{goal_id}/full-model`、`POST .../full-model/preview`、`POST .../full-model/confirm`。
- `apps/api/app/tests/test_full_goals.py`：严格模型、双摘要映射、原件/归属/epoch/原版本/原回执篡改和显式确认风险。
- `apps/api/app/tests/test_full_goals_api.py`：两项待真实执行的 PostgreSQL HTTP/事务风险节点。主协调器接入路由和读取依赖，公共模型/迁移/原生命周期没有由本批重写。

## 模型与权限边界

完整配置使用 FULL-103 的严格 `LongTermGoalPolicy`，包含目标额、截止日、月 min/target/max、重要性、最低保证、允许部分完成/延期、延期成本、资产策略及跨目标引用。当前归属金额和当前有效版本只能来自原 Goal/PolicyVersion，不接受客户端补填。

预览返回规范 FULL 配置/hash，以及规范原 `goal_saving` 配置/hash。原目标额/月贡献、重要性/最低保证、部分/延期及资产引用显式映射到原对应字段；延期成本等额外字段留在 FULL 原模型。真实财务影响复用 `preview_change`，响应明示该原边界比较没有包含全部额外 FULL 规则。读取和预览强制同一干净的 REPEATABLE READ / READ ONLY 事务，不写库。

确认必须明确接受并复核两份摘要，绑定原版本、当前原 epoch、原因和原幂等键。在同一重入审计命令保护及父事务的 nested transaction 中，调用真实 `change_policy`：产生原新版本、同步原 Goal、失效原受影响动作；随后追加 `USER_CONFIRMED_POLICY / FULL_GOAL_MODEL_V1` 证据。第二步失败应回滚同次原版本/审计变化，不能留下半个确认。模型 content 保存原配置、双摘要、reviewed pair、accepted、服务器确认时间和原请求摘要；同键重放返回原版本及证据，不声称历史回执就是当前授权。FULL 前缀计入原 160 字符限制，因此客户端键上限为 150。

新模型可指向旧 FULL 模型作为 supersedes，旧 row 的内容/hash/status 不改。读取验证原 content/hash、owner、epoch、原 base 当前版本及确认回执、映射的 Goal 全配置字段、原元数据时间/有效期限、原确认来源和 `is_version_authorized`，同时检查 supersedes 来源和无环。无当前 FULL 原件返回 `MODEL_MISSING`，不从旧布尔字段猜额外模型。原策略没有当前有效权限时不能得到已授权模型。

FULL 模型证据明确 `bank_authority=false`、`dedicated_audit_event=false`；额外模型不是新的银行权限或已有专用 typed AuditEvent。跨目标回拨仍缺独立已实现确认服务，当前配置请求启用该能力明确拒绝，不借原 bool 获权。

## 已运行命令及证据

`scripts/run_scoped_check.py --task W4` 仅绑定上述四个源文件，运行新纯测试、四文件严格 mypy 和 Ruff。最终结果：

- `docs/progress/evidence/W4/full-goal-bridge-final-direct-pure-20261005T131305Z-3c94cf00/manifest.json`：29 PASS，1.75 秒。
- `docs/progress/evidence/W4/full-goal-bridge-final-direct-types-20261005T131306Z-2d17f181/manifest.json`：四文件严格 mypy PASS。
- `docs/progress/evidence/W4/full-goal-bridge-final-direct-static-20261005T131306Z-e73e434f/manifest.json`：Ruff PASS。

首次 Ruff 的导入顺序失败原件 `full-goal-bridge-direct-static-20261005T130947Z-850af7ee` 保留；修复后单独检查通过，没有改原失败状态。早期 27 项纯通过保留，最终窄保护增加为 29 项，不冒充新增金融实测。

后续原窗口窄修订：显式 `valid_from/valid_until` 均已过去时，原生命周期可以生成 EXPIRED 版本，但新 FULL 证据不能以本次 `confirmed_at` 为起点、以过去版本期限为终点。现在在实际 `_window` 计算后、原策略写入之前明确拒绝 `EXPIRED_FULL_GOAL_WINDOW / 422`。未来起点仍可明确确认，不能由此获得当前授权。修改前原源字节与 SHA 保存在 `.runtime/W4-full-goal-expiry-before-24fb2804e58d4dd384a0ad869e9cbdcd/`。

该窄节点首次结果 **29 PASS / 1 FAIL**，原件 `full-goal-bridge-expiry-direct-pure-20261005T131836Z-f4bd871d` 保留：夹具没有指定过去的 valid_from，原 `_window` 先准确返回既有 INVALID_WINDOW，而非新 guard 的码。只补齐该反例的显式过去窗口，重跑失败节点 **1 PASS / 1.74 秒**：`full-goal-bridge-explicit-past-window-pure-20261005T131933Z-14dea487`。没有重复已通过的 29 节点。当前四文件严格类型检查及改动测试静态检查通过，原件分别为 `full-goal-bridge-explicit-past-window-types-20261005T131934Z-6a998188`、`full-goal-bridge-explicit-past-window-static-20261005T131935Z-9d4f380e`。不将上述分次结果改称一次 30 项全量运行。

## 具体未覆盖项与下一前置

两项真实 PG 节点尚待主协调器实际执行；第一项检查原创建/只读预览/双摘要及 epoch 拒绝/原版本更新/同键重放/证据篡改和九张财务表原件保持，第二项检查追加模型存储失败的原子回滚。这些测试文件存在不表示已实测。

2026-10-05 真实集成补录：主协调器已实际执行上述两项，两项均 PASS；所属联合批次 `docs/progress/evidence/W3/full-goal-confirmation-and-actual-joint-planning-real-pg-20261005T132102Z-070b9fd2/manifest.json` 总结果为 **3 PASS / 1 FAIL，27.67 秒，FAILED**，不能改称整组通过。其另外失败属于联合计划正向夹具：旧收入发生在新版本确认之前，生产正确不分配；主协调器追加真实外部 payroll、保存两银行腿后，又保留一次断言读取旧字段失败，最终修正为实际 source_account_id/fragment 关联，单项 PG PASS / 12.07 秒，原件 `docs/progress/evidence/W3/actual-joint-payroll-original-shape-repaired-real-pg-20261005T132545Z-0687ce86/manifest.json`。本项两节点已经有真实确认、读取、篡改拒绝及回滚证据；仍未宣称完整 FULL-301 或全量验收通过。此前“尚待执行”段为测试交付时原状态，本补录覆盖该执行状态而保留历史文字。

本批仅扩展**已存在且真实确认的目标**，没有新的 FULL 目标首次创建界面或服务。当前月归属、原银行/收入真实性由资金读取及联合计划适配器另核，本模型不是余额证明。尚缺专用 FULL typed 审计事件、跨目标回拨确认/执行、前端双模型复核和最终初版/完整版全量验收。FULL-302 的联合规则仍有当期及保护范围限制，不能由本模型文件直接标全部目标功能已验收。
