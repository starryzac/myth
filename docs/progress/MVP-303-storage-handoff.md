# MVP-303 storage owner 交接

2026-10-04，状态：storage owner 实现及定向验证完成。本文保留owner交还时的历史状态；root随后完成正式0005非重置迁移及完整check，最终结论见MVP-303.md，本文不独立充当全量验收记录。

## 已完成并有真实证据

- `models.py` 新增可空父 run、subject action 同用户组合外键与索引；action 外键使用 `use_alter=True`，不破坏原动作创建顺序。
- `0005_decision_trace.py` 迁移已实现。降级仅允许随机 `bf_test_<32hex>` 数据库；降级丢弃关系投影列，保留冻结 JSON，后续升级不宣称恢复历史关系。正式库不允许此降级。
- `services/decision_trace.py` 提供冻结来源/策略、幂等记录、约束投影、历史只读读取、动作关联与有界稳定分页。
- 原 INVALID hash 声明仍能冻结并读取；合法 supersede/revoke 仅附注；单项内容、约束或来源索引变化返回 409。父链必须是原动作的祖先，禁止同用户换绑另一决策。
- 保持原恢复 `preview`、通知、状态、完成时刻语义；冻结内容置于 `input_snapshot.decision_trace`，完整 snapshot hash 覆盖额外原字段和冻结来源索引。
- `MVP-303-storage-history-green.txt`：17 项通过，15.91 秒。`MVP-303-storage-mypy-second.txt`、`MVP-303-storage-ruff-second.txt` 均通过。没有运行全量 check 或 coverage。
- 后续真实行为 RED 修复已完成：原 effect、动作身份和完整恢复 request 的绑定；读取时钟不能早于 run 时钟；演示重置仅在原事务内清空演示用户的 nullable 关系。
- `MVP-303-storage-final-green.txt`：storage / migration / reset 三文件共 22 项通过，46.75 秒；真实执行后 seed 两次得到完整相同数据库，其他租户关系保留；失败 seed 完整回滚。
- `MVP-303-storage-capture-final-green.txt`：2 项重叠测试通过，8.77 秒，补充专用 available capture 的缺项/跨租户分支；不能与上述 22 项累加为唯一总数。
- `MVP-303-storage-final-mypy.txt`：owner 五文件无错误；`MVP-303-storage-final-ruff.txt` 通过。
- 额外领域接缝 RED 发现购买投影会把 ActionPlan.position_id 从 null 更新到原 effect.position_id；已按 301 原协议精确允许这两个值，赎回仍要求原精确持仓，其余动作要求 null。`MVP-303-storage-purchase-ruff.txt` / `-format.txt` 均通过；该 purchase 参数的最终业务 GREEN 由 domain owner 运行并交 root。

## 缺项捕获合同

默认 `capture_sources` / `capture_policies` 保持 missing/foreign 404。在 domain 真实 PG 缺源 RED 后实现专用 `capture_available_sources(session,user_id,ids) -> (copies, missing_ids)`，只有显式记录入口使用；实际存在而属于外用户的 ID 仍 404，不返回其内容。真正不存在的来源不会创建占位证据，root 把缺项 ID 冻结到 `inputs.missing_evidence_references`。该路径不产生任何资金动作或权限。

## 当前环境与运行状态

Docker / PostgreSQL 54329 曾离线，已由 root 统一恢复并确认原容器 Healthy；没有重置正式数据。离线 exec session `14597` 只收集了 6 个选中测试，没有完成任何 case；按 root 明确授权发送 Ctrl+C，退出 1。此轮不是行为 RED，详见 `MVP-303-storage-request-reset-environment.txt`，原收集日志保留，不覆盖或删除。本组当前无 pytest 进程。

首次恢复后的 `MVP-303-storage-request-reset-behavior-red.txt` 共 5 失败，其中 reset 两项最初是测试夹具缺 accepted 字段；修正后，`MVP-303-storage-reset-fixture-corrected-red.txt` 才真实得到 2 项 subject FK 冲突，34.47 秒。effect/request/readclock 三项原始断言红灯与这个真正的 reset 红灯是实现修复依据，不把环境或夹具错误冒充生产缺陷。

## 精确下一步

源码交还冻结后由 root 统一执行完整质量门；只在具体失败或新修改后追加定向复核。恢复本组定向验证的精确命令：

```powershell
uv run --frozen pytest apps/api/app/tests/test_decision_trace_storage.py apps/api/app/tests/test_decision_trace_migration.py apps/api/app/tests/test_decision_trace_reset.py
```

没有迁移或重置正式演示库，没有外部金融接口；所有上述数据库测试只针对随机隔离模拟库。

禁止 LibreOffice，不清理缓存、卷、数据库或失败证据；所有数据库测试仅用已有 `temporary_database()` 生成的随机隔离库。
