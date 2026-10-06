# 完整版 STRIDE 控制与当前证据

2026-10-06 功能优先增量；FULL-801/802/803 保持 PENDING。此文件补充初版威胁模型，不把已有代码或局部结果直接当成完整安全验收。

保护对象：用户明确意图、真实模拟银行来源、原配置与经济效果、收入源分与目标归属、独立银行操作/posting/回执、审计原件、原键恢复以及私密会话凭证。浏览器、用户自由文本、候选模型输出和展示说明均不能自行提供银行事实或金融权限。

| STRIDE | 实际触发与应保持的不变量 | 当前可运行控制 | 当前证据/精确缺口 |
|---|---|---|---|
| S 身份冒充 | 客户端把 role 写成 USER/AGENT 或改签名 payload | 服务器 HMAC、固定 bounded-user→实际模拟 owner、有限签名合同、当前过期检查；每个受控写重新核验 | 本地身份实际 PG `W4/actual-local-owner-cookie-domain-repair-20261005T210101Z-75f86e0c` PASS；拒客户端角色、伪造签名与原 owner 分母；没有真人身份核验 |
| S 收款人冒充 | 新/歧义 payee、不同来源账户借用旧周期策略 | 固定付款完整 scope 绑定 FULL 与原 MVP 版本/配置、已证明 BANK payee、来源账户及时间；USER 发起/确认 | 新606 domain/service/API 定向能力已交；实际金融 AUTO/ASK 未取得成功，不能用前端或合成 principal 冒充 |
| T 候选篡改 | LLM/文本夹入金额权限状态或执行字段 | 原严格 DSL 与额外字段拒绝；模型只产生候选；新 FULL 编译与独立校验正在实施 | 12模板 Schema 可运行；自然语句编译/脱敏/独立来源差异具体覆盖待新模块交付；不宣称802全通过 |
| T 配置/产品漂移 | 客户端重算 hash 后换产品、版本、现金scope或原批 marker | 原目录不可变原件、FULL/MVP版本、完整原组合/批次/action/command绑定；独立银行新接收再次重验；删除 marker/key 仍由持久批次索引识别 | 多资产当前实际链 RUNNING；原prepare500与当前时钟诊断记录保留，不能把纯 guard 或57 UI测试当银行拒绝实证 |
| T 审计/账本篡改 | SQL 改行、删除、乱序、截断或伪造 posting/回执 | 原哈希链、原财务/银行回执与跨表来源验证；新保留表拒 UPDATE/DELETE/TRUNCATE 与带数据 downgrade | 0012保留全部负例与当前35模型迁移往返实际 PASS；0013新增保留负例此前实际 PASS。不存在独立数据库之外的生产银行签名/外部可信锚；不能把拥有全部数据库写权限时的重造攻击称为已解决 |
| R 抵赖/错误重放 | 换 key、不同 body 或确认上一版本后执行新效果 | 持久原完整请求与 hash，明确复核 effect/portfolio/scope，原键查询；NOT_FOUND 不作最终拒绝证明 | 固定付款与整组资产前端原GET/列表外恢复、两族父门已定向通过；新金融原键链尚待实际终态 |
| I 信息泄露 | 日志、响应或候选来源泄露 secret/token/signing key | 本地签名 key 只在 ignored 环境；凭证提交清空；HttpOnly cookie；静态错误消息不回显原凭证 | 身份模块相关检查与实际零写 PASS；全浏览器cookie、最终日志/导出/材料完整脱敏审查未完成。私密文件不作为证据包输入 |
| D 重复/资源耗尽 | 同 action/key 重扣、多批越序、未决后继续下一批 | 独立银行原幂等、原 UNKNOWN 恢复；固定 expected_batch_number/action；每族原请求门、存储错误阻写、后批停 | 原银行 UNKNOWN 同键能力历史实际通过；新资产完整链仍运行。新链并发、大负载、时延/SLA 未实测，无杜撰性能结论 |
| E 越权 | FULL规划或身份显示直接给银行权限、跨账户借钱、未来收入支持今天 | FULL只增加当前否决；原 MVP 权限独立必须成立；注册账户保守支出证明只限同请求、未来收入/本金/借他账户均0 | 新保护 consumer16直接 PASS/3.78s、strict3源PASS；实际606付款仍未测得成功，默认无证明 UNKNOWN 负例保留 |
| E 在途撤销误释放 | 策略撤销后删除已提交银行操作/claim 或新建替代key | 原用户锁、新策略命令、已登记依赖 recheck；任何 bank/未决保留原键/claims，零effect未提交才可失效 | release/payment已接605，whole asset新适配器在实施；新605实际未决撤销/原键恢复与覆盖矩阵尚未通过，结果字段仍不宣称所有适配器支持 |
| T 假解释 | 前端展示“已安全”掩盖 UNKNOWN、未来收入或未提交动作 | 只读原JSON/来源/hash、未知量保留null；客户端解释不参与原银行判断 | 当前父页37与来源host2合成HTTP PASS；没有真实浏览器完整操作/所有页面无障碍验收 |

金融检查只能在生成隔离 `bf_test_*` 库运行，当前正式模拟库不迁移、不 reset、不改原失败或历史 hash。日常只跑变更模块及直接风险；当前慢资产节点未终态，不能推断成功，也不并行再开金融节点。原全量、并发、故障和完整材料审查集中在功能完成后的验收节点。

来源：完整计划16.3、原需求追踪FULL-801—803；当前源码 `services/local_actor_sessions.py`、`services/full_payment_permissions.py`、`services/full_asset_execution_dispatch.py`、`services/execution_bank.py`、`domain/full_execution_protection.py`、`domain/full_registered_account_debits.py`；原件入口 `docs/progress/FULL-registered-account-debits-consumer.md`、`docs/progress/FULL-402-606-app-consumers.md` 与各模块进展。外部合规/真实银行生产安全不在本模拟证据的已证明范围。
