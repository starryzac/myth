# MVP-205 独立恢复账本

依据 [ADR0008](../adr/0008-evidence-bound-safety-recovery.md)，测试 [test_recovery_properties.py](../../apps/api/app/tests/test_recovery_properties.py) 只对整仓、明确返本时间的有限模拟事实建立独立预期。公开规划接口的独立测试已完成下述一次完整运行；银行执行和整项质量门另行验收。

## 独立性和时间约定

独立账本仅有现金、常量非目标保护、目标现金及若干原始本金。它直接构造90日/273检查点，不调用生产 `compute_boundary`、恢复规划器或其内部函数生成 expected。当天付款前、付款后、本金到账后分别占一个点；收益和未来收入没有进入账本的字段。

T0预计恢复是“已经到账后的反事实快照”，本金从第一个检查点起转成现金；原实际快照不变。T1在day1的本金到账阶段才增加预计现金，此前五个检查点仍可能不足。提前返本替换原持仓的返本事件，不另建一笔同本金未来回款。目标本金到账时现金与同目标现金保护同步增加。

候选比较使用逐点关系：全部新余量不小于当前累计方案的对应余量，且至少一个原负点严格改善。全窗最小值未提高不等于没有改善。按T0/T1和原持仓身份确定顺序，整仓尝试；没有可改善负点即停止。模型不假设部分赎回，也不把整仓金额截成现金缺口。

## 字面真值

| 输入与动作 | 当前事实 | 预计或结清后的字面结果 |
| --- | --- | --- |
| C70000、保护100000、T0整仓50000 | 缺30000 | 到账后C120000、余20000；不默认只赎30000 |
| 同一缺口、T1整仓30000 | 缺30000 | 前5点仍缺30000，day1本金阶段起余0；全窗min仍−30000 |
| T0整仓10000加T1整仓20000 | 缺30000 | T0后仍缺20000，T1实际结清后才恢复 |
| C70000含目标现金20000、其他保护80000、目标本金30000 | general缺30000 | 回原目标后C100000、目标保护50000，总保护130000；general仍缺30000 |
| 原本金30000计划day5返还，提前整仓T0返还 | day5前缺30000 | C最多100000，day5不再加第二次本金 |
| 缺30000，顺序两个T0整仓50000/80000 | 缺30000 | 第一笔已消除负点，第二笔不为增加正余量而赎回 |
| 本金30000、费用100、损失0 | 缺30000 | 净到账29900、余缺100，只能成为非自动ASK_ONCE提案 |

## 运行状态与验证边界

独立字面模型首次运行 **7 passed / 0.37s**，见 [oracle-first](../progress/evidence/MVP-205-recovery-properties-oracle-first.txt)。此阶段没有调用生产恢复规划器，不能把字面模型通过称为恢复实现完成。

公开T1接口首例已记录真实 [red](../progress/evidence/MVP-205-recovery-properties-public-first.txt)：当时首版将T1立即转为现金，预计全窗最小余量变成0，而独立真值应保留−30000及前五个未覆盖点。领域所有者处理此明确TDD用例后，[前三组财务性质和公开T1](../progress/evidence/MVP-205-recovery-properties-financial-first.txt) 已实测4 passed / 19.22s：三组各150个有效样例、0失败，首组另有4个Hypothesis invalid样例，不计入有效数；其余两组invalid为0。

最终一次完整运行 **12 passed / 29.36s**，包含8项固定测试和4组生成性质，见 [final-green](../progress/evidence/MVP-205-recovery-properties-final-green.txt)。每组设置为 `max_examples=150, derandomize=True, database=None, deadline=None`。

| 生成性质 | 有效通过 | 失败 | Hypothesis invalid |
| --- | ---: | ---: | ---: |
| 整仓顺序、逐点改善与独立273点账本一致，输入事实不被修改 | 150 | 0 | 4 |
| T1在全窗min不变时仍改善未来负点，实际现金与预计现金严格分开 | 150 | 0 | 0 |
| 原返本事件被替换、消除负点后停止多赎、重排后完整结果与hash不变 | 150 | 0 | 0 |
| 目标本金与展示收益不填general余缺；完整条款hash仍绑定动作 | 150 | 0 | 0 |

合计 **600个有效生成测试样例、0失败，另4个invalid不计有效数**。只统计上表这一次完整运行，不累计先前定向运行、重复预览或一个样例内多次规划调用。输入与expected由独立字面账本构造；生产配置验证器只用于构造合法DTO，不生成金融预期。

最后一组把产品展示年化从0改为10000bps，并同步完整产品/报价条款摘要。实际和预计的完整 `BoundaryResult` 必须相同；`plan_hash` 则必须不同，以保留准确原条款的动作绑定。这不把预计收益当作本金或新增收入。

命令（仓库根目录，PowerShell）：

```powershell
.venv\Scripts\python.exe -m pytest apps/api/app/tests/test_recovery_properties.py -q -p no:cacheprovider --hypothesis-show-statistics
.venv\Scripts\ruff.exe check apps/api/app/tests/test_recovery_properties.py
.venv\Scripts\ruff.exe format --check apps/api/app/tests/test_recovery_properties.py
.venv\Scripts\python.exe -m mypy apps/api/app/tests/test_recovery_properties.py
```

静态检查均通过：[Ruff](../progress/evidence/MVP-205-recovery-properties-ruff.txt)、[格式](../progress/evidence/MVP-205-recovery-properties-format.txt)、[严格mypy](../progress/evidence/MVP-205-recovery-properties-mypy.txt)。本组未修改生产源码、数据库、API或主进度，也未执行完整项目check或提交。

