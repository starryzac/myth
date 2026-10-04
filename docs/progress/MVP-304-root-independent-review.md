# MVP-304 root 文件独立只读审查

2026-10-04，storage owner 对 root 的 5 个文件进行源码审查，并读取已有真实测试日志。未修改源码、测试或合同，未运行 CLI seed、正式数据库迁移或资金动作。本轮没有发现阻断统一验收的 root 文件缺陷；完整 check 和正式非重置迁移仍由 root 独立完成。

## 源码结论

| 检查点 | 依据 | 结论 |
|---|---|---|
| 真实 BEFORE 副本 | `audit_recording.py:58,313,605` | mapper 全列复制；UUID/date/aware UTC 转 JSON，嵌套 dict/list 递归复制，保留原 bool/float。状态转换要求提供已复制的 BEFORE，校验其原状态，再捕获 AFTER；不存在通过替换新对象的 status 来重建旧行。实际业务调用方在变更前调用该复制入口。 |
| typed 同 UUID 原件锚 | `audit_recording.py:177` | anchor kind 映射到具体 subject kind，按 kind/id/AFTER 匹配，必要时 BASIS。ACTION_PLAN 与 BANK_OPERATION 使用相同 UUID 时，posting-set 锚仍指向银行原件，request 锚指向动作原件。BEFORE/AFTER 的不同 hash 可并存。 |
| 决策与完整经济绑定 | `audit_recording.py:264,396,449` | 决策绑定原 trace hash；银行结算从实际 operation 读取完整非 OPENING posting 集合，并绑定全行 digest；投影绑定原 receipt 全行、posting-set、所声明 transaction 和其 evidence。没有内部 commit、经济结算或自造银行结果。原内容/身份/金额核验由域和 storage 完成，录制器不把摘要当授权。 |
| 固定事实重试与历史缺口 | `audit_recording.py:105,154` | 固定 fact 的录制 helper 可以返回原事件，但先 parse/verify 原 canonical 文本，再核实际 epoch。仅整体 VALID/明确 LEGACY_UNAUDITED，且 chain VALID、reference VALID/LEGACY_UNAUDITED、errors 空时返回；当前错误不会因 legacy 标记被忽略。generic append 另行比较 intent 语义，状态变更 fact_key 绑定真实前后数据与 context。 |
| 审计 HTTP 参数及租户 | `api/v1/audit.py:22,34,57,62,97` | limit 为 1..100；拒绝额外 query/body 字段。服务入口只接实际 DemoUser，epoch 归属由同用户查询校验。verify 不接受 repair、authority、用户或客户端时钟覆盖。head 的 COMPLETE 表示已知 head 可解析，不等于全历史 VALID；完整性分类在 verify 中返回。 |
| 真实只读事务 | `api/dependencies.py:25` | GET 和审计 POST 在第一次 SELECT 前设置 REPEATABLE READ；审计路径先执行 SET TRANSACTION READ ONLY，再加载用户及核验，沿原 Session 单事务读取。读取不会 ensure epoch 或追加事件。 |
| CLI 有界读取与范围 | `scripts/verify_audit_chain.py:25,29,78,159` | checkpoint 最多读 65537 字节，超 65536 明确拒绝；先 count，轮次数超过 1000 不装载 epoch 列表。单 epoch 或全部保留轮次分别标明范围；检查 ordinal、前轮封口与 legacy 原事件，子集不标为全历史。每轮事件/原件进一步由 storage 的预读取预算保护。 |
| CLI 检查点与出码 | `scripts/verify_audit_chain.py:177,181` | 只有本次所选范围全部 VALID 才导出 canonical checkpoint；最终文件使用独占 x 模式，既有文件或竞态中的已有文件不会被覆盖。有效返回 0；未知、缺失、损坏、legacy 和 checkpoint 不匹配返回 1；输入/文件/数据库错误返回 2，不输出假 VALID。普通 hash 未宣称是签名或外部可信存证。 |
| seed CLI 输出 | `scripts/seed_demo.py:19` | 先执行已有授权的 seed 服务，再开独立 RR/READ ONLY 观察真实 head 和保留行数。稳定业务 summary 与 `audit_metadata` 分开，后者包含实际 read_at；这是提交后的独立观察，不冒称 seed 原事务中的同一时刻。金融版本仍 mvp-301-v6。 |

## 已有实际证据

本轮直接读取日志和对应测试源码，以下是各 owner 的既有实际结果，并非本轮新复现：

- [root 最终定向](evidence/MVP-304-root-targeted-final.txt)：**29 passed / 106.84s**。包括 11 个 HTTP/CLI 用例及原 303 storage 兼容，实际 unknown trace 顶层 UNSUPPORTED_VERSION、旧缺口 LEGACY_UNAUDITED、原 current 篡改检测。
- [HTTP/CLI 前轮](evidence/MVP-304-api-cli-final.txt)：**9 passed / 63.25s**；实际 PostgreSQL SHOW 检查 RR/READ ONLY，同时前后全表快照相同。CLI checkpoint EXACT/PREFIX、既有输出不覆盖、未知用户/epoch、管理员篡改及超长 checkpoint 均有实际断言。随后新增 activation、seed CLI 结果已包含 root 最终组。
- [独立工作流](evidence/MVP-304-hooks-final-green.txt)：**8 passed / 386.77s**。真实转账明确断言同 UUID 的 ACTION_PLAN/BANK_OPERATION typed 原件和银行 posting anchor；最终 income request hash、真正 BEFORE/AFTER、205/301 单次受理/结算/回执、审计 INSERT 失败后的银行保留与 UNKNOWN、三段命令 reset gate 均有真实覆盖。
- [seed CLI](evidence/MVP-304-seed-cli-first.txt)：**1 passed / 20.07s**，实际两次命令输出业务 summary 相同、epoch 从 2 增至 3，事件/副本增长，审计观察为 RR/READ ONLY。
- [域最终](evidence/MVP-304-domain-final-green.txt)：**28 passed / 0.83s**；pure domain 结果独立于 PostgreSQL，覆盖原 anchor、未知版本优先级、完整旧 archive ledger，不能将这些纯函数结果当作数据库触发器证据。

所有本次核对到的集成证据使用随机 `bf_test_<32hex>` 数据库。管理员 DDL 能力、正式应用仍使用当前数据库 owner 凭据、单合成用户身份和未接外部存证的边界保持 ADR 0012 的原声明；本审查不扩大其保证。

## 审查文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| apps/api/app/services/audit_recording.py | `7445ff6e1fcc9bedc6823fe808f085274eed4cf0a9d634cd11bd1d64b1b96e6c` |
| apps/api/app/api/v1/audit.py | `fc1f6eead5297c8127c4a87f406e9667466ea90404b614cdafcea3a2ffa949ce` |
| apps/api/app/api/dependencies.py | `a55e02e03e1318405dd50ef51063c22d2c64d5dcdc79195c5904b07153d45229` |
| scripts/verify_audit_chain.py | `16d249b4865e61394c2c692ed3158cf55ec325e9123bc1a0da24c00716d26bef` |
| scripts/seed_demo.py | `ea32639a547b94ac50b6d6aabe7c02c93b09d4348fbb56d5396bb7dff3f2a1cd` |
