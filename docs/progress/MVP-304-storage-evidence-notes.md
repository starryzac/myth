# MVP-304 storage 验收记录

记录时间：2026-10-04。范围为 models、0006 迁移、audit_chain、audit_guard、demo_seed 及本组测试；不替代 root 的完整 check、正式迁移或整体验收。

## 最终结果

- [本组 6 文件定向集成](evidence/MVP-304-storage-owner-final.txt)：真实 PostgreSQL **52 passed / 122.58s**。所有数据库均由 fixture 创建为随机 `bf_test_<32hex>`；没有迁移或重置正式数据库。
- [核验顺序补充回归](evidence/MVP-304-storage-verification-order-green.txt)：**3 passed / 10.67s**，覆盖真实 genesis、当前列篡改后的幂等重试、已知原件删除/合法生命周期变更/封存后重置。
- [Ruff](evidence/MVP-304-storage-ruff-final.txt)：11 个 owner 文件通过；[mypy](evidence/MVP-304-storage-mypy-final.txt)：10 个源文件通过。

永久保留事件与预留 User；新增 epoch、不可变 canonical-text 原件版本。事件、原件和 epoch 的普通 UPDATE/DELETE/TRUNCATE 由数据库保护，原件引用按 tenant/epoch/kind/id/hash 区分。非 owner 测试真实创建随机 NOLOGIN role 并 SET ROLE，不声称正式应用已切换数据库角色；管理员 ALTER/DROP/禁用 trigger 仍是明确边界，其后读取必须发现损坏。

重置保留完整旧副本、封口和旧事件字节，在同一事务内归档、重建业务图并开启新轮。业务 seed 仍为 `mvp-301-v6`，`seed-summary-v2` 只统计显式 19 张业务表；成功 reset 比较业务数据，失败比较全部表，不能把应保留的审计增长当作业务不确定性。固定 reset key 重试返回原 summary，不新增 epoch；理由变化拒绝。晚于旧轮封口的银行 opening 失败仍全事务回滚。

命令 shared gate 使用专用 NullPool 连接，跨三段事务保持锁，不占业务连接池；reset 使用相同 key 的 exclusive gate。真实单业务连接池、三次提交、嵌套 guard、另一用户及异常解锁已验证。完整 execute-action/reset 交错由 hook owner 的独立测试覆盖。

读取先聚合数量/字节预算，超限返回 INCOMPLETE，不装载全部文本。无 head、旧历史缺口、未知版本分别显式返回；旧业务没有 AuditEvent 行也不能冒称完整审计。只有当前链 VALID 且原件 VALID 或明确 LEGACY_UNAUDITED、没有错误时允许维护追加/重试/reset。OPEN 中已捕获的不可变原件缺失判完整性错误；SEALED 使用旧副本，不依赖已重置的 live UUID。

## RED 的证据边界

真实行为 RED 均保留：required tenant header 缺失或 JSON null 被接受；重试未检测列篡改；预算在读取后才检查；固定 reset key 新建轮次；当前已知 evidence 删除仍 VALID；实际前轮 seal 被管理员改动而当前轮仍 VALID。对应 GREEN 已包含在上述最终 52 项内。

[ancestor 首轮](evidence/MVP-304-storage-ancestor-red.txt) 的 pending-trigger DDL 错误是测试夹具错误；修正夹具后才得到 [ancestor 行为 RED](evidence/MVP-304-storage-ancestor-behavior-red.txt)。UV 缓存权限、pytest cache 权限、迁移夹具 JSON 冒号 bind 和 pre-audit schema 上运行新 hook 的错误不计为生产行为 RED。旧 migration 测试只在随机库中构造 pre-audit 原银行事实，再核验真实经济数据字节迁移；不删除既有审计史，也不据此宣称审计 VALID。

未知 303 algorithm 的存储额外检查在域原件核验之后执行，保留原 hash、关联和约束篡改的完整性优先级；原件不变但算法不可识别才返回 UNSUPPORTED_VERSION。root 保留此兼容用例的独立 RED/GREEN 日志。
