# 钱途有界 Web

这是模拟资金原型的浏览器入口。当前 MVP-001 仅验证 Web 与 API 的启动和健康连接，不生成金融结果。

## 本地运行

在仓库根目录安装工作区依赖后运行：

```powershell
pnpm --dir apps/web dev
```

浏览器打开 `http://127.0.0.1:5173`。开发服务器把 `/api` 代理至 `http://127.0.0.1:8000`，可通过 `API_PROXY_TARGET` 环境变量调整 API 目标。浏览器使用同源路径；`VITE_API_BASE_URL` 仅为 HTTP 测试夹具入口，当前不提供跨源部署。

```powershell
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
pnpm --dir apps/web e2e
```

端到端测试会在专用端口 18000/15173 启动当前代码的 API 和 Web，不复用现有服务；它经过 Web 代理请求真实 API，不拦截接口。健康响应正文必须符合模拟 API 契约。默认使用已安装的 Microsoft Edge，可通过 `PLAYWRIGHT_CHANNEL` 改用其他已安装的浏览器通道；CI 环境应预先安装所选浏览器。

## 测试边界

- Vitest 在 UI 与健康 HTTP 接口的边界验证用户可见的模拟声明及 API 连接状态；测试运行本地 HTTP 服务，不 mock React 组件或请求模块。
- Playwright 验证浏览器到开发代理再到 FastAPI 的真实健康请求。
- 当前不代表资金业务、真实银行接入、收益或生产可用性已经验证。

依赖使用固定版本，版本解析结果由根目录 `pnpm-lock.yaml` 保存。
