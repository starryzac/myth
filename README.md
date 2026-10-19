# 知余 · Zhiyu

基于 FastAPI、React / TypeScript 与 PostgreSQL 的模拟资金 Agent。用确定性规则管理当前可安排资金，提供规则配置、多目标规划、资产规划与支取、必要问题问答、持续授权和原动作恢复。

所有账户、银行操作与回执均为模拟。金额、权限、幂等、执行状态与审计由代码校验；未来未到账收入不扩大当前资金边界。模型负责理解需求和提出候选，执行前仍须通过授权与业务校验。

## 仓库内容

这是当前已安装源码的精简发布，使用知余四页应用作为唯一前端入口。

```text
apps/api/app/        API、规划与规则、模拟银行、执行与审计、受控 worker
apps/api/alembic/    数据库迁移
apps/web/           知余前端及其实际使用的共享模块
packages/contracts/ OpenAPI 与 TypeScript 合同
scripts/            安装、启动、构建与合同生成
deploy/initialize_demo.py  独立模拟数据库初始化
```

旧控制台与旧 Demo 的前端入口、重复前端组件、独立验收工具和过时容器部署配置已清理。开发过程材料、测试夹具、运行数据、依赖目录和私有配置不纳入发布；资金执行、合同类型及历史审计仍需使用的共享模块保留。

## 本地启动

需要 Python 3.12、uv、Node.js 24、pnpm 11 和 PostgreSQL 16。启动器检查独立目录名，请按以下名称克隆：

```powershell
git clone https://github.com/starryzac/myth.git bounded-funds-next
Set-Location bounded-funds-next
python scripts/tasks.py bootstrap
```

模拟数据库管理连接默认使用 `127.0.0.1:54329`。首次体验且该端口空闲时，可用以下命令启动数据库：

```powershell
docker compose up -d db
```

已有本地数据库时，直接复用其服务；管理连接可通过会话环境变量 `ZHIYU_NEXT_ADMIN_DATABASE_URL` 指定，不提交口令。启动器只接受回环地址上的 54329 端口，每轮创建独立数据库、低权限角色及审计 epoch。

```powershell
uv run --frozen --no-dev python scripts/zhiyu_next.py prepare
uv run --frozen --no-dev python scripts/zhiyu_next.py start --round <prepare返回的完整轮次>
uv run --frozen --no-dev python scripts/zhiyu_next.py status --round <同一完整轮次>
```

默认页面：[http://127.0.0.1:19273/zhiyu-next.html](http://127.0.0.1:19273/zhiyu-next.html)，API 端口为 `19200`。若端口已占用，先用 `prepare --help` 查看自定义参数，并始终使用该轮的端口和完整标识。已有历史不会在启动时自动重置。

模型服务在页面的模型设置中配置并测试连接；密钥保存在本机忽略目录中。未配置模型时，可使用离线规则入口。运行信息和凭证保存在 `.runtime/`，不应上传。

## 构建与检查

```powershell
pnpm lint          # 前端代码规范
pnpm typecheck     # TypeScript 类型检查
pnpm build         # 唯一知余入口的生产构建
pnpm types         # 生成基础 API 的 OpenAPI 和 TypeScript 合同
```

产物位于 `apps/web/dist-zhiyu-next/`。独立启动器使用源码开发服务；`pnpm dev` 仅启动前端，需先准备并启动对应 API。Windows 可用 `make.cmd`，Linux/macOS 可用 `make`；两者调用 `scripts/tasks.py`。

## 当前边界

代码已接入十二模板候选、受控模型工具、多目标与双向授权调拨、资产及必要问题等流程。整体功能仍有验收缺口，部分请求耗时较长；本仓库不宣称完整模拟金融、常驻恢复或浏览器验收已完成。

后台默认关闭，现有 worker 的精确源码及实际验收许可门保留。仓库不分发本机许可文件；重新部署需自行完成对应验证。UNKNOWN 动作继续使用原动作身份和银行键恢复，暂停与撤销不会把未知结果改成成功。
