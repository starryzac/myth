# FULL-808 功能差量（2026-10-06）

状态：PARTIAL_IMPLEMENTED；原需求关闭 PENDING；实际实验 NOT_RUN。

新 `scripts/full_experiment_claims.py` +直接测试实现固定原MVP数值重新计算、身份/whole输出精确JSON/单位/当前源码/原字节准入；TOOL_TEST_ONLY准入false，缺原记录MISSING，永不升级FULL效果。未生成真实效果数字或填企划书成功。完整Full材料账本、Full28效果、最终18/Full包出口与全部材料人工检查仍缺；窄已有MVP引用不是本编号全验收。

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
