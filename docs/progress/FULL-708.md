# FULL-708 移动端与无障碍：策略中心增量

2026-10-05。状态：`POLICY_CENTER_INCREMENT_IMPLEMENTED / PRODUCT_ACCEPTANCE_PENDING`。原FULL-708仍为 PENDING。

原要求见 `钱途有界_完整开发计划_Codex执行版.md:1392–1394`：关键流程手机可读，重要解释不用宽表；原追踪要求手机视口、键盘、标签、焦点、对比度和核心浏览器实测。

本次策略地图在窄屏按节点、详情顺序单列；筛选、版本、关系均使用有名称的原生输入/按钮，并以 aria-pressed 表达选择。地图选择会将焦点移到所选策略详情，打开当前编辑器会移动焦点；关闭编辑返回仍存在的原入口，输入中的重新渲染不会抢走焦点。保留原编辑器所有确认要求。

修改影响的重要解释从宽表改为逐项定义列表；核验状态、安全闲置、含负最小余量、资金缺口均各自展示“修改前/假设修改后”，手机单列且不删字段。策略中心追加最小按钮尺寸、换行、可见焦点及更清楚的说明色彩，不修改其他页面行为。

改动和实际命令、原日志见 `FULL-703.md`。最终类型与相关ESLint退出0；页面原4测试加1焦点/比较测试已通过，地图/模型最终9项通过。夹具只证明组件行为；没有调用真实浏览器、DB、金融接口或全量检查。

未覆盖：实际320/375/手机视口、触屏、屏幕阅读器、真实键盘全路径、整体视觉/对比度测量及其他产品页面均未验收。现有App导航、全局路由焦点由父任务负责，本次不声明全产品无障碍完成。下一步在最后冻结源上进行核心流程浏览器与人工可读性审查，结果不足继续保留缺项。

无迁移或种子变化。原源与失败证据保留，本批不关闭需求。

## 2026-10-06 通用手机阅读与原房租解释增量

状态：`MOBILE_STYLE_AND_RENT_EXPLANATION_IMPLEMENTED / CSS_COMPILED / TARGETED_HTTP_FIXTURE_PASSED / DEVICE_AND_ACCESSIBILITY_ACCEPTANCE_NOT_RUN`。原上段记录与证据保留，FULL-708继续PENDING。

本包只修改 `apps/web/src/styles.css`、`pages/DemoConsolePage.tsx` 和该页面的一项既有直接测试；App、FullPoliciesPanel、恢复宿主、FULL604八个冻结前端源、Main/Schema和金融源由Root持有，不属于本包。

集中样式原内容不删，末尾追加规则：15项导航可换行，760px以下按可伸缩列排列；Header/页面标题可换行；关键定义列表和原动作身份在窄屏单列；长UUID/hash/原JSON可换行，不截掉原文本或金额。按钮、summary、主要输入/select有44px最小高度，checkbox/radio原标签有可点区域；窄屏表单使用16px输入文字，关键说明至少14px，文本行距增加。焦点有深色outline及浅色分隔，状态仍有原文字而非仅颜色。CSS reduced-motion规则只覆盖CSS动画/滚动，不宣称已改所有显式JavaScript smooth调用。

房租91日修改预览原宽表改为有名称的区域和定义列表，每项明确“修改前”“假设修改后”：核验状态、安全闲置、含负数的最小余量保留，另外展示原DTO已经返回的资金缺口。手机单列能读完关键比较；null仍“待核验”，负数不夹成0。配置/原版本/hash、明确checkbox、POST/PATCH/key、断网同请求恢复和所有handler均保持原内容，没有新确认、金额计算或授权。其他当前关键页面已有定义列表和年度日期/三阶段文本读数，未以图形替代原精确值。

### 宿主接口

Root可用 `.skip-link` 作为跳过15项导航的链接，焦点时显露；当前路由内容容器使用 `className="page-content" id="main-content" tabIndex={-1}`，CSS支持可见焦点和scroll margin。hash业务路由变化时由Root显式聚焦内容；skip link需拦截默认hash路由并聚焦容器，避免 `#main-content` 被业务router识别成另一页面。初始加载不应抢走用户正在输入的身份字段。本包仅交付样式接口，不冒称Root宿主已接或真实键盘路径已跑。

### 实际最小检查

- 原房租完整配置→服务端比较→明确确认→丢响应保同键/PATCH原payload用例：**1 PASS / 11 targeted skipped**，Vitest2.93s、wrapper4.053928s；`docs/progress/evidence/W6/full708-mobile-rent-original-comparison-direct-20261006T001544Z-599d910b/manifest.json`。追加断言核无宽表、四项before/after标签、原负数和null文本；原资金handler/请求一致性断言均保留。此HTTP夹具不是手机、浏览器或银行证明。
- 两个修改TS源的现有ESLint：exit0，wrapper2.977657s；`full708-mobile-markup-local-lint-20261006T001541Z-992817e6`。
- 现有Vite7.3.6/Tailwind真实构建编译CSS及markup：exit0、Vite7.33s；`full708-mobile-css-production-bundle-20261006T001608Z-66735a33`。产物位于新 `.runtime/FULL-708-mobile-css-build-20261006T0016Z/assets/`，没有清空或覆盖旧dist/源码；保留原“目录在Web根外不会自动清空”提示。保留实际单JS chunk大于500kB提示（约1.03MB），本包没有静默调高告警阈值或冒称加载性能已验。构建没有代替TypeScript或手机布局实测。

