# FULL-402 资产可行性过滤

状态：生产过滤/只读服务/路由候选已实现，直接纯与静态检查通过；真实 PG 候选待主协调器执行，FULL 项未关闭。原依据为 `docs/spec/requirements-traceability.md:77`、原完整版 F:1276–1278 及资产章节 10.1–10.2。按功能优先显式执行修订推进，不变更原编号或验收要求。

## 可运行能力

新增 `app.domain.full_asset_allocation.plan_full_assets(FullAssetPlanningInput)`，每个实际产品 id / code / version / terms_digest 单独保留过滤结果及全部独立拒绝理由。检查当前已知/生效/最新版本、允许类别、本金波动、风险上限、申购许可、锁定/赎回上限、完整保证本金条款、原收益/结算条款一致性、起投、实际额度、目标/用款日期和零损失规划赎回确认。本金波动始终拒绝进入自动候选。

旧 `FIXED_DEPOSIT` 仅在原 fixed-principal-return-v1 条款中的严格整数 term_days 为 7/30/90、锁定不超过期限且完整到期规则相符时，映射为对应 FULL 规划类别。实际原目录版本和 hash 不变。原不完整 v1 maturity_rule 仍被拒绝，不从产品名称推断条款。LOW_RISK_TERM 只接受完整、保证本金、零费用、无波动的固定到期条款；目录没有该真实产品时，不生成一个产品来补齐类别。

新增 `app.services.full_asset_allocation.read_full_asset_allocation` 与 `GET /api/v1/full-policies/{policy_id}/asset-allocation`。服务要求同一 clean RR/RO Session，读取原 `read_full_policy` 的实际确认、版本及引用验证，原已验证银行/收入/完整 exposure、原 365 日财务曲线和当前完整审计；目录只用 `product_catalog.verified_catalog_products` 的不可变登记原件及当前源 hash 完整重核。任一缺登记、源漂移、原件或容量问题均返回 UNKNOWN/空 allocation，不 fallback 到可变旧行。

所有约束字段显式命名 planning_constraints / planning_*，只收紧有限候选域，不能授予新权限。响应始终 `planning_only=true`、`bank_authority=false`、`execution_support=NOT_IMPLEMENTED`，每个候选 `bank_auto_eligible=false`；当前完整声明不是原 V1 执行器的银行授权。

## 文件与设计边界

本批新增共享于 FULL-403/404 的 domain/service/API 和三个直接测试文件：

| 文件 | SHA256 |
| --- | --- |
| `apps/api/app/domain/full_asset_allocation.py` | `2d1c58b27263ee91fd887507506113593a4e0ef93c636d1187369d00c2fc1a32` |
| `apps/api/app/services/full_asset_allocation.py` | `de0b9e0877905f0151e593df2ca415c5c77484510b83403052a0e6b4669edf05` |
| `apps/api/app/api/v1/full_asset_allocation.py` | `26c23c2ae5a4b9ca60f0c11d3ae3678b9e4138ee321c66d53870035eb7d3cdd1` |
| `apps/api/app/tests/test_full_asset_allocation.py` | `007a668129730b71746519e18b1ab6f11387127a2ecbf82ac2f74e7972f80b53` |
| `apps/api/app/tests/test_full_asset_allocation_api.py` | `f2e98d01c78966e006f292f12c0170190cb0e4c98cbaf1037a042b688ca8d049` |
| `apps/api/app/tests/test_full_asset_allocation_contract.py` | `bb7b211a5b48589d1fa9b5599233b935cd3ede77b320cf6cfe4f3a59e942722c` |

复用实际原产品条款 DTO、原 exposure 预留检查、原 `_project` 和 `compute_boundary`，没有修改原单产品选择/执行、旧资金记录、旧策略/产品 hash、共享 ORM、migration 或正式历史。FULL-401 新不可变目录由主协调器单独实现；本批只是其只读消费者。

## 已执行检查

现有 `run_scoped_check.py --task W4` 记录真实命令/源 SHA/原日志，仅运行本批直接纯/严格类型/Ruff/格式检查，不运行 PG、浏览器或全量：

