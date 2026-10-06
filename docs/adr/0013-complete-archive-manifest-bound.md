# ADR 0013：完整归档清单使用独立有界编码

状态：IMPLEMENTED / TARGETED_PG_VERIFIED，2026-10-05。仅初版合成审计归档清单，不改变事件协议、原哈希或正式历史。

真实Edge工资链通过后，第二用例的明确UI重置POST84.7秒返回500。原run `edge-business-and-rounds-20261005T033610Z-91351800` 保持FAILED、隔离库已VERIFIED_ABSENT。其原24.5MB快照包含7610行，其中6552条DecisionConstraint；合并当前epoch原副本与所有业务原件的7092条索引规范字节1,115,737，超过单事件1MiB门。独立原服务PG回归 `salary-reset-actual-regression-20261005T034411Z-c9e6219a` 实际1 FAILED/222.69秒，在reset_archive_epoch的canonical_bytes(manifest)报AuditContractError，完整原栈保留。

完整索引跨越许多subject，原错误将它当单条事件。新增archive_manifest_bytes仅接受数据库原形状entries/counts：全部登记subject kind、规范UUID、原SHA256、排序唯一三元组与逐kind精确整数计数。上限50000条、16MiB，继续使用原非raw深度32/节点250000/有符号64整数及同一UTF-8、排序键、紧凑JSON编码。单事件仍1MiB，raw subject仍16MiB。超过任一边界仍拒绝，不裁剪清单或跳过审计。

reset归档、SEALED库验证以及纯archive_manifest_digest全部使用独立编码。`bounded-funds/audit-archive-v1\0`命名空间、SHA256及全部清单条目保留；原小清单输出必须逐byte相等，历史hash不回写。无迁移、无正式库reset/seed、无权限缓存和真实资金接口。

既有纯审计域28项和独立清单风险60项已PASS；后者包括原7092条完整索引、420条小原件的原字节/哈希，以及删除、乱序、重复、kind/id/hash/count、字节/条目门负例。首轮101字符静态错误保留，最终Ruff和Mypy通过。`salary-reset-archive-green-20261005T035038Z-85883fbe`真实两个PG节点2 PASS/284.88秒，完成原工资后reset和旧key重放，旧epoch SEALED/新epoch OPEN均VALID，旧原件保留。随后实际Edge工资链1 PASS，下一reset成功；目标账户精确标签卡住的原FAILED独立保留。实际Edge六链/三轮仍未通过，不能据编码实现关闭MVP404。
