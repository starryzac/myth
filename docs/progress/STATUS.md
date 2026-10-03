# 开发状态

完整目标：先完成初版全部验收，再推进完整版及研究、竞赛交付。目标保持 active。

当前最小未完成任务：MVP-101（数据库模型与迁移）。

已完成任务：MVP-001、MVP-002、MVP-003。M0 工程基础门通过；各任务文档含实际命令与原始日志。

## MVP-001 文件级实施计划

- 根配置：pyproject.toml、pnpm-workspace.yaml、Makefile、make.cmd、docker-compose.yml、.env.example、README.md、AGENTS.md。
- API：apps/api/app/main.py 与 HTTP 健康测试。
- Web：React/TypeScript/Vite 应用，调用真实健康 API。
- scripts/tasks.py：统一启动、安装、验证入口；packages/contracts：OpenAPI 和生成的 TS 类型。
- docs/progress：按任务记录命令、结果、限制与下一任务前提。

测试：API 健康响应；浏览器真实请求连通；依赖安装与服务启动；后续 M0 错误请求、类型/lint 负向探针。

风险：本机 Docker 引擎初次检查未运行，需启动并实测；现有其他研究文档保持原状。
