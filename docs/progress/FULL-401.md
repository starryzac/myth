# FULL-401 产品目录版本化

状态：PARTIAL_IMPLEMENTATION / 最终集中验收待取得，原编号未关闭。2026-10-05 14:20 UTC 功能交付优先批。

可运行能力：`GET /api/v1/catalog/products` 返回实际注册状态、不可变原件、当前来源匹配、未注册产品和容量问题；`POST /register-current` 空严格body只登记服务器现有模拟产品行；`GET /versions/{id}` 使用原规范产品JSON复现当时条款。来源未登记、未来观察、同版本来源漂移或容量超出保持 UNKNOWN，不输出可用规划产品。登记不授银行权限，不改任何旧canonical或旧产品行。

实现文件：`apps/api/app/db/catalog_models.py`、`services/product_catalog.py`、`api/v1/product_catalog.py`；根集成 main/dependencies；0010 literal SQL迁移、env注册；直接纯与PG测试。0008/0009历史迁移风险按各自实际目标revision排除显式后来表，当前全表捕获扩展为30 ORM+alembic=31物理表，未删减原23表与篡改负例。

新 ProductCatalogVersion 唯一绑定实际product_id、code/version、来源整行hash、完整maturity_rule hash、观察时钟和有效窗口。数据库触发器拒绝 UPDATE/DELETE/TRUNCATE，不可空丢失历史downgrade；旧AssetProduct的V1行为与原测试保留。新产品版本必须独立行与版本号，不能用同版本更新覆盖原件。当前规划适配器 `verified_catalog_products(Session, now)` 要求同请求 REPEATABLE READ/READ ONLY，验真失败返回空products/bindings；成功绑定 catalogue_version_id/product_record_hash/terms_digest，不跨请求缓存权限。

## 实际检查

- 16纯测试 PASS/1.62s，`evidence/W4/immutable-product-catalogue-pure-command-repaired-20261005T141350Z-0f6ead6e`。包含时间规范、完整条款/身份漂移和禁止自填目录、principal、时钟。
- 严格类型5文件与Ruff最终 PASS，`immutable-catalogue-original-types-repaired-20261005T141556Z-142ad10c` / `immutable-catalogue-original-static-repaired-20261005T141557Z-a3d26329`。首次types7错误、Ruff排序失败与原源保留 `.runtime/W4-product-catalog/types-first-red-source`，不改原FAILED。
- 3目录真实PG+2原迁移回归共5 PASS/23.06s，wrapper27.152426s；`evidence/W4/immutable-product-catalogue-and-prior-migrations-real-pg-20261005T141628Z-11c8f990`。scoped_source_stable=true；all_source_stable=false，仅独立autonomy_envelope两新测试变化。
- PG核：0009→0010所有旧29 typed表逐行相同，新表空；实际全产品注册只写新目录、重放全31物理表零写；不可变三种变更拒绝；旧来源改变后目录原件仍可读/current_source_matched=false、整目录与适配器UNKNOWN、登记409且零写；实际新增V3后旧原件逐项复现。0008/0009历史增量继续通过。
- 三次早期runner命令因重复source-prefix flag错误，WinError193且未启动子检查，不计测试结果；原source.before/空output与说明完整保存。仅格式后的shell0不能冒充首次Ruff成功。

## 未覆盖与下一依赖

旧决策、旧Action与原账本尚未绑定这些新目录版本，明确 `legacy_decisions_bound_to_this_catalogue=false`，不宣称旧run完整重放。新FULL资产规划需要将目录绑定写进其真实规划结果和未来决策原件；独立完整执行与专用目录审计事件尚未实现，`dedicated_audit_event_recorded=false`。当前支持已存在的实际模拟产品，不编造额外低风险产品数据或产品收益。

所有迁移/篡改/新版本测试在自动清理的 bf_test 隔离库；正式库未迁移、未重置，11:26正式保全仅原时点证据。下一前置：FULL402—404从验真目录读取实际产品与确认版本，最终补完整决策引用/旧run可复现证据和全量验收。
