# FULL-106：有限自然规则完整候选编译

状态：独立生产 domain/service/API 已实现，模块检查通过；原 FULL-106 继续 PENDING。对应原完整版计划 6.2/6.3（352–388）及 FULL-106（1196–1198）。本批不是任意自然语言理解、原策略确认、实际资金执行或最终验收。

## 能力与接口

- `domain/full_policy_compiler.py::compile_full_policy(text, CompileContext)`：十二个原 FULL 模板的明确受控中文句式，独立调用原 `validate_full_configuration`，不改变原 DSL、旧编译器或哈希算法。
- `services/full_policy_compilation.py::preview_full_policy_candidate(User, aware server now, FullCompilationRequest, server runtime)`：从当前实际模拟 User 及其时区获得服务器本地日期；不接收客户端用户、角色、金额结果、金融事实或时钟；无数据库写。
- `POST /api/v1/full-policy-compilations/preview`：严格 `{text, engine='rules', comparison_candidate?}`。comparison 精确 `{template_name,configuration}`，仅用户提供候选比较，不冒充当前策略版本；独立完整Schema验证后显示逐字段变化。
- `GET /api/v1/full-policy-compilations/grammar`：返回实际十二模板受控句式及有限范围。示例 UUID/金额仅合成文本，不是已存在账户、产品、策略、授权或实测结果。
- API reader结果包含实际服务端 user/date/timezone、原全文 UTF8 SHA、脱敏原句、原片段 start/end/raw SHA、draft、缺失/歧义/unknown、规范候选/hash、默认字段、可读金额/日期摘要及差异。

每个输出固定 requires_confirmation=true、grants_authority/bank_authority/policy_created=false，reference_validation=NOT_SERVER_VERIFIED。候选没有状态变更、确认记录、专用审计事件、余额或银行接口。原引用必须由后续已有真实预览/生命周期重新验真；不将字段存在当作当前授权。

## 明确支持与缺口

十二模板句式由实际 GET grammar 给出：周期义务、生活储备、应急缓冲、日期支出、长期目标、定期转账、资产配置、回撤规则、目标分配、跨目标应急调拨、节日储备建议和介入规则。

金额支持明确阿拉伯十进制元/分/万元，按Decimal转换整数分，不取整，不能超signed64。日期只支持明确YYYY-MM-DD，不猜“明年/过几天/节日前”；过期deadline/valid_until/window拒可用候选。自然月为显式1–31日，准备天数、月min/target/max、scope、源UUID、收款人符号及资产期限必须明确。列表用顿号；未知/未解释片段不会被忽略，同字段不同声明和多个模板保持AMBIGUOUS。不提供任意中文数词、自由长段混合多意图、自动实体别名解析、实际历史样本/到期/已付/拥有权推定或银行新收款人创建。

模板原有安全默认值可显式归一化，defaulted_fields列出未声明字段；LivingReserve的rolling_window_quantile是固定模板算法，编译不计算历史储备额。Seasonal仅建议且需确认，不因candidate产生实际调整。CrossGoal启用句式需原完整dates/sourceIDs/caps/triggers，通过Schema仍不是专用执行授权。Intervention保留全部mandatory询问，不允许自然句式删除安全问题。

## 可选provider与来源

默认server runtime `llm_enabled=false, provider=None`。rules模式不调用provider；客户端不能设置开关、provider/network配置。尚无任何外部模型SDK/网络实现或真实模型请求。

server显式注入的provider只收到已被规则识别的原片段以及CompileContext的服务器日期/时区，不传未知原文、User ID、交易、余额或账户上下文。UUID、收款人符号、策略显示名称变成本地源令牌；Email、连续11–19位手机号/账号/身份证、显式姓名/地址/密码字段脱敏。未知散文完全排除，因此不把有限regex冒充通用PII识别器。令牌映射仅server本地保留。输出引用只能在精确引用slot恢复原token；不能改成任意UUID或其他收款人。

provider输出必须是精确 `{template_name,configuration}` 有限JSON（64KiB/4096nodes/depth16）并重新独立通过十二模板；非JSON、NaN/inf、额外权限/状态字段、bool/string/float金额拒绝。完整可用模型candidate必须与独立原句规范候选完全一致。缺原金额/日期/scope不得让模型补造；冲突只显示REVIEW_REQUIRED及逐字段差异，不返回可用configuration/hash。provider异常公开错误隐藏敏感底层cause，原编译不落部分写。用户若选择改变候选，仍必须走原完整校验/预览与明确新确认，不能从模型输出直接ACTIVE。

## 已运行检查

最新源码：两个直接文件71 PASS / 2.68s，独立5文件strict mypy、Ruff及format PASS。原件均位于 `docs/progress/evidence/W5/`：

- `full-natural-compiler-summary-final-direct-20261005T220252Z-3775021e`
- `full-natural-compiler-summary-final-types-20261005T220252Z-bbac3382`
- `full-natural-compiler-summary-final-static-20261005T220253Z-8097e1a8`
- `full-natural-compiler-summary-final-format-20261005T220253Z-4cbdc9d6`

包含12真实Schema受控输入、整数金额/原JSON、缺失/否定/多意图/相对日期、PII/provider最少上下文、输出越权/结构/容量/引用切换/无来源填值、规则冲突diff和实际FastAPI JSON/禁止query/客户端role/bank/time/config字段。FastAPI User dependency为明确synthetic fixture，不是PG当前User或银行证据。原Starlette TestClient弃用警告及缓存目录拒绝warning保留，未为本包安装依赖或改变检查门。

首次65 direct PASS后，类型一处optional模板RED、静态47项RED原目录及源码在 `.runtime/FULL-106-802-first-candidate-retained-20261005T215555Z-45b67716` 保留；窄修类型和格式后的PASS是新原件，未改原失败。

## 下一接线

Root注册router、实际Main OpenAPI/Schema与readonly POST；独立新Web候选编辑组件只将经过实际重新校验的完整候选回调到现CreateEditor draft，不自动提交确认。持久自然原文/模型/人工修订链、本包新12模板候选进入不同原确认入口的完整产品浏览器与真实User/source验证、真实provider隐私评审和最终FULL验收未取得。没有迁移或新数据库表，旧MVP编译持久化完整保留。

## 当前Main与真实合同接线（2026-10-06 06:25）

Root已注册 full_policy_compilation router，精确POST /api/v1/full-policy-compilations/preview 接干净RRRO；grammar为无数据库目录。实际Main OpenAPI/TypeScript已生成：W6/actual-registered-full-compiler-605-openapi-20261005T222246Z-9b06a429 子命令exit0，wrapper SOURCE_CHANGED是刻意更新contracts的输出变动，未伪称冻结通过。随后 --check 在 W6/actual-registered-full-compiler-openapi-check-20261005T222445Z-54e84dc3 exit0/PASSED，合同匹配当前API。FullCompilationResponse/FullGrammarResponse aliases已实在schema，前端独立消费继续。此为实际注册/合同，不是PG、模型联网、持久候选来源receipt或用户确认验收。
