# W0 三个固定场景性能实测

生成：2026-10-05T01:00:32.464541+00:00。状态：完整对照已取得。
本报告只从本次原结果生成；每路径 n=1，所有比例均为单样本局部对照。没有 P95/P99、稳定加速倍数或延迟 SLA 结论。

[原运行清单](performance-fixture-repair-20261004T232505Z-5daa2f9b/manifest.json)；[方法与限制复核](performance-method-review.md)。基线加载执行前冻结源码，候选复用同一场景数据库与时钟，不重新 seed。

本清单显式复用原短/长 PASSED 结果和哈希，原扩大夹具失败清单保留；工具唯一文本差异为扩大夹具 counterparty 改用 seed 已开户 payroll/merchant，测量代码未变。

## 正常读取和数据规模

API 墙钟为真实 TestClient GET 至响应收到；服务墙钟含新 RR/READ ONLY Session、服务及产品 model_dump。均排除客户端 JSON 解码/证据哈希和诊断插桩。

| 场景 | 版本/结果 | 23 表业务行数 | 存储审计 canonical 文本字节 | API / 服务正常秒 |
| --- | --- | ---: | ---: | --- |
| short-seeded | [baseline / PASSED](performance-20261004T225248Z-a5290990/baseline/short-seeded/result.json) | 422 | 2437 | 0.385204 / 0.402735 |
| short-seeded | [candidate / PASSED](performance-fixture-repair-20261004T232505Z-5daa2f9b/candidate-20261005T000955Z-8a890efa/short-seeded/result.json) | 422 | 2437 | 0.327347 / 0.165359 |
| mvp401-long-chain | [baseline / PASSED](performance-20261004T225248Z-a5290990/baseline/mvp401-long-chain/result.json) | 10575 | 13305017 | 101.635072 / 83.554818 |
| mvp401-long-chain | [candidate / PASSED](performance-fixture-repair-20261004T232505Z-5daa2f9b/candidate-20261005T000955Z-8a890efa/mvp401-long-chain/result.json) | 10575 | 13305017 | 19.960296 / 49.880999 |
| expanded-fixed-history | [baseline / PASSED](performance-fixture-repair-20261004T232505Z-5daa2f9b/baseline/expanded-fixed-history/result.json) | 14116 | 18899257 | 112.629382 / 116.159879 |
| expanded-fixed-history | [candidate / PASSED](performance-fixture-repair-20261004T232505Z-5daa2f9b/candidate-20261005T000955Z-8a890efa/expanded-fixed-history/result.json) | 14116 | 18899257 | 138.810674 / 81.163265 |

## 独立诊断请求的重复工作

以下均按 API / 服务分列，不与正常秒数混合。SQL 包括事务设置和容量查询。返回行数累积重复查询及聚合结果，不能当唯一业务规模。模型次数仅 Python BaseModel 入口，C 层嵌套实例数未观测。canonical 核心次数不与其嵌套入口相加。

| 场景/版本 | SQL 次数 | SQL 驱动区间合计秒 | 累计已知返回行 | canonical_bytes 次数 | 模型 Python 入口 | ledger_heads 次数 |
| --- | --- | --- | --- | --- | --- | --- |
| short-seeded/baseline | 81 / 81 | 0.210885 / 0.197389 | 1226 / 1226 | 15 / 15 | 341 / 341 | 4 / 4 |
| short-seeded/candidate | 83 / 83 | 0.096205 / 0.088758 | 1228 / 1228 | 15 / 15 | 341 / 341 | 4 / 4 |
| mvp401-long-chain/baseline | 582 / 582 | 3.533223 / 3.461790 | 12512 / 12512 | 15397 / 15397 | 22811 / 22811 | 13 / 13 |
| mvp401-long-chain/candidate | 290 / 290 | 0.941962 / 1.779044 | 12073 / 12073 | 15565 / 15565 | 12803 / 12803 | 5 / 5 |
| expanded-fixed-history/baseline | 5037 / 5037 | 11.934879 / 13.027993 | 29075 / 29075 | 102404 / 102404 | 74003 / 74003 | 34 / 34 |
| expanded-fixed-history/candidate | 4613 / 4613 | 11.315713 / 10.601737 | 28348 / 28348 | 102773 / 102773 | 60645 / 60645 | 26 / 26 |

