# FULL-104 当前依赖读取差量

状态：IMPLEMENTED_DIRECT_CHECKED / ACTUAL_PG_NOT_RUN / FULL_ACCEPTANCE_PENDING。

原需求为完整策略不可变版本及依赖生命周期（原计划 298–318、1188–1190）；当前已有真实生命周期、确认、历史及部分动作重查。本差量新增当前 OPEN 周期、完整所属用户分母的只读依赖图，展示确认时引用与实际当前引用及有效状态变化，精确列出声明闭环并保持仅需复核的语义。

新增 domain/service/API 和四直接测试文件。接口及金融边界见 `docs/spec/full-policy-dependencies-contract.md`。不修改原生命周期、共享模型、审计 canonical、旧历史或策略摘要，不执行金融动作。

## 已验证

- 最终 33 直接检查：`docs/progress/evidence/W2/full-policy-dependency-effective-final-direct-20261006T053225Z-91607976`，4.53s。涵盖完整分母、周期/所属用户、原配置摘要、缺引用、循环、漂移、到期 MVP 原件和无权限 HTTP 门。
- 最终七文件类型、Ruff、格式的原件分别位于 `full-policy-dependency-type-only-final-types-20261006T053359Z-13df27dd` / `final-static-20261006T053359Z-d070c3d6` / `final-format-20261006T053359Z-a354128c`。各终态 PASSED，scope 均稳定。
- 唯一 PG 候选仅收集：`full-policy-dependency-pg-candidate-collection-20261006T052705Z-949daead`。未执行 PG、浏览器、全量或银行动作。
- 首轮类型/静态失败原件及当时六源码保留在 `.runtime/full-policy-dependencies-first-static-type-red-20261006T0524Z`；原 31 PASS 不替换为新轮结果。修复为类型声明、确定性 cycle closure 参数及严格 zip 语义；随后单独追加 MVP 到期状态能力与两个风险反例。
- 新 MVP 状态反例首类型失败（混合字典被推为 Collection[str]）保留在 `full-policy-dependency-effective-final-types-20261006T053225Z-60fc19a3` 及 `.runtime/full-policy-dependencies-effective-type-red-20261006T0534Z`。随后仅补测试字典类型声明；33 个运行行为未改，不重复无差量行为检查。
- 首七源 FINAL `.runtime/full-policy-dependencies-backend-final-20261006T053433Z-c96cad2c` 原件保留。随后显式窄补“跨表同 UUID 必须按真实 reference kind 区分”的图边语义；同 UUID 反例及两原 cycle 风险共 3 PASS /0.86s：`full-policy-dependency-kind-bound-final-risk-20261006T053700Z-380144ec`。最终七文件 strict/Ruff/format 分别 `9b533cdd` / `018f95e4` / `b15069df`，均 PASSED，scope 稳定；没有重跑无关纯行为或金融链。

## 未覆盖

真实主应用路由/RRRO 注册及实际 PG 由主代理接线；新只读 Web 界面另包交付。当前图不等于完整资金冲突求解；不自动失效或恢复所有模板动作，不验证所有持仓与边界重算完成，不自动解除保护或确认新版本。原 FULL-104 逐项证据及最终关闭仍 PENDING。
