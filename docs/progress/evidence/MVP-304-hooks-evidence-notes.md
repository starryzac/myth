# MVP-304 真实工作流挂钩证据分类

所有 PostgreSQL 工作流均使用测试夹具生成的 `bf_test_<32 hex>` 隔离数据库，真实迁移、seed、业务命令和审计 INSERT；没有 mock 审计包装，没有使用 `--cov`，没有运行全量 check。

## RED 与边界

- `MVP-304-hooks-first-red.txt`：原 prepare 已返回 200，但 DECISION_RECORDED 为 0，真实功能 RED，8.49s。
- `MVP-304-hooks-environment-cache.txt`：uv 缓存目录写权限边界，不算功能 RED；改用仓库现有 `.venv` Python。
- `MVP-304-hooks-first-implementation.txt`：重复调用账户创建夹具导致 MultipleResults，不算生产缺陷；改为只重放原 HTTP 请求。
- `MVP-304-hooks-core-second.txt`：同 UUID 的 ACTION_PLAN 和 BANK_OPERATION 导致锚原件不唯一，真实录制包装 RED；root 按实体类型与 UUID 共同匹配锚原件修复。
- `MVP-304-hooks-core-third.txt`：完整转账已成功，合法对象状态版本被 current bundle 当重复身份拒绝，真实域核验 RED；domain 对相同 kind/id/hash 的 current 副本去重，冲突副本仍拒绝。
- `MVP-304-hooks-core-income-reset.txt`：转账及收入请求两项 GREEN；并发重置已经证明独立银行提交、reset 等待 exclusive gate、放行后原回执成功，但 Future 的 60s 等待阈值不足。此项是测试时间边界，不能宣称资金逻辑缺陷。保留原日志，单独复核重置完整终态。
- `MVP-304-hooks-static-current.txt`：测试 spy 读取模块未显式 re-export 的函数触发 mypy；改从原定义模块导入真实银行函数，没有修改业务行为。

## 已完成 GREEN

- `MVP-304-hooks-final-green.txt`：最终独立真实 PG 工作流整组 8 passed，386.77s；句柄 62764 exit 0。全部 9 个业务 hook 文件与独立测试文件正式冻结，没有遗留运行中的 owner 测试进程；最后统一 check 由 root 负责。
- `MVP-304-hooks-policy-goal-first.txt`：2 passed，18.04s。撤销只记录真正的 PLANNED→INVALIDATED；重复撤销不增事件；确认策略及零金额目标只记录一次且资金不动。
- `MVP-304-hooks-t1-unknown-first.txt`：2 passed，60.89s。205/T1 使用同一统一银行操作；GET 到期仍不结算；SQL trigger 使真正的 ACTION_PROJECTED INSERT 失败，原应用投影回滚、银行 SETTLED 保留、动作 UNKNOWN；原动作重试仅补一次回执。
- `MVP-304-hooks-core-income-reset.txt`：其中转账及收入请求 2 passed。301 同 UUID 同时保留 ACTION_PLAN/BANK_OPERATION 原件引用，BANK_POSTING_SET 绑定银行原件；四阶段决策及单受理/结算/投影完整，审计核验 VALID。收入请求锚使用实际补入 income_evidence 后的最终 request_hash。
- `MVP-304-hooks-static-current-green.txt`：9 个业务服务与 1 个独立集成测试文件 Ruff、格式及显式范围 mypy 全部通过；不替代 root 最终全量静态验收。
- `MVP-304-hooks-reset-final.txt`：1 passed，99.56s。真实 reset 完成，原 epoch SEALED；旧受理、结算、回执事件各一次且仍保留，live 银行操作已按 reset 业务表清单删除。原 60s Future 阈值不够，完整复核使用 180s 防挂起边界。
- `MVP-304-hooks-final-static.txt`：最终显式 owner 范围 Ruff、format check、mypy 再核通过（10 files）；此核对仍不替代 root 全量静态检查。

## 只读与历史边界

prepare 与原键重放后的 action/decision GET 对全库快照零写入；顶层 audit_chain_status 读取当前实际链状态，原 303 explanation.audit_chain 仍保留原生成时的 NOT_IMPLEMENTED。历史缺口不能通过读取补造事件。

重置并发测试只包裹真实 `process_operation`，等待原独立银行事务返回后暂停；另一线程实际运行 `seed_demo`，由 `pg_locks` 证明同 gate exclusive 请求未获锁。它没有替换任何金额、状态或银行结果，没有合并三个原事务。

## 独立只读源码审查

- `domain/audit_chain.py:verify_epoch` 的状态优先级使当前 INTEGRITY_ERROR 优先于历史 LEGACY_UNAUDITED；genesis 的历史标记只降低原 VALID 的引用诊断，后续错误仍保持更高优先级。
- `services/audit_chain.py:verify_audit_chain` 在 SEALED 轮次不读取 live current_subjects，也不运行 303 live 引用检查；只用同 epoch 原件和完整 manifest，不能借 reset 后复用 UUID 的新实体。
- `0006_audit_chain.py` 的唯一 SECURITY DEFINER helper `audit_event_head` 固定 search_path 为 pg_catalog,public,pg_temp，并显式限定 public 表、审计 helper 和 digest；PUBLIC 的直接 EXECUTE 已撤销。head guard 同时检查真实新事件、count/prev/hash 与允许字段差分。此处是源码审查结论，不冒充已实测所有 SQL helper/search_path 攻击。
- 源码审查发现 OPEN current 原件缺失会被 `_immutable` 跳过，而 303 allow_missing 仅提供注记。root 已确认已捕获的已知不可变原件在 OPEN 丢失应影响完整性，交 storage owner 增加 original_errors 与真实 PG 删除测试；本 owner 没有修改其核心或额外测试。初始 missing_evidence_ids 和 SEALED 历史副本边界保持分别处理。
