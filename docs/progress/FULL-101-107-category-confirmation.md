# FULL-101 / FULL-107 历史消费类别明确确认

2026-10-06 北京时间。功能增量已实现；原需求仍PENDING，实际PG正在运行，未宣称通过。

新增 `domain/transaction_category.py`、`services/transaction_category.py`、`api/v1/transaction_category.py` 及两个直接测试文件。注册实际 GET `/transactions/{id}/category-review`、POST `/transactions/{id}/category-confirmation`、GET `/transactions/{id}/category-confirmations/{epoch}/by-key/{key}`。GET 先于身份查询建立原 RRRO 快照，无客户端事实或时钟覆盖。POST 只接受有限类别、明确布尔true、实际复核hash、reason、原epoch/key；USER身份由服务器决定。

本功能补齐建议服务的真实前置：原种子租金虽有BANK_CONFIRMED交易，却未有USER_DECLARED分类确认，先前PG期待READY错误，原失败和当时源保留。新接口核验独立银行原件、完整交易、经济角色CONSUMPTION与DEBIT、可知时间/有效窗口、原epoch及复核hash；银行金额/余额/经济角色、历史覆盖证明和旧hash保持原件。首次确认在原事务只修改类别与category_confirmed，并追加独立USER_DECLARED Evidence与精确 `TRANSACTION_CATEGORY_CONFIRMED` typed审计事件，记录真正BEFORE/AFTER交易及银行/声明原件。新增事件名称与TRANSACTION关联不改变旧v1默认字段、canonical字节或已存历史。没有资金action、BankOperation、金融授权、自动策略确认或真实资金接口。

原key查询验证完整typed审计、独立声明封套/hash和原回执；同键exact原请求返回原事件/hash/receipt，改body拒绝。NOT_FOUND_NOT_FINAL不允许替换原键。已确认交易的更正需独立版本流程，本入口明确拒绝；不能修改旧声明状态、覆盖原分类证据或将收入/转账伪装消费。当前仅模拟用户身份，完整角色鉴权、类别更正版本、前端操作和专用导出尚未交付，不能据此关闭FULL-101/107。

已运行：W5/category-direct-and-old-audit-negative-20261005T164012Z-8cc4a585，24新直接风险+1原未知审计算法负例共25PASS1.27s。首次strict17错误保留W5/category-confirmation-first-strict-types-20261005T163516Z-ada02db0和`.runtime/category-confirmation-first-type-failed-20261005T1636Z`原源；窄修变量名字及类型后5源strict PASS（163600Z-0b1df9d2），测试/Main/dependencies四strict PASS（164013Z-2a00ca37），十源Ruff PASS（164155Z-d6cbc1dd）。首次未格式化长行Ruff输出保持原运行语义，不称它通过。

必要真实集成正在唯一root session67312，目录W5/actual-full-joint-and-explicit-category-patterns-real-pg-20261005T164204Z-eee000f7：FullJoint实际消费保护、分类确认两租金→真实建议、原缺确认与覆盖篡改拒绝三个节点顺序运行。生成隔离bf_test库，不更改正式库。结果未取得；本测试中的明确actor为模拟产品接口调用，绝非真人研究或招募记录。

下一前置：取得实际原audit、回执重放和全physical金融不变的节点结果；若失败，保存原manifest/source后只修失败节点。随后接实际前端确认与原key恢复，保留更正流程和角色权限的具体缺口。
