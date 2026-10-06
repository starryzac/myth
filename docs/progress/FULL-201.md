# FULL-201 · 365 日级资金投影

状态：年度读取功能已实施，定向纯检查与真实 PostgreSQL/API 节点已通过；原完整需求尚未关闭。

原要求见 `docs/spec/requirements-traceability.md:63` 及原完整版计划第 432—452、1212—1214 行。实施顺序依据用户功能优先修订，不改原编号和验收要求。

## 当前可运行能力

新增 `GET /api/v1/planning/annual`，只从当前模拟用户、可信服务器时钟和已验真的财务上下文读取，不接受金额、用户身份、策略权限、预测收入或日期覆盖参数。

- 保留初始日 `day=0`，提供之后 `day=1..365` 的 365 个日级检查点。因此完整计算有 **366 个日期、每日期 3 个阶段**，并非日 0—364 的另一种分母。
- 各日期保留付款前、付款后、本金到账后原计算点及日内最小余量；未来付款是已确认义务的条件投影，不是实际付款或银行回执。
- 当前可用现金、真实账单、已确认保护、目标归属和本金来源复用 `load_verified_financial_context`；当前审计链也实际验真。一次无待写入对象的只读 `REPEATABLE READ` 事务读取，服务会拒绝不满足此条件的调用。
- 本金仅在现有适配器验真了 `SIMULATED_PRINCIPAL_AVAILABILITY` 的原到账日期进入曲线。持仓没有该证明时列为 `NO_VERIFIED_RETURN_DATE`，不根据产品名或到期猜测到账。
- 目标本金变现金后继续增加同目标现金保护，不释放成普通自主资金。付款先于同日本金到账，日内风险不会被日末余额掩盖。
- 来源不足、持仓 `UNKNOWN` 或审计未验真时，365 个日期仍保留，金额与阶段为 `null`、状态 `NOT_PROVEN`。
- 原 MVP 当前执行视图仍默认 90 天，与年度条件投影分开；新响应固定 `grants_authority=false`、`future_points_are_settled_cash=false`。本接口不调用准备、授权、执行或回写。

## 文件与合同

- `apps/api/app/domain/boundary_types.py`：严格整数 `horizon_days=1..365`，默认 90。
- `apps/api/app/domain/boundary.py`：由 root 将五处固定 90/91 循环改为显式 horizon；旧 90 日算法/默认输入及历史哈希不重写。
- `apps/api/app/services/full_projection.py`：来源验真、年度曲线和保守未知值。
- `apps/api/app/api/v1/planning.py`：仅年度 GET、禁止额外查询字段。
- `apps/api/app/tests/test_full_projection.py`、`test_planning_contract.py`：直接功能与风险纯测试。
- `apps/api/app/tests/test_full_projection_api.py`：一个隔离真实 PG 节点，准备核全部物理表（含 Alembic）前后零写、原 MVP 响应、重复 GET、额外查询及篡改后拒绝给金额。该节点尚未由本代理执行。
- `main.py` 路由注册和 `dependencies.py` 的请求前只读设置已由 root 集成；本代理没有独立启动数据库。

## 已运行检查及原失败

所有以下为定向检查，不是初版或完整版全量。

