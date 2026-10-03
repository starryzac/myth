# 开发状态

完整目标：先完成初版全部验收，再推进完整版及研究、竞赛交付。目标保持 active。

当前最小未完成任务：MVP-202（90 日现金流与自主资金边界）。

MVP-201 已完成：完整历史覆盖、精确分位估算、独立分类证明、只读 GET、seed v2 已验证。最终 check `20261003T184838Z-1632f44d` 的 455 后端、4 前端、1 Edge E2E 全部通过，源码摘要一致；两次正式 seed 完全复现。

MVP-105 已完成：离线规则编译、可信日期基准、可追溯草稿与修订、来源检查、默认关闭的 LLM 接口。check run `20261003T180806Z-aad50cdf` 的 377 后端、4 前端、1 Edge E2E 全部通过。

MVP-104 已完成：确定性房租/信用卡候选、幂等修订、来源快照检查、确认隔离；最终 check run `20261003T173729Z-a3c17e60` 的 271 后端、4 前端、1 Edge E2E 全部通过。

已完成任务：MVP-001、MVP-002、MVP-003、MVP-101、MVP-102、MVP-103、MVP-104、MVP-105、MVP-201。M0 与 M1 完成；资金边界、执行引擎及业务界面仍待后续实施。各任务文档含实际命令与原始日志。

## MVP-001 文件级实施计划

- 根配置：pyproject.toml、pnpm-workspace.yaml、Makefile、make.cmd、docker-compose.yml、.env.example、README.md、AGENTS.md。
- API：apps/api/app/main.py 与 HTTP 健康测试。
- Web：React/TypeScript/Vite 应用，调用真实健康 API。
- scripts/tasks.py：统一启动、安装、验证入口；packages/contracts：OpenAPI 和生成的 TS 类型。
- docs/progress：按任务记录命令、结果、限制与下一任务前提。

测试：API 健康响应；浏览器真实请求连通；依赖安装与服务启动；后续 M0 错误请求、类型/lint 负向探针。

风险：本机 Docker 引擎初次检查未运行，需启动并实测；现有其他研究文档保持原状。
