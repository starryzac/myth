# FULL-106 / FULL-802 自然候选消费界面

状态：**MODULE_UI_DELIVERED / HOST_CALLBACK_PENDING / BROWSER_NOT_RUN**。后端五源已冻结，Root已注册实际路由/只读preview并生成真实OpenAPI类型；新独立前端五源已实现。没有自然候选金融确认或浏览器实证，编号未关闭。

## 产品接缝

新增独立 `FullPolicyCompilerPanel`，不修改现有 `FullPoliciesPanel` 或 CreateEditor。用户手动输入原句后请求 `/api/v1/full-policy-compilations/preview`；读取 `/grammar` 得到服务器当前有限句式目录。目录示例明确属于合成说明，日期和 UUID 不当作用户当前事实。

实际 props：`mutationBlocked?: boolean`，`userId?: string`（提供时严格匹配服务原user），`onCandidate?: ({templateName, configuration, configurationHash}) => void`。回调只填入父工作区的待验证草稿，不能提交创建、确认、激活或执行命令。父工作区仍需要其原服务器校验、明确 accepted、理由及持久原键链。当前父页接线由Root负责，本包没有修改CreateEditor。

## 复核与失效规则

- 展示服务器返回的候选状态、原句片段、缺失/歧义、schema 默认字段、差异解释及未验真引用。
- 只有完整服务器候选才能进入编辑；手动编辑需要现有 `/policy-templates/validate` 严格校验后才能回调。编辑内容改变即清除旧校验与 hash。
- 原句改变、重编译或请求失败即清除旧候选；晚到的旧请求响应不能覆盖新原句。没有自动重试或自动套用。
- provider 默认关闭；界面默认规则模式，不接受客户端网络配置、角色、银行事实或时钟。
- 响应必须保留 simulation、无权限、需复核、引用未验真语义。不能把 READY_FOR_REVIEW 显示为已确认。
- 金额只能以安全整数分读取，bool、UUID、日期与原片段位置严格校验。Python 原片段偏移是 Unicode 码点位置，不能使用 JavaScript UTF-16 下标替换。

## 文件与检查

新增 `api/full-policy-compilation.ts`/测试、`components/FullPolicyCompilerPanel.tsx`/测试、`tests/full-policy-compiler-fixture.ts`，均位于`apps/web/src`。reader使用实际generated aliases，对服务器十二目录、原请求文本UTF8摘要/Unicode码点片段摘要、严格整数金额、日期/UUID/bool和无权限flags验真；保留原JSON。配置含分位浮点，不复制只支持整数的银行hash算法；最终编辑使用原服务器严格模板校验的规范配置/hash。

命令通过现有`run_scoped_check.py --task W5`绑定五源：Node Vitest只运行新增两个测试文件；TypeScript用只包含五源及其真实imports的模块配置；ESLint只运行五源。没有全量检查、PG或浏览器。

- `full-natural-compiler-ui-first-interactions-20261005T222617Z-8a04e851`：18 PASS/4.38s（wrapper5.539055s），两测试文件；scoped稳定true、global=false，仅其他代理三个dynamic-goal源变化。
- `full-natural-compiler-ui-first-types-20261005T222610Z-dbf04ceb`：FAILED exit2，生成句式index signature允许undefined。原五源/日志精确保存 `.runtime/FULL-106-ui-first-type-red-retained-20261005T222646Z-84a57e8a`，未重标成功。
- 窄修只增强已通过十二键验证的`FullGrammar`类型表示，运行代码未变；18行为结果复用，没有重复运行。
- `full-natural-compiler-ui-type-only-repaired-20261005T222704Z-34f67ce3`：五源类型PASS，scoped/global均true。
- `full-natural-compiler-ui-final-static-20261005T222704Z-9e510bac`：五源ESLint PASS，scoped/global均true。

Root实际合同检查 `W6/actual-registered-full-compiler-openapi-check-20261005T222445Z-54e84dc3` PASS，前端没有手写假Schema。后端证据位于 `.runtime/FULL-106-802-natural-compiler-final-20261005T220440Z-dc69bbdc/manifest.json`（71直接模块/HTTP DTO测试），同样不包含PG、外网模型或真人研究。所有UI fetch与候选为明确synthetic测试，不能作为用户成功或真实金融效果。

## 未覆盖与下一依赖

父页候选回调与后续原确认由Root接；需要实际完整复核/accepted/reason/原键链。十二受控句式之外的自由中文仍UNKNOWN/MISSING；账户/payee/Goal引用仍待当前事实验证，模型默认关闭。没有外网模型、真实完整编译→确认链、浏览器或用户研究实证，不因此关闭FULL-106/802。

## 2026-10-06 原创建宿主接线

Root 在原 CreateEditor 中显式展开编译器；只有手动采纳才填入模板和草稿，清除旧候选、接受勾选和理由。采纳后必须再次原 server validate，再由用户明确创建确认。disabled/busy 阻止展开和采纳。原 hash 不沿用为创建证据。12 相关 Vitest PASS/6.16s（2 新宿主风险和10原父组件，有重叠不与旧批相加），整体Web类型与2源ESLint通过。命令原件 W6/full-compiler-existing-parent-draft-risk-20261005T223205Z-d8419882、types-20261005T223208Z-8f0dfad7、static-20261005T223209Z-174d7eee。精确2源 FINAL .runtime/root-full-compiler-host-final-20261005T2240Z/manifest.json；浏览器/真人/新功能性能未测，FULL仍PENDING。