## 独立 CPU 与内存 profile

CPU 请求有 cProfile 开销，含数据库等待；内存请求有 tracemalloc 开销。Python 峰值不含原生或 DB 服务端分配。进程工作集为 worker 生命周期高水位，包含导入、夹具、快照与 profiles；基线构造夹具而候选不构造，因此不计算请求内原生内存优化比例。原 pstats、文本与 tracemalloc 文件保留在各结果目录。

| 场景/版本 | CPU profile 墙钟秒 | 内存 profile 墙钟秒 | 请求 Python 分配峰值 bytes | 进程生命周期工作集峰值 bytes |
| --- | ---: | ---: | ---: | ---: |
| short-seeded/baseline | 0.513029 | 1.466628 | 2111478 | 140394496 |
| short-seeded/candidate | 0.221339 | 0.436145 | 2000531 | 124796928 |
| mvp401-long-chain/baseline | 179.718387 | 499.216064 | 249640231 | 730611712 |
| mvp401-long-chain/candidate | 115.347067 | 382.922539 | 280724062 | 784277504 |
| expanded-fixed-history/baseline | 363.531018 | 969.228046 | 353670321 | 1060179968 |
| expanded-fixed-history/candidate | 245.596218 | 1054.863828 | 392620660 | 1102467072 |

## 同输入结果与零写核对

- short-seeded：响应 hash 一致、审计 VALID、业务表与迁移元数据四份摘要一致；api-get 耗时变化 -15.02%；repeatable-read-service 耗时变化 -58.94%（n=1）。
- mvp401-long-chain：响应 hash 一致、审计 VALID、业务表与迁移元数据四份摘要一致；api-get 耗时变化 -80.36%；repeatable-read-service 耗时变化 -40.30%（n=1）。
- expanded-fixed-history：响应 hash 一致、审计 VALID、业务表与迁移元数据四份摘要一致；api-get 耗时变化 +23.25%；repeatable-read-service 耗时变化 -30.13%（n=1）。

## 执行环境与未覆盖项

实际平台 Windows 11/AMD64，Python 3.12.5，28 逻辑 CPU。WMI 返回 CPU 型号/物理核/内存字段均为空，详细硬件未取得。各结果保留自身 PostgreSQL 版本/参数、依赖锁、源与工具 hash。CPU 频率、亲和性和后台负载未控制；DB 缓存未知、未清空。

扩大场景为长链加三轮固定 INCOME=100/CONSUMPTION=100 分，以及实际 prepare_action；外部现金净变化零，新增准备动作不确认/执行，状态及预留以真实 fixture-phases 和表快照为准。初始正式模拟库未用于构造性能场景。

未覆盖外部网络/浏览器延迟、部署、多用户并发、DB 服务端 CPU/内存、请求原生内存、C 层全部嵌套构造或长期统计性能。性能结论不关闭 FULL 规模实验、真人研究或两次全量验收。

## 收尾绑定补充

上述字节指标来自数据库 octet_length；未单独记录 server_encoding，因此不进一步宣称其字符编码。每场景 API 与服务、基线与候选的四份响应 SHA 均相同；固定 as_of 见各 fixture.json。

原工具 SHA `a55aaae0963b878ba1413703873d033481f3f541a7f28bf4927347a7d466f539`；最终工具 SHA `2d5f296f99f5a64e04a48a2a6234e96dfa5fbd4102f5a8ac50f187c7424a10b7`。当前及原失败 manifest 的路径和 SHA 另绑定于 [报告绑定](performance-report-binding.json)，原失败状态不更改。

## 本次单样本中增加的成本

- short-seeded：API SQL 次数 81 → 83（+2.47%）。
- mvp401-long-chain：API canonical_bytes 次数 15397 → 15565（+1.09%）；请求 Python 分配峰值 249640231 → 280724062（+12.45%）。
- expanded-fixed-history：api-get 正常耗时 +23.25%；API canonical_bytes 次数 102404 → 102773（+0.36%）；请求 Python 分配峰值 353670321 → 392620660（+11.01%）。

这些结果含局部下降与增加，尚未测得稳定延迟、因果归属或总体算力节省。
