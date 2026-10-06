# W8 当前功能材料包交接

范围：仅本新目录。旧材料、失败证据与正式模拟历史不覆盖；没有改 Main/App/API/scripts/tasks/shared contracts、没有 PG/浏览器/Docker/金融/seed/reset、没有安装依赖或使用 LibreOffice。本包的 build/ 文件是材料作者与渲染源，不是新的产品验收框架。

## 已交付可审阅能力

- 企划交付版 PDF 实际 12 页，白皮书交付版 PDF 实际 30 页，答辩交付版 PPTX 实际 10 页可编辑文字与来源 notes。正文共用真实产品源，白皮书覆盖原建议 22 章；答辩含 18 问答。
- 公开原件 45 份 exact bytes、路径、尺寸和 SHA 逐项绑定，独立实验结果与历史上下文分开。Registry 对应 PDF 的 156 段及 5 个明确未测业务假设，slides/问答/脚本/报告另有来源引用；不是全部材料每一句的最终语义审查。
- 初版 14 指标和原 Full 28 度量均保留 null/NOT_RUN 与分母定义，空 CSV 不算实验输出。原 8 图逐项保留输入/原生画面缺口，不画假效果柱形图。外部市场/机构事实数字实际使用 0。
- 42 页最终 PDF 经真实 Poppler 渲染并联系表检查，8 个重点页全尺寸检查；实际最终 PPTX 导入 Artifact Tool 后重新渲染 10 页，全尺寸逐页检查未观察到溢出/重叠。只是 AI 版面审阅，原生 PowerPoint 打开及真人逐项业务审阅 NOT_RUN。
- 四分钟脚本/镜头清单给出原 A 的 T+1、B 的 NL/修改/首次确认/新增资金、C 的无损与有损确认，以及异常、Trace 和真实对照需求。状态 NOT_RECORDED/NOT_TIMED。

## 准确未覆盖

FULL901—906 全部仍 PENDING，正式关闭仍 21/92。Full >=50 族/多变体/七机制/八消融、初版 24×5 正式完整独立结果、真人 18—24 计划（实际 0）、当前新性能、原 8 图、最终两版全量与当前同 run 三黄金链/异常/离线备份/<=240 秒视频均未取得。没有工行合作、真实资金接口准入、客户规模/收益/安全改善结论。

FULL102 新 35 表完整版图只有直接检查，actual PG NOT_RUN；307 prepare 接缝修复仍未证明完整银行执行；旧 204 v1 UNKNOWN 及新 v2 状态不称 globalComplete。最新负责人消息只是交接事实，见 current-owner-gap-addendum.md；不能替代原生金融日志。

SCRIPT_TIMEBOX_REVISION_20261006 明确：原六段合计 30/55/55/65/45/30=280 秒；新计划 20/50/50/60/35/25=240 秒，六类原内容保留，未实测。若演练不足，须新版本记录，不能删内容骗时长。

## 生成/复核接口

运行环境使用 load_workspace_dependencies 返回的已安装 Python/ReportLab/pypdf、Node/@oai/artifact-tool、Poppler 和系统 Microsoft YaHei。精确 runtime 路径和 finalizer 信息见 build/deck-structure-delivery.json、build/pdf-structure-delivery.json；font 原 SHA 已记。

`build/render_pdfs.py` 消费 pages.json，输出固定两个“交付版”PDF，拒绝覆盖；`build/render_deck.mjs presentation-finalizer-delivery` 消费 pages.json，按技能 finalizer 输出 PPTX，并拒绝覆盖。Node 需要 RUNTIME_NODE_MODULES 和本 build/node_modules 指向已安装包的 junction；不能复制平台依赖为另一平台构建。当前目录再次运行会正确拒绝已有成品。若要复建，应新建 docs/materials 下另一个目录，只复制 pages.json 与 renderer 源，创建空 outputs 与所需 build；不得复制/重用现成输出冒新生成。

`build/render_previews.py` 对真实最终两个 PDF 作 100dpi 渲染，`build/render_final_deck.mjs` 重新导入最终 PPTX 渲染 10 页，均保真实文件字节。它们的重跑也必须新目录，不覆写原图。

`build/finalize_material_package.py` 只检查本包页数/原件 hash/登记一致性/未测值和现路径差异并生成本包 manifest；默认 x-create 拒覆盖。它不判断产品资金安全/材料效果/人工一致性通过。根生产 build-proposal 命令仍是旧 HTML 审阅入口，尚未接本包成品 builder，此是具体后续接线缺口。

既有 scripts/evidence_check.py 在真实清单缺失时实际 exit2，stdout/stderr 原字节保留。该拒绝不证明 18 组完整出口，INCOMPLETE 未改。

## 原失败与来源边界

两个失败 PPTX 子进程的真实日志和第一版脚本保存；后续独立文档执行授权与依赖路径正确后才生成最终文件。首 PDF footer 过窄的错误只在原 tool transcript 中捕获，后续 junction 掩盖 shell 退出码；build/first-pdf-render-failure-record.json 诚实标为回顾记录，不伪造原 stderr/原失败源码。初稿、阅读稿和最终稿均留存。

build/source-current-comparison.json 只比较 45 明确 public 来源当前字节与本版捕获副本。Root 后续源码变化并行进行，本包不宣称全仓 frozen/current financial proof。后续更新应另新目录/修订，保此次原件及 hash。build/node_modules 是已安装依赖 junction，manifest 显式排除它及生成缓存，不能遍历/打包当项目成果。

最终成品/文件 SHA、版面审阅与退出码原日志见 delivery-manifest.json。这里的“已生成”仅材料结构与来源交付，不能标初版/Full 验收完成。

终态封存说明：最初 manifest.json 把尚未关闭的 finalize.stdout 计为空，完整实际原stdout随后产生；原清单和原日志均保留为诊断。此文件的上一版在 build/revision-3，最终终态字节封存只认 delivery-manifest.json。