本文件的纯函数测试不能证明真实银行posting、应用补投影、并发互斥、ActionReceipt、通知或来源完整性，也不替代有损报价、manual权限及所有输入拒绝矩阵。这些由领域所有者及205服务/真实PostgreSQL集成测试验收。尤其 `projected` 通过不表示银行已经到账，T1计划不得被报告为实际RECOVERED。合并运行时权限/输入严格校验仍由领域所有者收尾，最终生产源码状态须再由root完整check核验。

## 独立执行审计

[test_recovery_audit.py](../../apps/api/app/tests/test_recovery_audit.py) 使用隔离的真实PostgreSQL测试库、当前迁移和合成种子，直接核对独立银行请求与posting、应用余额、持仓、回执及本金返还流水。这里只注入故障和构造测试事实，不修改生产实现，不重置真实demo数据库。集成测试不计入上述600个生成样例。

| 故障或篡改窗口 | 独立验收真值 |
| --- | --- |
| 银行已提交，响应丢失 | 两条守恒posting已经存在，应用现金与回执尚未变化；同key补账及再次重放均只有一个银行请求、一份回执、一条PRINCIPAL_RETURN |
| 银行已提交，应用投影事务失败 | 银行效果不随应用事务回滚；同key仅补投影，现金只增加一次整仓本金 |
| 第一key已提交SUBMITTED，尚未调用银行时启动第二key | Event固定暂停窗口；第二key结束后同仓ASSET_REDEEM动作仍恰一条，不产生第二个悬空SUBMITTED；放行后一次经济效果 |
| 银行已提交，应用持仓account_id被改为同用户另一账户 | 同key返回BANK_RECONCILIATION_REQUIRED/409，整个数据库快照不因拒绝而变化，无新现金、回执或证明；恢复原身份后原key可以补账 |
| 应用现金增加1分，同时重算BANK_BALANCE及完整v2 manifest摘要 | 独立银行posting保持不变；202/203/204/recovery均INSUFFICIENT_EVIDENCE，精确额度为空、恢复步骤为空，预览不写数据库 |
| 两仓250000/150000分，银行首仓成功、后仓持续拒绝 | 已结清首仓必须仍能投影；持续拒绝的同key重试不重复首仓；取消故障后同key完成另一仓，最终现金增加400000分，两个请求、两份回执、四条守恒posting |

最初三项基础故障/普通并发运行 [first](../progress/evidence/MVP-205-recovery-audit-first.txt) 为3 passed / 10.31s。随后把普通并发替换为确定性phase1到bank之间的竞争窗口；该窗口及v2篡改测试 [phase-and-v2-first](../progress/evidence/MVP-205-recovery-audit-phase-and-v2-first.txt) 为2 passed / 8.52s。身份篡改定向运行 [rebound-first](../progress/evidence/MVP-205-recovery-audit-rebound-first.txt) 为1 passed / 4.74s，前五项整体运行 [five-green](../progress/evidence/MVP-205-recovery-audit-five-green.txt) 为5 passed / 18.88s。这些是不同阶段的定向记录，不累计为样例数，也不把首次green写成red→green。

v2篡改用例首先在健康入口发现历史持仓被目录导入时间拒绝的实际失败，见 [v2-first](../progress/evidence/MVP-205-recovery-audit-v2-first.txt)。领域所有者将“目录导入时间早于历史购买”的过严约束改为导入时间不晚于当前快照、产品有效窗覆盖购买；未修改旧目录、种子或manual权限，之后健康入口及篡改关闭均通过。

批次部分成功用例保留了真实 [partial-batch-red](../progress/evidence/MVP-205-recovery-audit-partial-batch-red.txt)：首仓银行SETTLED后，后仓拒绝导致整个phase3未运行，首仓回执数量为0而独立真值要求1。服务所有者修复后，六项经济效果测试先通过6 passed / 17.04s，保留在 [first-six-green](../progress/evidence/MVP-205-recovery-audit-final-green.txt)；此日志早于下一项状态断言，不能作为最终源码的独立验收记录。

补账已成功后，同一批次用例进一步记录真实 [batch-status-red](../progress/evidence/MVP-205-recovery-audit-batch-status-red.txt)：第二动作尚未受理，DecisionRun却为SUCCEEDED。实现方随后约束持久完成状态必须覆盖所有动作的SETTLED银行请求与成功回执，且没有当次银行/投影错误；通知只针对成功投影的动作。

加入持久状态断言后，本文件最终完整运行 **6 passed / 22.40s**，见 [final-after-status-green](../progress/evidence/MVP-205-recovery-audit-final-after-status-green.txt)。该运行包括原key在持续第二仓拒绝时的重复调用、原身份恢复后的补账，以及取消批次故障后全部400000分实际结清。不累计之前运行次数。测试文件的 [Ruff](../progress/evidence/MVP-205-recovery-audit-ruff.txt)、[格式](../progress/evidence/MVP-205-recovery-audit-format.txt)、[严格mypy](../progress/evidence/MVP-205-recovery-audit-mypy.txt) 均通过。

```powershell
.venv\Scripts\python.exe -m pytest apps/api/app/tests/test_recovery_audit.py -q -p no:cacheprovider
.venv\Scripts\ruff.exe check apps/api/app/tests/test_recovery_audit.py
.venv\Scripts\ruff.exe format --check apps/api/app/tests/test_recovery_audit.py
.venv\Scripts\python.exe -m mypy apps/api/app/tests/test_recovery_audit.py
```

本节证明限定模拟赎回链路的上述故障窗口，未实现或声称完成301全动作框架、真实银行网络接入、有损执行和完整通知页面。后续完整源码验收由root统一执行。