- 六文件严格 mypy 通过：`docs/progress/evidence/W3/annual-projection-direct-types-20261005T120959Z-f85d963f/manifest.json`。
- 六文件 Ruff 通过：`docs/progress/evidence/W3/annual-projection-direct-static-20261005T121000Z-34f29dbc/manifest.json`。
- 新纯功能/接口和旧边界/显示首轮：137 PASS、1 FAIL，8.29 秒；原件 `docs/progress/evidence/W3/annual-projection-direct-pure-20261005T120958Z-0bd50714/manifest.json`。旧年 9999 日历溢出负例发现窄 horizon 改动使最后一天的下一午夜溢出成为原始 `OverflowError`；已报告 root 修复受控日期检查，原负例不删、原失败不改。
- root 保留原末日排他日界校验顺序修复后，只重跑两条原失败日期参数及新模块直接相关测试：29 PASS、1.92 秒，`docs/progress/evidence/W3/annual-projection-calendar-repaired-direct-pure-20261005T121324Z-189f94b8/manifest.json`。旧边界/显示其余未变且已通过的结果复用。
- 六文件格式检查通过：`docs/progress/evidence/W3/annual-projection-direct-format-20261005T121325Z-baf1b1d8/manifest.json`。
- root 统一运行双时态事实和年度读取两个真实 PG 节点，2 PASS/11.66 秒，原件 `docs/progress/evidence/W2/full-facts-and-annual-readonly-real-pg-20261005T121349Z-37119972/manifest.json`。年度节点实际验证 365 个未来日期、原 MVP 90 日结果一致、全部物理表读取零写、未知查询 422、原余额篡改后返回未知金额；隔离库测试中的篡改不作用于正式历史。
- Ruff 首次检查四个长行失败后仅作格式修复；未触及金融判断。

## 未覆盖与下一前置

1. 当前真实 PG/API 节点和日历修复后的定向重跑已通过；完整版全量、完整 12 模板和年度执行消费者验收尚未完成。
2. 未来收入没有已登记来源适配器，当前年度规划曲线明确不含预测收入；见 FULL-203。
3. 当前只复用已实现的五类策略财务语义。完整版新增的 12 模板合同、365 日自动执行消费者和产品可用时间升级需要各自后续集成，不能凭本读取接口算全系统已完成。
4. 本金将来的到期或赎回没有可靠证据时保守不计；不会自动生成退出指令或本金证明。
5. 未接真实资金接口；未运行全量、浏览器或正式实验。

## 2026-10-05 · 已确认完整保护来源接入差量

按用户功能优先修订新增独立 `GET /api/v1/planning/full-annual` 模块，交由 root 注册，不改原 `/planning/annual`、旧边界算法、旧策略配置或历史哈希。接口不接受查询覆盖字段，要求同一干净 `REPEATABLE READ` 只读事务。真实 PostgreSQL 候选已交 root，当前本差量尚未取得实际 PG 结果。

新增 `domain/full_protection_projection.py`、`services/full_protection_projection.py`、`api/v1/full_protection_projection.py` 和三份直接测试。服务实际调用原财务/银行/收入/资产敞口验真、当前审计以及 `list_full_policies` 的原版本和确认链校验。引用 `must_not_reduce_policy_ids` 时核当前确切 MVP/FULL 版本、配置摘要和确认来源；不能以原布尔值替代当前证明。只在本请求读取复用，不保存授权缓存。

- 新响应同时保留原 `original_execution_view`（90 日）和 `original_annual_projection`（365 日）完整结果；新 FULL 读取失败不会把声明来源混入旧财务 digest。新摘要单独绑定 Full 当前版本、完整原确认、引用快照、有效窗口、保守未付金额和原年度 hash。
- 已确认 `DatedExpensePolicy` 按登记的 `amount.max_cents` 预留一个窗口义务，在窗口最早日作一次条件付款，最晚日单独保留。上限是保守待付额度，**不是实际账单、已发生支出或已付金额**；普通转账不会被猜作 Full 结清证明。
- `PeriodicTransferPolicy` 以严格 exact/range 上限和原来源账户生成每月一次义务，31 日夹到实际月末，闰年二月正确保留。确认的未来承诺可在今天的规划中保护；`prepare_days_before` 仅显示准备日，不延后当前保护，不授予今天的银行权限。实际有效截止使用排他 aware 时间，最后有效自然日的到期义务不误删。
- 曲线为初始日加 365 个未来日期、每日期付款前/付款后/原本金到账后三阶段，共 1098 点。新的条件付款只扣新曲线，原到账本金继续采用原验真日期及同日阶段顺序；目标归属和原保护不转为新的自由现金。在途现金购买预留继续保守保护。
- 指定来源账户单独扣除当前目标现金归属和在途预留，并比较本 Full 周期承诺总额。聚合现金足够而来源不足时显示 `PERIODIC_SOURCE_LIQUIDITY_LIMIT`，新自主额度为 0；没有宣称已完整分配原 MVP 的未来逐账户借记，也不能据此自动转账。
- 已确认 `SeasonalReservePolicy` 只返回建议状态 `ADVICE_ONLY_NO_ADOPTED_AMOUNT`、采纳金额 null；没有实际采纳来源时不从分位数造额外保护池。
- 旧版本、过期/暂停/撤销后的既有欠付、早于当前月的周期欠付无法完整重建时返回 UNKNOWN，新数值曲线 null；原 365 日结果仍保留。档案版本不会被当当前权限。历史 Full 结清覆盖固定 false，不以空数组冒称已结清。