- 首整批 **16 PASS / 2 FAIL，11.50 秒**，原件 `full-asset-domain-direct-pure-20261005T135911Z-d9ab5f00` 保留。一个夹具错把用款日午夜前的 T1  earning 88 天写成 89 天；另一个三批精确搜索在较弱上界下实际达到容量 UNKNOWN。修正原数学期望，添加有效的逐批 rounded-yield 上界及必要收益阈值下界，不能将旧 UNKNOWN 改为成功。
- 整个直接模块 **18 PASS / 1.35 秒**：`docs/progress/evidence/W4/full-asset-integer-bound-direct-pure-20261005T140123Z-2f70dc9c/manifest.json`，严格两文件类型与 Ruff 分别为 `...types-20261005T140124Z-323de777`、`...static-20261005T140125Z-192769b4`。
- 新未来硬保护点的一个节点首次因夹具未按原配置默认值 canonicalize 而 RED；原件 `full-asset-future-floor-direct-pure-20261005T140347Z-24ec3793` 保留。只修原配置摘要夹具，单节点 **1 PASS / 0.81 秒**：`full-asset-future-floor-original-canonical-pure-20261005T140410Z-a5a69b51`。
- 新 API 查询九负例/约束与短保护窗口/旧 exposure 直接节点合计 **10 PASS / 1.82 秒**：`full-asset-new-read-contract-pure-20261005T141454Z-915b1c3f`。这是分次 18+1+10，不改称一次 29 项运行。
- 六文件严格 mypy / Ruff / 格式 PASS：`full-asset-actual-dto-types-20261005T141601Z-b41a053a`、`full-asset-actual-dto-static-20261005T141601Z-ed58eeeb`、`full-asset-final-format-20261005T141602Z-7868e7ae`。首次 strict 类型中 Optional exit_plan、变量名复用，以及后续实际 UUID DTO / list 不变性 / fixture NOW 导入问题均有原 RED 记录保留，未改失败标签。最后仅把 PG 夹具目录登记改为真实原 POST，改动测试严格类型/Ruff PASS：`full-asset-native-catalogue-post-types-20261005T141803Z-a7fea41e`、`...static-20261005T141804Z-b3dbd355`。

## 未覆盖及下一前置

主协调器须完成目录迁移/真实登记、注册新路由及前置 RRRO，再实际运行 `test_full_asset_allocation_api.py` 两节点。候选明确通过真实 `POST /api/v1/catalog/products/register-current {}` 登记服务器原产品，不自填条款；检查真实完整确认、原 payroll 两银行腿、组合及收紧约束、原物理全表零写、目录漂移拒绝，以及原目标动作真实归属后才读梯度。文件存在不代表已运行。

当前实际目录仅 T0/T1/旧三十天定存及其版本，没有七类全部真实产品；响应逐类给 unavailable_asset_classes。新 FULL 财务预测/dated expense/季节储备尚未集成到原边界的能力不能由本批曲线代替；protection_scope 明确仅 ORIGINAL_VERIFIED_365_DAY_CURVE。还缺完整执行消费者、前端全部产品体验和最终初版/完整版验收。FULL-402 未关闭。


## 2026-10-05 实际只读集成补证（原 NOT_RUN 记录保留）

主协调器完成并读取原 `docs/progress/evidence/W4/actual-full-assets-catalogue-and-goal-ladder-real-pg-20261005T142956Z-eb772045/manifest.json` 与日志：`test_full_asset_allocation_api.py` **2 PASS / 93.73 秒**，wrapper 96.72704 秒、exit=0、scoped_source_stable=true、all_source_stable=true、source_changes=[]。实际分母为两个隔离 PG 风险节点：完整目录登记/实际 payroll/当前组合及收紧/全物理表零写/目录漂移 UNKNOWN；真实目标动作产生归属后读取定存批次、短截止拒绝及全物理表零写。新路由 RRRO 已接线并生成合同。

实际服务器只有三十天单期限，结果仅 SINGLE_MATURITY_AVAILABLE；七天/九十天/LOW_RISK_TERM 未提供真实目录源，仍列为 unavailable。不是动态执行或真实多期限梯度证明，仍缺执行消费者、完整前端/真实全产品范围及最终完整版全量。原编号保持未关闭；前文候选 NOT_RUN 属交付时状态，不改其原件。

