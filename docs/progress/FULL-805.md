# FULL-805 功能差量（2026-10-06）

状态：PARTIAL_IMPLEMENTED；原需求关闭 PENDING；实际实验 NOT_RUN。

七有限机制与8对应分支已实现，可调用 `decide_full_mechanism`，源UNKNOWN/未决、每机会分母、金额整数/现金占用/原确认与 effect identity 保留。新domain `full_experiment_mechanisms.py` 与直接测试。原五臂MVP adapter不改。实际Full7服务/350运行、完整金融恢复及审计消融和Full28指标仍未接，当前全部 NOT_RUN/noauthority。有限当前候选机制不是生产完整P。不得关闭本编号。

关键合同：[FULL有限实验核心合同](../experiments/full-finite-experiment-core-contract.md)。不改旧MVP24、正式模拟历史、旧失败/哈希，未接真实资金。

## 已运行命令与范围

- 核心4源 `.venv/Scripts/python.exe -m pytest apps/api/app/tests/test_full_experiment_mechanisms.py apps/api/app/tests/test_full_experiment_cases.py -q`：49PASS1.21s，`evidence/W7/full-finite-experiment-amount-core-direct-20261006T022909Z-d69f9425/manifest.json`。
- 核心4源 mypy/Ruff：`full-finite-experiment-amount-core-types-20261006T022910Z-d4759553` / `...static-20261006T022910Z-e64bcf18`，PASS。
- 新窄引用2源 pytest `scripts/tests/test_full_experiment_claims.py -q -p no:cacheprovider`：12PASS1.26s，`evidence/W7/full-existing-mvp-original-json-number-final-20261006T024041Z-e2cd7cb7/manifest.json`。
- 引用2源 `mypy --explicit-package-bases`/Ruff：`...original-json-types-final-20261006T024041Z-329c7c16` / `...original-json-static-final-20261006T024041Z-37eb6d79`，PASS。
- 原核心首静态 RED `full-finite-experiment-core-static-20261006T022447Z-ec682cb8`；引用首11setup ERROR /type/static RED `full-existing-mvp-number-*-20261006T023839Z-*`，原source各保 `.runtime/full-experiment-core-first-static-red-20261006T0224Z`、`.runtime/full-existing-claim-first-red-20261006T023914Z-2a8f214a`。

上面均工具/纯函数范围，未运行PG/browser/金融/50×7/真人，不能作为版本全量验收或Full效果。最终源冻结见新 `.runtime/full-experiment-core-final-*`。

## 下一真实前置

实际50族作者INPUT/八真值 → 独立审核/whole family隔离 → Root真实Full selector/执行接缝 → 每臂实际原记录/28指标 → 最终集中验收。缺源或未接都保持 UNKNOWN/null。

## 2026-10-06 03:23 UTC 原生消费者增量

Development混合入口已提供实际MVP服务+固定Full API消费路径，当前50种操作，原body/HTTP文本/SHA/error/skip和原机会分母均保留。该入口尚未接7机制selector，不称已经实际运行7臂或8消融；正式原金融结果仍null。50×2作者包和直接检查范围见 [原生开发病例合同](../experiments/full-native-development-cases-contract.md)，本编号仍PENDING。

## 2026-10-06 03:55 UTC 当前 GENERAL 真来源 producer

新增独立 `domain/full_experiment_asset_selection.py` / `services/full_experiment_asset_selection.py` 与2直接测试文件。可调用 `read_current_general_purchase_selection`：owned bf_test、fresh RRRO、真实已确认 Full/MVP/current bank/audit/income/claims/immutable catalogue/365保护 → 原 rule path/SHA → 实际有限机制选择和严格单批新提案。原 P response/status/hash不改，原 prepare/recheck/historical不改。原未决身份阻止新选择；未来收入计入0；B4没有实际模型、MISSING；Goal/支付/恢复/不确定世界未接，银行consumer NOT_CONNECTED，实际臂和指标 NOT_RUN/null。

完整接口及 Root 必接的 private 实验选择/当前与历史重验见 [当前 GENERAL 机制合同](../experiments/full-current-general-mechanism-contract.md)。26 synthetic pure PASS3.01s：`evidence/W7/current-full-general-mechanism-original-exit-direct-20261006T035411Z-2da4d0e3`；strict4 PASS `...types-20261006T035411Z-90b4785b`；Ruff4 PASS `...static-20261006T035412Z-d5b05ed5`。三manifest scoped=true/global=false，独立 `full_action_set_recovery_observations.py` 变化明确保留。数据库风险仅 collected1：`...producer-pg-collection-20261006T035140Z-56bcdcc7`，NOT_RUN；此处没有资金执行证据。

首direct14PASS/1FAIL/2 Windows temp ERROR、types1/static5和第二temp permission RED、final import type RED原件保留；已修夹具/导入/格式与项目内默认权限唯一纯原件目录，无绕过金融门。下一步 Root 新消费者的产品/金额选择接缝、完整 current/historical source marker、单节点真实读取证明；之后仍须完整7机制/8消融/原同条件实际运行及28指标。本编号不关闭。