具体命令：

```text
node apps/web/node_modules/vitest/vitest.mjs run --root apps/web --config vitest.config.ts src/pages/DemoConsolePage.test.tsx -t 房租完整配置
node apps/web/node_modules/eslint/bin/eslint.js --config apps/web/eslint.config.js apps/web/src/pages/DemoConsolePage.tsx apps/web/src/pages/DemoConsolePage.test.tsx --max-warnings 0
node apps/web/node_modules/vite/bin/vite.js build apps/web --config apps/web/vite.config.ts --outDir ../../.runtime/FULL-708-mobile-css-build-20261006T0016Z/assets
```

原源完整bytes/SHA先保存于 `.runtime/FULL-708-mobile-before-20261006T001501Z/`，包括此前样式/页面/测试和本文原内容，不用HEAD旧基线覆盖后续成果。上述三个检查均由原 `run_scoped_check.py` 捕获command/exit/log/source before-after；具体稳定性以各原manifest字段为准，不把并行宿主变化算成此模块冻结。整体Web类型由Root宿主集中进行，本包不重复跑whole types。

直接用例与局部lint的 scoped/all source stable 均 true；CSS构建 scoped stable=true/all stable=false，唯一并行变化为其它负责人新增 `apps/api/app/tests/test_full_action_set_asset_producers_integration.py`。该新候选不属于本包或已运行检查，未据此声称全仓冻结。

### 具体缺口

320/375/390px实际视口、200%/400%缩放、触屏目标实测、真实Tab/Shift+Tab/Enter/Space全路径、焦点是否可被遮挡/误移、屏幕阅读器和动态通知报读、所有输入标签/关联错误全路径、实际背景/opacity上的对比度均 **NOT_RUN**。深色文本和outline只是已实现样式，不声明WCAG级别或全产品通过。`body`沿用原320px下界，更窄或高倍率视口仍须实测；CSS `:has()`和native summary在目标浏览器的实际布局/可操作性未取得本包证明。

App跳过导航/路由焦点由Root随后接；原页内programmatic focus与部分JavaScript smooth行为未本批更改，必要 reduced-motion缺口仍在。既有年度曲线有原精确日/阶段读数，地图有原节点/关系文字，但完整图形替代说明和所有产品页面人工可读性没有本批新增验收。全部金融操作仍模拟，未运行金融、正式reset、浏览器、DB、依赖安装或全量。下一前置是在最终当前源码上集中实际核心流程手机/键盘/对比度及人工阅读审查，缺证据不得关闭FULL708。


## 2026-10-06 08:26 Root 接缝与实际证据

2026-10-06 08:26 北京时间：604 前端41直接风险/API8源和Root真实当前策略宿主均已交付；原pending与PLANNED/UNKNOWN工作区跨页阻新写/演示reset/登出，列表读取失败仍有独立原GET。Root最初18相关PASS9.80s，纯账户摘要夹具types FAILED2原件留存；窄补实际DTO后受影响5PASS8.63s、整体Webtypes b9f79960和5文件lint7c79cf22 PASS。Root五源FINAL 4bb74a52，708集中样式和房租窄屏说明已FINAL55288bc4，实际CSS/Vite build通过；Root跳导航不改hash、路由改变focus当前内容三case内已核；真实手机/键盘/屏幕阅读器/对比度仍NOT_RUN。

0014实际migration首命名约定重复prefixERROR8.47s保留，两个drop仅op.f窄修后actual1PASS11.26s，FINAL683e4ef7；旧0012/0013/已有行/哈希不改。204v1 actual原节点FAILED13s，后诊断FAILED16.34/14.72s均留；根因实际无 simulated_bank_ledger_heads 表、证据200行截断漏refs、512KiB不足。v1/已冻结Full-v1继续UNKNOWN，不能造表别名或提高旧版本容量改历史。独立显式 actual-v2使用真实表/全counts/现原schema type进行修订，307+真实asset family/math接新版本，原FAIL不改。后Full-v1与HTTP自动通知节点仍NOT_RUN。

307 actual原preview409 DYNAMIC_GOAL_V2_LEDGER_REQUIRED，FAILED12.91s；实际诊断FAILED13.49s证明原nativeV2唯一差异as_of `+00:00`与typed输出`Z`，同一时点。Root仅改新动态消费者及新历史验证接缝为严格原生IncomeLedger解析后全值相等；原JSON、原source hash、所有整数/源身份/归属/预约/权限/clock门和旧nativeV1拒绝不改，不写规范化原件。新增7反例和原35direct/4strict正在跑；准备后的真实银行/原键恢复仍未得结果，105/604后两节点NOT_RUN。当前无Root金融RUNNING，唯一shared财务负责人仍Root；新v2/102/材料独立并行。正式关闭21/92/FULL原项PENDING、真人0/NOT_STARTED、新性能NOT_MEASURED，最后集中验收尚待。

