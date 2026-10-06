# 知余 Demo 实施与验收记录

2026-10-06；直接授权：用户“按方案开始开发”。采用 `知余_Demo转型开发方案_2026-10-06.md`，显式修订见 `docs/spec/execution-amendment-v3-zhiyu-demo.md`。**首版 IMPLEMENTED：功能链与两组真实浏览器验证通过；性能目标尚未全部达到。** 本文件集中保存交付、实际证据、剩余缺口和交接。

## 当前可运行交付

- [本机试用](http://127.0.0.1:19179/zhiyu.html)：服务已就绪，尚未执行本轮模板、收入、目标与资金场景，保留原种子基线。API19006/Web19179。
- 轮次 `20261006T073008Z-9e4333a75fb649b2b39b440f67590090`；数据库 `bf_test_9e4333a75fb649b2b39b440f67590090`；epoch `077e5bab-69a9-4950-bbe5-5f5ef531a4c7`。实际DB/用户和API/Web代理标识均匹配；原件 `start-ready.log`、`ready-state-check.json`。
- [启动/新轮次指南](../demo/zhiyu-start.md)、[5—8分钟讲稿](../demo/zhiyu-talk.md)。当前服务已运行，先 `python scripts/zhiyu_demo.py status --round 20261006T073008Z-9e4333a75fb649b2b39b440f67590090`；不要重复 start。创建下一轮可显式使用空闲端口19007/19180，示例尚未创建。

检查原件目录为 `.runtime/zhiyu-validation/`；真实 Edge 截图目录为 `output/playwright/zhiyu-20261006/`。口令仅保存在各轮次已忽略的 `environment.private.json`，不在本记录中公开。

## P0—P4 实现

P0：登记 AGENTS §1.7、修订三及索引。实施前后 HEAD 均 `4ccf84e973978482a1098d18c69fbfc9f011fac6`；大量既有修改/未跟踪成果全部保留。没有回退、批量删除、迁移正式库、清库、改旧哈希、创建提交或关闭原 FULL 项。原正式计数21/92、FULL67项保留原状态，本轮不代表两版全量验收。

P1：知余独立入口提供总览、我的规则、目标与执行、活动记录四页。保留完整工程入口与内部历史标识。离线模板为“保留3000元应急金”和“旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。”；没有接外部大模型。所有银行和资金均为模拟。

P2：复用原规则编译/确认、单目标规划、权限、整数分、账本、模拟银行与执行服务。创建目标时零归属；固定200000分收入实际到账且晚于旅行权限生效。浏览器不提供金融金额、时钟、成功事实或回执。准备及执行基于当时事实重检，最终实际执行100000分。

P3：写前保存原定位信息，独立新 state 回读核对环境/epoch/action_id/effect_hash/用户；UNKNOWN 与未决原件阻挡冲突写入。模拟银行实际提交后才注入响应丢失；UNKNOWN 恢复使用同一原ID，且不再次注入。撤销拒绝说明复用原 hash 校验 `DEMO_EVENT_INTENT`，不授予权限，也不冒称独立不可修改审计事件。

隔离工厂 `app.zhiyu_main:create_zhiyu_app --factory` 复用原初始化，在实际请求及 OpenAPI 中应用方法/路径白名单；高级路径404。专用 GET 强制 RR/READ ONLY，state 使用同请求既有 `historical_ledger_scope`，没有跨请求授权缓存。启动包装仅创建随机精确库与专属低权限用户，核对回环54329端口/准确名称；旧轮次、服务、失败原件全部保留，端口占用时拒绝。

P4：代码审查/自动检查、真实 PostgreSQL 金融风险检查、Edge 两组 A/B/C、独立数据库核验、截图、实际时延和文档已完成。真人代码审查及真人讲稿计时未开展。

主要新增代码为 `apps/web/zhiyu.html`、`src/zhiyu-main.tsx`、`src/zhiyu/`、`vite.zhiyu.config.ts`，`app/zhiyu_main.py`、`app/zhiyu_isolation.py`、`api/v1/zhiyu.py`、`test_zhiyu_demo.py`、`scripts/zhiyu_demo.py`。修改共享依赖仅为专用GET只读接线与品牌/入口/构建配置；不能把整个既有 Git diff 算作本轮修改。

## 检查与原件

| 检查 | 实际终态 | 证据 |
|---|---|---|
| 实际金融链、隔离门与历史读取风险 | 最终13 PASS，308.37s；GET read_only=on/POST=off；A/B/C、重复请求至多一次与历史原件完整性 | `financial-06.log` |
| 修复前后完整金融补验 | 6 PASS/248.16s；6 PASS/244.19s | `financial-04.log`、`financial-05.log` |
| 未来收入不能扩今日额度 | 4 PASS/0.83s | `future-income.log` |
| 权限/银行命令/回执原件直接风险 | 18 PASS/1.49s | `direct-risks.log` |
| 后端严格类型与静态 | 新三源strict PASS；最终Ruff PASS | `backend-types-02.log`、`backend-static-final.log` |
| 实际合同 | 原全应用OpenAPI及共享TS生成PASS；专用实时18路径白名单、3禁用路径404、额外query及4类金额/时钟/伪回执输入422 | `contracts-03.log`、`read-timings-final.json` |
| 前端恢复/定位/组件 | 3文件13/13 PASS，3.60s；原品牌定向1 PASS | 代理的实际终端结果，未另存独立日志；不作真实银行证明 |
| 最终构建 | 全域tsc PASS；专用Vite86模块PASS，1.26s | `web-build-final.log`及tsc终端退出0 |
| 最终全域前端lint | `eslint . --max-warnings 0` PASS | `web-lint-final-02.log` |

可复核命令（仓库根 `.venv` / Web目录已安装Node）：

```text
python -m pytest apps/api/app/tests/test_zhiyu_demo.py apps/api/app/tests/test_historical_read.py
python -m mypy --strict apps/api/app/zhiyu_main.py apps/api/app/zhiyu_isolation.py apps/api/app/api/v1/zhiyu.py
python -m ruff check apps/api/app/zhiyu_main.py apps/api/app/zhiyu_isolation.py apps/api/app/api/v1/zhiyu.py apps/api/app/api/dependencies.py scripts/zhiyu_demo.py
node node_modules/vitest/vitest.mjs run src/zhiyu/operation.test.ts src/zhiyu/api.test.ts src/zhiyu/ZhiyuApp.test.tsx
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build --config vite.zhiyu.config.ts
node node_modules/eslint/bin/eslint.js . --max-warnings 0
```

未运行或宣称原92项全量 make check。失败01—03（路由包装兼容、原收入幂等规范键、模拟响应超时处理）、初次合同/构建失败及默认沙箱Node/esbuild EPERM原件保留，修后覆盖对应实际链路。最终全域lint首次扫描了新生成的压缩产物，原1036条产物错误日志保留；仅在eslint配置中补 `dist-zhiyu/**`，未排除真实依赖，随后全域通过。pytest缓存权限及FastAPI/httpx弃用警告保留。

## 两组真实 Edge 演示

专属 Microsoft Edge 会话 `zhiyu20261006`，真实API、PostgreSQL及模拟银行；全部操作通过页面控件。每组采用两库分别运行A+B与C，避免同一月额度冲突，未用新轮次掩盖或替代UNKNOWN恢复。

| 组/场景 | 轮次（API/Web） | 原动作与实际结果 |
|---|---|---|
| 第1组 A+B（AB2） | `20261006T065950Z-f8d3a56cd29c4d26a92ddf92e2febeb0`（19003/19176） | `c032882c-0382-4c24-92a1-318b57016c93` SUCCEEDED；回执 `c66ff1fb-037b-5785-acd0-afd405ce5b88`，100000分；撤销后409拒绝 |
| 第1组 C（C1） | `20261006T065150Z-a87939e8864949c690f95cd51e29f7da`（19002/19175） | `df98cc52-daca-4a14-93b3-8233978d352e` UNKNOWN→刷新仍UNKNOWN→原ID恢复 SUCCEEDED；回执 `182a8755-a010-549f-aa18-9f9b9f45f86f`，100000分 |
| 第2组 A+B（AB3） | `20261006T071848Z-b43172f572614275968085edd60b57ed`（19005/19178） | `d1b9da6d-8588-4298-9d48-11bdab57c13a` SUCCEEDED；回执 `a9dde84e-b495-5faa-ae48-2d9d7bfa4f3c`，100000分；撤销后409拒绝 |
| 第2组 C（C2） | `20261006T070638Z-50f238f24965450784decab6432547ed`（19004/19177） | `e214e423-a8bd-40ae-8d9d-ba70e8713fab` UNKNOWN→刷新仍UNKNOWN→原ID恢复 SUCCEEDED；回执 `8fb00297-fbc1-5e87-8812-675447b1e5e0`，100000分 |

原 `edge-{标签}-{setup|execute|recover|refuse}.log` 保存响应、action_id、完整effect_hash、回执与耗时；C1/C2恢复前后的ID/hash一致。截图按实际场景为 `{标签}-prepared/success/refused/unknown/unknown-after-refresh/recovered.png`。试用总览为 `READY-overview.png`。这些是浏览器真实截图，已查看总览和拒绝记录，没有用生成图替代。

独立只读结果：

- `isolation-C1-unknown.json`：银行SETTLED/5条posting，应用尚无回执、目标零归属。`isolation-C1-final.json` 13项PASS，恢复前后原银行操作及5posting字节不变，应用回执0→1/目标0→100000分，没有新增银行扣款。
- `isolation-AB2-final.json`、`isolation-C2-final.json` 各15项PASS；`isolation-AB3-final.json` 16项PASS。准确DB/低权限owner、各1银行操作/1回执、goal100000/income200000分、5原posting匹配、资金守恒、审计链VALID及subject snapshot原哈希有效。
- AB3撤销/拒绝前后八金融表（账户、交易、目标、预留、银行操作、银行posting、回执、收入事实）逐表完整字段摘要全部一致；整体SHA256均 `5a2c1598c50dea7db5da7bd9aae173b643b2616c2dc39475f1507bed39573e9e`，原JSON `AB3-before-B.json` / `AB3-after-B.json` 保留。策略和拒绝说明正常变化，不属于上述无经济效果比较。
- AB2没有独立前后全表摘要，拒绝无新增经济效果据当前唯一经济操作和原5posting核验；C2没有独立UNKNOWN数据库快照，UNKNOWN据真实Edge原响应日志，不冒称存在这两类原件。

更早AB1诊断（19001/19174）仍保留：安全成功后撤销拒绝，六类金融表前后SHA256相同 `bcc15947638b4ccc8a25cb0954a9462b30f8c5e852bd9a23b8e0fc7e33495bf2`；见 `B1-before.sha256` / `B1-after.sha256`。初始未执行的cc002诊断也保留。两者不替代上述最终源码两组。

## 性能实测与剩余缺口

单位毫秒。HTTP含响应下载/解析；可见时间从点击到原结果及独立回读显示，部分测量末尾包含截图。测试总时间不作交互速度证明。

| 场景 | 准备HTTP | 执行HTTP / 结果可见 | 原操作恢复HTTP / 结果可见 |
|---|---:|---:|---:|
| C1 | 4788 | 6269 / 16507（UNKNOWN） | 8493 / 19401；刷新+读取+恢复全程42614 |
| AB2 | 8986 | 14737 / 43096（成功） | 不适用；撤销确认→拒绝记录可见27169 |
| C2 | 9697 | 19707 / 49004（UNKNOWN） | 8513 / 19329；刷新+读取+恢复全程75311 |
| AB3 | 7659 | 15941 / 37278（成功） | 不适用；撤销确认→拒绝记录可见51715 |

最终AB3固定数据每阶段5次读取（`read-timings-final.json`，HTTP+JSON，不含浏览器）：

| 阶段 | state每次 | 单目标preview每次 |
|---|---|---|
| 准备完成/执行前 | 10424.176、9543.185、11269.643、11066.192、10624.457 | 479.124、402.134、521.564、312.013、930.414 |
| 成功执行后 | 21716.892、19722.066、21709.362、21374.381、20745.853 | 547.527、394.787、363.961、305.528、301.714 |

每阶段实际账户/目标/原动作/回执投影5次一致；JSON解析最多13.619ms。早期动作前state约0.85—1.08秒、preview约0.43—0.69秒的5次诊断仍见 `read-timings-01.json`，不能由早期小状态推导执行后state也<2秒。

初始试用新来源第一次访问至可用3460ms，随后5次同来源刷新889/699/568/744/751ms，均真实HTTP200。定义为服务已就绪、现有专属Edge首次访问新origin及刷新；可用要求真实state、额度标题、刷新按钮可用、无等待/错误提示，不含Edge进程启动。原件 `edge-ready-open-timings.log`。原测量的单数goal_present字段不作目标证据，另用真实 `goals[]` 独立核验为0，见 `ready-state-check.json`。

**3秒首次总览目标本次样本未达到（3.46秒）；5秒执行目标未达到（成功可见约37—43秒）；整页state通常2秒目标未达到。** 单目标preview本次10次均<1秒；等待期间显示处理状态；UNKNOWN明确可查询/恢复，未按定时器判成功。5—8分钟讲稿已按真实控件走通两组，但真人讲解计时未验证。

已窄修专用GET RR/READ ONLY、同请求既有账本作用域、取消额外动作GET、按需读取，未跨请求缓存授权或跳过原件检查。只读审查确认state还有嵌套账本/审计重复校验；简单增加audit scope未证实能解决此链路，未改共享金融核验。下一性能工作先按实际固定数据剖析执行/审计/读回，再评估同请求去重；必须保留用户/事务/嵌套事务/异常隔离与非头原字节篡改测试。性能与现场讲解门未通过，不宣称已达到现场交互要求。

## ZY-D01—08 与下一步

| 要求 | 状态 | 实测与边界 |
|---|---|---|
| ZY-D01 总览 | IMPLEMENTED | 原读数与限制已核对；首次3秒与全状态2秒目标未达 |
| ZY-D02 规则候选/确认 | VERIFIED | 两模板真实候选→明确确认；候选不授执行权限 |
| ZY-D03 单目标执行 | IMPLEMENTED | 两组真回执、保护/守恒/一次效果通过；5秒目标未达 |
| ZY-D04 拒绝/UNKNOWN | VERIFIED | 两次撤销拒绝；两次UNKNOWN刷新、同ID恢复；AB3无经济变化完整摘要 |
| ZY-D05 活动解释 | VERIFIED | 实际规则/收入/目标/执行/拒绝及原件解释核对 |
| ZY-D06 隔离/启动 | VERIFIED | 精确门、实际低权限库与API/代理就绪；新轮次保留历史 |
| ZY-D07 品牌/讲稿 | IMPLEMENTED | 四页品牌、讲稿与截图已交付；真人5—8分钟计时未验证 |
| ZY-D08 验收交付 | IMPLEMENTED | 必需功能/风险/两组Edge通过，缺口已登记；性能与真人讲解仍待优化/验收 |

范围外：联合目标、多来源、多资产梯度、有损支取、到期再配置、季节/全年规划、完整网络故障矩阵、实验平台、离线搬运、外部LLM、真实银行与真实资金。没有将这版三场景结论推广到未选FULL；没有真人审查者。

交接：首版可从上方试用链接使用，当前初始轮次无未决原动作。后续唯一重点为本链性能定位与讲稿计时，预计另安排1—2小时定位/窄修与相关重验；超过方案60—90分钟优化预算时登记具体缺口并重新估时，不恢复全92项范围，不以降低金融真实性换速度。不要重跑正式seed/reset、清理旧库/服务或删除浏览器未决定位信息。所有7套开发/验收轮次及失败原件保留；当前19000—19006/19173—19179均为本轮保留服务，新增轮次先检查空闲端口。未做部署或提交。禁止LibreOffice；若后续额度中断，先更新本文件当前原动作、进程、验证终态与准确下一条命令。
