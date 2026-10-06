# W1阶段原件校验器安装

显式修订 `W1_PHASE_PROOF_VALIDATOR_INSTALLED_V2`：冻结候选manifest SHA9671f7bd46723becf459ad6528334c752d2a0d5dbb697c4665c661fb50f088cf及两原源码逐bytes核对、归档到 `.runtime/W1-phase-validator-install-20261005T1024Z/originals` 后安装新 `scripts/mvp_phase_proof_validator.py` 和新纯测试。没有修改金融核心/provider/旧阶段风险测试/任何原证据或raw False。

可运行能力：`validate_capture`每调用fresh读取原返回Capture封套及原件/source，独立核真实phase/commit/rollback/probe原图内部一致，输出derived引用/逐phase分类；完整图仍明确actual-run binding MISSING，运行/经济均false。源码合同和接口见 `../experiments/mvp-phase-proof-validator-contract.md`。V2新provider注册的source/clock adapter缺失另显式MISSING，不把global SOURCE变造旧V1。

验证：57 TOOL_TEST_ONLY纯测试PASS20.32s（原56+1新缺口），strict mypy2/Ruff2/format2 PASS；原56测试名及风险assertions保留，仅改生产import和fixture路径，新增一项V2缺口。安装本身无SQL、DB/金融/浏览器，未运行或重跑PG；原56候选日志/源/RED全部留在原freeze。

未覆盖：可信actual driver run-to-original Capture attestation、V2 registry精确适配、部分/历史/no-bank分类完整原封套、typed effect和经济oracle、正式24×5/完整14指标/初版全量。root接后真实driver attestation；没有这些原件时不提高SERVICE/经济证据等级，不关闭MVP-503或FULL。