定向原件（均 scoped/global source stable=true）：

1. 首轮新模块及直接相关原年度纯测试 46 PASS/3.54 秒，`W3/full-protection-dated-periodic-direct-pure-20261005T152436Z-03cf5c01/manifest.json`。首轮严格 mypy 两处容器类型标注 FAIL 保留于 `W3/full-protection-five-types-20261005T152436Z-6670777e/manifest.json`；只修静态标注，原失败不改。
2. 最终六源直接纯检查 46 PASS/3.47 秒，`W3/full-protection-final-direct-pure-20261005T152903Z-0d2dbbd8/manifest.json`，包含手算窗口上限、未来确认、闰月/到期、来源账户不足、原目标/应急保护、在途预留、早于本金到账的短缺、精确引用、旧版本/历史未知、Seasonal 未采纳、来源摘要/确认/时钟和原输入不可变风险。
3. 最终六源严格 mypy、Ruff、格式均 PASS，分别 `W3/full-protection-final-six-types-20261005T152903Z-830ddc43`、`W3/full-protection-final-six-static-20261005T152903Z-d62e4180`、`W3/full-protection-final-six-format-20261005T152903Z-8e8e4fb3`。

实际 PG 候选一个节点通过真实结构化原策略声明/确认、三份 Full 声明确认，准备核原两个视图完全相同、当前确认/已知银行收款来源、三阶段保护、全物理表（含 Alembic）零写、重复 GET、未知参数和隔离余额篡改后的 UNKNOWN。没有由本代理运行数据库、浏览器或全量，工具/纯 fixture 不充当金融成功。

仍未覆盖：Full 原生结算与完整旧欠付账本、Seasonal 实际采纳金额来源、新 Full 曲线接执行/资产/恢复/多目标消费者、完整逐账户未来借记、动态 Full 目标保护升级、规划未来收入来源及最终初版/完整版验收。本编号未关闭。

### 2026-10-05 15:42 UTC · 本差量实际 PG 结果

root 注册路由并统一执行边界介入事件与本年度保护两个实际节点，2 PASS/209.43 秒、wrapper 211.987494 秒。原件 `docs/progress/evidence/W3/actual-boundary-event-audit-replay-and-full-protection-real-pg-20261005T153844Z-d006f762/manifest.json`；这是合批耗时，不归为本单节点耗时。manifest PASSED、scoped_source_stable=true、all_source_stable=false，唯一全局变化为独立建议模块、目标 UI 与生成 contracts 七个文件，具体清单保留在原 manifest。

本单年度保护节点实际通过：结构化原应急策略声明/确认和不得削减引用，真实 Full Dated/Periodic/Seasonal 确认及已知银行收款来源，原 90/365 日完整结果一致，500 分窗口上限与 12 次 100 分周期义务进入 1098 点保护，Seasonal 采纳额 null、未来收入计入 0，全部物理表前后零写、重复读取相同、未知参数 422，以及隔离库实际余额原件篡改后返回 UNKNOWN/null 且读取不修复篡改。六源 hold 已解除；原 NOT_RUN 准备记录不改，本增补限定为此真实节点。完整执行消费者、旧 Full 欠付结算、预测来源和完整版全量仍未覆盖，编号未关闭。
