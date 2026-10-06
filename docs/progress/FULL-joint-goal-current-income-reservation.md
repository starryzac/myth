# 联合目标固定子动作：当前收入来源桥

2026-10-06。本包仅新增收入来源校验服务与直接风险测试，不改原联合目标九源、原 MVP successor 规则、Action.request、银行命令、金额、IncomeUse、历史哈希或 ASSIGNED 账本。

## 可运行接口

`app.services.full_joint_goal_income_reservation.validate_current_joint_income_reservation(session, action, proof, now) -> None`

调用者持有原用户锁，提供**本次调用**由实际独立 RR/RO 读取后派生的 `FullDynamicGoalProof`。返回仅表示本动作的当前收入来源连续性已通过这层检查；不授予权限、不预留资金、不验证新的银行付款成功。失败抛出原 `PolicyLifecycleError`，当前事务按原流程回滚。

Root 仅在原 `reserve_income_for_action` 的新 Joint durable binding 分支、且该动作尚无 IncomeReservation 时调用；显式 `_joint_current_proof` 必须与真实 Joint 关联同时存在。已有预留仍走原精确重放、完整银行位置、fragment/cash 守恒检查，不能借此入口恢复或释放其他动作的 claim。旧 MVP 默认 successor 路径不由此服务替换。

## 原件与金额边界

服务复用实际原 parent 完整 frozen math/DecisionTrace/audit 验真、固定 child/bank command 绑定、当前 whole signed USER consent 及 `_ordered_current`。前序 child 必须有原 SETTLED 银行操作与严格原回执；本笔不能已有银行操作；后序不得先行提交。当前 OPEN epoch、原计划有效窗口、实际 Full policy 最新版本、原 Goal/MVP 版本有效期与 Model 原证据仍逐项核对。

当前 `read_income_state` 保留生产底座对全额来源、原银行位置、实际 Goal ownership、全部账户/事务的核验。新服务另核每份原 Evidence 的 owner/status/type/hash/known time，原 model 和 source refs，与提供 proof 的完整输入精确一致，重新运行确定性 dynamic proof。它不在写 Session 调用要求 RR/RO 的规划读取函数，不创建新的证据或 snapshot。

从当前 VALID native `new-funds-ledger-v2` 原 Evidence 沿 `supersedes_id` 回溯到 Action.request 原 income ID/hash，最多 10,000 条；完整原时钟必须单调已知，历史节点 SUPERSEDED，缺失/循环/越限/不一致均拒绝。原协议、simulation、complete 字段必须显式存在；V1 不在这里猜测重建。原 JSON 的 Z/+00:00 表示和哈希不改，只按原 typed datetime 解释同一时刻。

本笔固定 uses 必须同时来自原准备账本与当前账本的同 origin×fragment×account，金额不得超当前 available。原 scope 与原 origin 银行身份保留；逐前序 COMMITTED ALLOCATE_GOAL 记录必须与完整固定 uses 一致，且新消费量真实留在 ASSIGNED 增量中。先前已 COMMITTED 的原分母不重复累计。任何本笔/后序 claim 拒绝；其他 claim 保留并继续扣减 available。不能 ASSIGNED→AVAILABLE，不能以释放或新收入增加旧 fragment 的本笔可用额度；这种当前 replenishment 本包保守拒绝。

纯手算风险：原 500,000 分来源，第一笔 50,000 分真正由原 `reserve_income`/`commit_income` 数学转入 ASSIGNED；第二笔固定 100,000 分只预留当前 available，得到 ASSIGNED=50,000、RESERVED=100,000、AVAILABLE=350,000，原请求和原命令未改。该夹具是合成模块检查，不是实际付款证据。

## 检查与原失败

新增文件：`apps/api/app/services/full_joint_goal_income_reservation.py`、`apps/api/app/tests/test_full_joint_goal_income_reservation.py`。

第一轮原件保留于 `.runtime/FULL-joint-goal-income-reservation/first-direct-and-type-red-20261006T0528Z/`，没有重标结果：

- `W3/joint-current-income-pure-20261006T052639Z-ab43cb4d`：13 PASS / 28 FAIL，5.00s；服务真实拒绝时，测试错把错误 code 当异常中文 message 匹配。
- `W3/joint-current-income-types-20261006T052640Z-1bc2fc10`：FAILED，一处原 MVP nullable valid_from 比较缺门；修为显式拒绝 None。
- `W3/joint-current-income-static-20261006T052640Z-090cb3ea`：PASSED。

后续原件如下；不把测试输出字符串当金融证明：

- `W3/joint-current-income-corrected-pure-20261006T052738Z-6f4f3c6c`：40 PASS / 1 FAIL，4.16s，原 FAILED 保留。仅 V1 负例的一处同类错误代码断言未被前次转换覆盖（含数字 V2），原测试另归档于 `.runtime/FULL-joint-goal-income-reservation/second-direct-single-assertion-red-20261006T0530Z/`。
- `W3/joint-current-income-v1-negative-20261006T052914Z-e945f921`：仅修正该断言后重跑原失败节点，1 PASS / 40 deselected，2.64s；未声称新整批 41 PASS，未删除原负例。
- `W3/joint-current-income-final-types-20261006T052914Z-143e1e9e`：strict mypy 两源 PASSED。
- `W3/joint-current-income-final-static-20261006T052914Z-b7536538`：两源 Ruff PASSED。

以上最终三检查原 manifest 的 scoped_source_stable/all_source_stable 均为 true，exit_code=0。首次纯检查 global=false 的无关并行源变化仍留在原 manifest；不改成全仓冻结。此前第二轮 strict/static 通过也保留原件，不需要重复全套联合目标测试。

## 未覆盖与前置

尚未运行实际 PG、浏览器、全量验收。直接测试只覆盖整数 continuity/前后分母、原 source/lineage/status/时间、固定顺序和请求篡改拒绝，不替代数据库真实 full parent、signed USER、前序银行/回执或同锁整条执行链实测。实际必要节点是现有 `test_actual_joint_different_amount_signed_whole_consent_fixed_key_unknown_no_advance`；Root 安装 0015、Shared Joint guard/trace 分支与 typed `_joint_current_proof` 后串行运行，验证第二笔旧请求经真实第一笔账本变更仍可使用原固定金额/uses，UNKNOWN 保留并挡后笔。

新收入追加、source scope 变化、旧 fragment replenishment、跨 epoch、旧 V1 缺 native 原件、超过 lineage/whole trace 容量均不会被本桥自动升级支持。whole parent 无预留；单笔资金仍只由原 reservation 与实际 bank 首次受理管道产生。

完整两 Goal **合成** fixture 的 plan JSON=8,122,221 bytes，inputs=5,848,971 bytes，仅容量样本。原 source refs/hash 一致性记录在 `.runtime/FULL-joint-goal-income-reservation/synthetic-fixture-json-size.json`。Root 新 Joint 整体 Trace 16MiB、inputs/outcome 各 10MiB 和 nodes≤250,000 的限制独立保留；超限应拒绝，不能裁掉 1098 点/目标/原证据。无实际大规模金融容量结论。
