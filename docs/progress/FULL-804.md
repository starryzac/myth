# FULL-804 功能差量（2026-10-06）

状态：PARTIAL_IMPLEMENTED；原需求关闭 PENDING；实际实验 NOT_RUN。

作者合同已实现 whole original INPUT SHA、八真值、全族split/数值日期拓扑隔离与 null 28指标；**仅 supplied authoring contract，尚无实际50族/多变体作者数据、独立八真值原件审核、正式冻结或运行**。纯50长度夹具不是正式业务族。新domain `full_experiment_cases.py` 与直接测试。下一步实际50族DEV/VALIDATION原INPUT及真值作者草稿，原21 Scenario 无多种Full dispatcher需Root接；不得关闭本编号。

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

## 2026-10-06 03:23 UTC 作者数据与原生入口增量

新 v4 主包已有实际50家族/100完整INPUT、100八类作者TRUTH、100机会SCHEDULE、16类别，整族18DEV/32VALIDATION/0FROZEN。原v1实际51/102、v2/v3和失败原件全部保留；不把旧statement重写成成功。源码及原件读回通过仍AUTHORING_INCOMPLETE；独立真值审核、完整7臂接线/正式freeze/金融实验未开展，28指标全null。

新增固定真实API操作扩展与 `FullNativeCaseRunner` 可消费完整Development输入，原MVP dispatch/fault/ref不改。60pure+新增timeout1pure、strict5/Ruff5通过，唯一实际PG候选仅collection。详细范围/原件/未覆盖见 [原生开发病例合同](../experiments/full-native-development-cases-contract.md)。本编号仍PARTIAL_IMPLEMENTED/PENDING，不凭50数据文件直接关闭。
