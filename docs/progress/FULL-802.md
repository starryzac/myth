# FULL-802：完整候选防越权增量

状态：新增FULL自然候选/可选provider入口的严格来源与Schema边界已实现，71个直接纯/FastAPI夹具检查通过；原FULL-802继续PENDING。生产路径与原规范引用、逐例检查和限制见FULL-106-natural-compiler.md。

默认离线provider关闭，无外部网络实现。server-only注入provider最多处理已识别且脱敏的原句片段及日期/时区；模型不能供应角色、资金事实、权限状态、金额新值或成功结果。输出精确封套+64KiB/4096nodes/depth16独立JSON门、原12 strict Schema、原引用token和原规则canonical一致性全部要求；额外字段、结构绕过、bool/string/float金额、NaN/inf、无来源填值及不同金额/日期/账户都不能给可用configuration/hash。原CONFIRMED/ACTIVE/银行权限只能来自后续真实用户确认/执行链，不由这预览写入。

旧MVP compiler/数据库/审计/hash/所有原失败和篡改负例未改。本包没有全量、安全审计、真实provider、真实Cookie/真人、PG或最终浏览器证据；有限PII模式不是通用识别器，未知散文完全不发送provider。下一Root注册+Schema后交实际UI消费与完整原确认链，最终集中验收前不得关闭原需求。
