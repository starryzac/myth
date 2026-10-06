# FULL-906 当前材料来源与证据索引

状态：SOURCE_LEDGER_DELIVERED / FINAL_CLAIM_GATE_NOT_PASSED。来源索引是公开文档/代码的明确子集，不宣称全仓冻结或全证据验收。外部事实/市场数字实际引用为0；业务价值均是假设。

## 登记规则

正文每页与每段来源已记录，`claims_registry.yaml`的正文许可只针对原限定措辞，automatic_final_admission=false；不是自动效果审查通过。`figures_registry.yaml`明示八图缺口，没有native截图。`metrics_registry.yaml`保初版14项与原Full28项，值均NOT_RUN/null；CSV空列不是真结果。

当前已有 `scripts/evidence_check.py` 只把显式真实原请求交给原 `export_evidence.py`；缺请求exit2，MISSING/UNVERIFIED/INCOMPLETE非零。原exporter的complete_mvp_documents/eight_figures/manual_consistency_review等未支持内容审查不会因为本台账存在变VERIFIED。没有调用其完整出口，也没有新增验证框架或弱化门。FULL808效果数字/外部来源与CI自动准入、真人研究和人工一致性仍未完成。

## 精确原件

| ID | 原路径 | SHA256 | 证据范围 |
|---|---|---|---|
| S01 | docs/spec/product-spec.md | 6993557c9b72d3b18fb23fd2fb7c68a44a6d138b37b4c3da6ee164d18c2e6e9d | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S02 | docs/spec/glossary.md | a8e0791a96ca175d9d016a00c28a65d49276e8c9aea2297865c3b01d55dda3be | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S03 | 钱途有界_完整开发计划_Codex执行版.md | 88be31edbb5f5442420942f821af744aa92c426617dbbac5b41cf2eb0b62fa06 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S04 | docs/spec/requirements-traceability.md | fdf69f1db667ffda35a089d8318fb6fed25a958e8a198aa4b0d6980038d15a8a | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S05 | docs/progress/STATUS.md | cb8b28fa5f39e35b486e0f0096dc114f61b3b710c8fd7df54e374052daa92a22 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S06 | docs/experiments/mvp-case-design.md | c7cefd8b3a0aa80748bf104bcb07662e1dd6eae4e227549ee6d7ae84480fcc63 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S07 | apps/api/app/domain/boundary.py | 1b15e7f05906ab7304d7409b445007fa67ba963f10f3a1b0518951daac085bfd | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S08 | apps/api/app/services/income_ledger.py | dc2cb8eac3207279b34a21ca44edfe334e91941cdb9d621ecc647c35460f712d | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S09 | apps/api/app/domain/full_policy_configuration.py | a19441d677b6e23e1d1c85e7be30c277b0b9738286319d276b424405f680e5cd | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S10 | apps/api/app/services/full_policy_lifecycle.py | 5c8515a9c650bb5e6b86210b59f3ddcabcd981f3614616517d32d2758f7ab5d5 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S11 | apps/api/app/services/full_policy_compilation.py | 4f46d1478c49287ac8c32cdc13597574f300872a94811043a011f0ecd75048dd | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S12 | apps/api/app/services/full_protection_projection.py | 76d0cb732db9be6471bd26eeed8bbeea9d1325cac400c90f02aae1cdf0aee46d | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S13 | apps/api/app/services/full_joint_goal_planning.py | 34bedef93dbf5f4baa4ff38a13947aff2e8683abc9d732a6bc402d65faceb4c6 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S14 | apps/api/app/services/full_goal_conflicts.py | 847ecd31f922e1fe530d694daf5acbd2cc4a9ca337ef86b4e99a13bc8fa4c12b | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S15 | apps/api/app/services/full_goal_release_execution.py | c50c5655a3b6cc2d8732c78b23d14b4ea9cf6f7d0e1e5fbb76f35c77001b67d8 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S16 | apps/api/app/services/full_asset_allocation.py | de0b9e0877905f0151e593df2ca415c5c77484510b83403052a0e6b4669edf05 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S17 | apps/api/app/services/full_asset_execution_store.py | aa08efda15eff035b018c7a1ea123abc76c44f997f16aa4c50bd31790d7eccba | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S18 | apps/api/app/services/finite_uncertainty.py | f7423ae4de73bb4dc542fb80ad9f8b24fa94e8c7537fec3e5dfb2018e7109a8a | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S19 | apps/api/app/services/question_workflow.py | 5277316c88f927aec9db8e0544062da8d124106a8053b7a064495a7876a35406 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S20 | apps/api/app/services/full_intervention.py | b1ebb22feae54fdc4615ae6ca017d6f9fa5cad764f4d03d2a835f481399b8eca | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S21 | apps/api/app/services/execution.py | ba4ec3702d35bd5e3906ea82a2d1ee282246b3eeaa12d647bf22f84adade6f4a | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S22 | apps/api/app/services/execution_bank.py | 69b7e9c06ec700bed7b43cefb92abad72654973af8449c53ab69971dc05594f7 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S23 | apps/api/app/services/audit_chain.py | 8845554e8ae177cef4b0e0ec17a17d7fbf4b4ebb69218f026162d5a45e6c2485 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S24 | apps/api/app/services/full_reconciliation.py | 72c958584be677c9e3e1f844986e6e8c2415d8fd4868b2e8b6a98c2e5107600c | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S25 | apps/api/app/services/local_actor_sessions.py | 4704ee09fc48479f5248bf47daf7103ea6df517186345315c766addb4db6b21e | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S26 | docs/security/threat-model.md | 8e18ed1c6cffd8c0bb435ca80a2c9f3848e25f4a28c23ed361c96f25bba08375 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S27 | docs/progress/FULL-701.md | 908ac65ae66d82c3247764cf92ebae2fd4333131a9542332d5c357fe3ff876a5 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S28 | docs/progress/FULL-105-financial-preview-ui.md | 3c0f64613e63c686cbb442d3061ed405d72747ad632212d5d92e82558fe87be2 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S29 | docs/progress/FULL-307-execution.md | 0b0852f3b19f982cefac6731f3da4cf1bd6cad69a7fb3f8c858dcc106333e626 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S30 | docs/progress/FULL-604-execution-ui.md | 0b756eee1732fd24d5d4aeb3caea7187ffb0ea1b3900d61481ece81748549cf2 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S31 | docs/progress/FULL-607.md | 20b64728c2df04283a495f7d8ab9a991253327fca285445b0af77ed63d7384fa | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S32 | docs/progress/FULL-806.md | 3f4fa1a8886148b74d44d55c92576d35ddefccacb0bff4a420940a2a07764a66 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S33 | docs/progress/FULL-002.md | 4ce10b23e16b0246e5ac6fd22cf615d25ee048cfe96674c8bb2c6e5beb7b8624 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S34 | docs/progress/FULL-402-403-404-actual-execution-repair.md | caa5256b0880e71b271765a41197f6f15baa7b61d6623ab8a7f1978837e2111b | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S35 | docs/progress/evidence/W0/performance-results.md | d91a9625d3a47c28d5009f30fee56873df40f508f4f0ab85fb113bfa5fa451fd | HISTORICAL_CONTEXT |
| S36 | scripts/export_evidence.py | 6174c36a5bd78246d0f72e0af6f1c3ce4360a45d0a992dc257fd598e73d65e20 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S37 | docs/spec/evidence-export-contract.md | 3c5e1d438fee614d3bb109826df88e87337fd5ea7fb82557cb09f50ad0ee30ef | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S38 | docs/spec/execution-amendment-v2-functional-priority.md | 05ec1c0392e15caa3f58d1b14f541be75ebb60c62b3a6d0d62098eaa69cf1fcb | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S39 | docs/spec/mvp-metrics.json | c8683faa99c6d3eee4e270b18a90c2e0b0ca75e9e2eeb3127833df7ff8107d30 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| E01 | docs/progress/evidence/W4/actual-fixed-payment-original-four-legs-auto-and-ask-20261005T231509Z-34a7fbee/manifest.json | 2a071c4c301e730b2d2d7ad6060862eee1c656774e372eaca87e2ccebec9457c | HISTORICAL_CONTEXT; original PASSED |
| E02 | docs/progress/evidence/W5/actual-audit-scope-original-event-repaired-and-full-readers-20261005T180751Z-fbbaffb2/manifest.json | a9d1271b4c7e60ad6cc2ed970b54084859bdc987ea166ad72ef6a970ffe5571f | HISTORICAL_CONTEXT; original PASSED |
| E03 | docs/progress/evidence/W4/actual-whole-asset-current-open-clock-20261005T213247Z-dadd11d5/manifest.json | c2c96fc051b01c7d60ed8a6fe7b23695065c2ee1bc3fedca85d8765952c947b8 | HISTORICAL_CONTEXT; original PASSED |
| E04 | docs/progress/evidence/W3/actual-global-migration-finite-full-and-http-postcommit-20261006T000656Z-4ce32085/manifest.json | e746f982739a15afcb7a4d46d73a51441229e6db99439c915860abadd718e032 | HISTORICAL_CONTEXT; original FAILED |
| S40 | docs/progress/FULL-102.md | b2faa3b65e598fe93ca0f3d015f6c69baa259e8541ebd91657edaf732eed5cf9 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |
| S41 | 钱途有界_初版开发计划_Codex执行版.md | 5a8747f6741ee8b5af8615508886d2708907d7997119c3df6b6393a75857a279 | DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE |

完整原字节副本位于source-originals；sources.json记录absolute原路径、捕获时刻、byte size。COPY不是重新生成金融证据，HISTORICAL_CONTEXT不参与当前源最终成功验收。相同HEAD不能代替dirty字节一致。

## 22章节覆盖到实际30页

1摘要→p1；2术语→p2；3青年情景→p3；4原则→p4；5事实→p5–6；6DSL→p7–8；7NL编译→p9；8包络→p10；9现金流→p11–12；10多目标→p13–14；11冲突/修复→p15；12资产/定存→p16–18；13最小介入→p19–21；14执行恢复→p22；15审计解释→p23–24；16安全隐私→p25；17实验→p27；18结果→p28；19真人研究→p29；20失败→p29；21工行假设→p30；22限制后续→p30。p26补实际工程与部署边界。

## 自动/人工边界

PDF实际页数由pypdf，渲染由Poppler；PPTX结构/几何/字体与Artifact Tool import由技能finalizer。它们不证明事实内容、实验效果、真人理解、现场使用或原生截图。AI版面审阅单列build/visual-review.json，人工逐claim审查NOT_RUN。原旧材料、FAILED日志、视频与正式模拟历史没有覆盖。
