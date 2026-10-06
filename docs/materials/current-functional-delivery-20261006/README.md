# W8 当前功能材料交付

新目录交付，旧材料/失败/正式历史保留。正式关闭仍21/92；FULL901—906全部PENDING。本包提供可审阅成品与来源，不将INCOMPLETE升级为全验收。

- `outputs/钱途有界_当前功能企划_12页_交付版.pdf`：真实12页与proposal.md。
- `outputs/钱途有界_当前功能白皮书_30页_交付版.pdf`：真实30页与whitepaper.md，覆盖原建议22章。
- `outputs/钱途有界_当前功能答辩_10页_交付版.pptx`：实际10页可编辑文字、notes来源，defense.md含18问答。
- `experiment-report.md`：保初版/Full完整基线/消融/原定向失败与分母；results空值明确NOT_RUN，非效果数据。
- `demo-240s-script.md` / demo-shot-list.json：显式280→240秒时段修订，NOT_RECORDED/NOT_TIMED，完整内容未删。
- 三registry、sources.json、evidence-index.md及source-originals：公开原字节/SHA/状态/用途，效果自动门NOT_PASSED。

## 构建与检查（仅文档，无LO/安装/金融）

已安装bundled Python+ReportLab/真实msyh中文字库生成PDF；已安装bundled Node `@oai/artifact-tool`生成PPTX，技能finalizer以Python核包/几何/字体并实际导入。Poppler把全部42页PDF转PNG，所有10页PPTX渲染审阅。没有依赖安装、LibreOffice、PG/Browser/Docker/seed/reset或真实资金。

生产build-proposal仍是旧HTML源审阅入口，未修改tasks/CI；本包的builder只新目录。author_materials.py和两次content/layout revision保存原版，不覆盖成品。复建须复制本new source到新独立目录、保ROOT路径引用；render_pdfs/refuse-existing、finalizer/refuse-existing防旧成品覆盖。运行Node须设置RUNTIME_NODE_MODULES为load_workspace_dependencies给出的实际Node包路径；最终校验子进程在sandbox中首EPERM失败保留，后授权仅文档子进程成功。

## 未覆盖

正式Full50族/多变体/七机制/八消融、完整独立指标/统计、真人18—24计划（实际0）、新当前性能、最终两版全量、最终当前三黄金链/异常/离线/同run原生截图与<=240秒视频、实际PowerPoint打开/投影、人工业务/消保/安全与逐claim一致性审查都未完成。原八图缺数据/原画面，示意文字不冒native capture；旧W0性能和旧资产diagnostic只historical context。

当前自动材料出口仍INCOMPLETE；manifest的actual_pages/slides仅结构事实，不关闭任何FULL原项。

## 实际缺证据拒绝

本包只运行既有检查器的缺原请求门：`python scripts/evidence_check.py --manifest docs/materials/current-functional-delivery-20261006/REQUIRED_REAL_EVIDENCE_REQUEST_MISSING.json`。该文件确实不存在，实际退出码2、status=REJECTED，原stdout/stderr保存在build/evidence-check.*。此负例只证明缺输入不会包装成功；没有运行18组完整出口，也不证明FULL906自动语义/人工一致性验收。

最终文件的页数、SHA与文档结构检查见delivery-manifest.json及build/package-review.json；AI渲染版面审阅见build/visual-review.json。源原件复制后，Root仍在并行开发；build/source-current-comparison.json明确哪些捕获原件与终态当前路径已不同。本版绑定捕获原件，不能以相同HEAD声称全仓冻结或当前金融实证。

终态封存说明：最初 manifest.json 把尚未关闭的 finalize.stdout 计为空，完整实际原stdout随后产生；原清单和原日志均保留为诊断。此文件的上一版在 build/revision-3，最终终态字节封存只认 delivery-manifest.json。
