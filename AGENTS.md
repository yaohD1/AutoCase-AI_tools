# AutoCase AI 开发地图

## 工作规则

- 先确认任务涉及的前端/后端边界，再读取相关文件。
- 优先复用现有 Vue、Element Plus、Flask、SQLAlchemy、Axios 模式。
- 不修改 `storage/` 中的运行数据、上传文件和导出文件。
- 修改后端模型时同步检查 `backend/app/models/__init__.py`、数据库初始化和删除级联。
- 修改 API 时同步检查对应的前端 API 封装和调用页面。
- 不把 API Key 写入源码、日志或生成脚本。

## 项目地图

### 前端

- `frontend/src/App.vue`：全局壳层、顶部导航、全局样式。
- `frontend/src/router/index.js`：路由入口。
- `frontend/src/views/Upload.vue`：首页、图片/文档上传、知识库入口、模块分析和用例生成。
- `frontend/src/views/CaseList.vue`：已审批用例、用例编辑、待审审批、原型图查看和 XMind 导出。
- `frontend/src/views/ModuleList.vue`：模块列表和模块审批。
- `frontend/src/views/Scripts.vue`：Playwright TypeScript 脚本生成工作台。
- `frontend/src/views/Settings.vue`：AI 配置、项目和迭代管理。
- `frontend/src/api/index.js`：所有前端 API 封装。

### 后端

- `backend/app/__init__.py`：Flask 应用工厂和蓝图注册。
- `backend/app/routes/`：HTTP API；`testcase.py` 还包含项目、用例和模块相关接口。
- `backend/app/models/`：SQLAlchemy 模型；`database.py` 负责 SQLite 初始化和轻量迁移。
- `backend/app/services/case_generator.py`：AI 用例生成业务逻辑。
- `backend/app/services/automation_generator.py`：自然语言用例到 Playwright 脚本的渲染、暂存、归档和路径校验。
- `backend/app/adapters/`：AI 适配器，`GeneralAdapter` 支持通用 OpenAI 兼容接口和结构化 JSON。
- `backend/app/utils/`：提示词、知识库检索、文档和校验工具。

### 数据和运行目录

- `storage/app.db`：SQLite 数据库。
- `storage/uploads`：上传文件。
- `storage/exports`：导出 ZIP/XMind。
- 自动化工作区根目录由 `AUTOMATION_WORKSPACE_ROOT` 配置。

## 核心业务流

- 用例生成：上传文件 → AI 分析模块 → 模块审批 → 生成待审用例 → 用例审批 → 已审批用例。
- 自动化脚本：选择已审批用例 → 保存项目级脚本配置 → AI 结构化步骤 → 渲染 Playwright TS → 写入服务端工作区 → 生成 ZIP 和历史记录。
- 图片审批：`TestCase.image_id/image_source` 和 `PendingModule.image_id` 负责原型图关联；“附带原图”只决定是否把图片送给 AI，不应影响审批页展示。

## 自动化脚本约定

- 当前只支持 Playwright + TypeScript，不执行生成脚本。
- 只允许从 `status='approved'` 的用例生成。
- `Base URL`、工作区、脚本目录、浏览器、认证状态、超时、覆盖策略和 AI 配置保存在 `AutomationConfig`。
- 生成批次保存在 `AutomationGeneration`，接口位于 `backend/app/routes/automation.py`。
- 工作区路径必须位于 `AUTOMATION_WORKSPACE_ROOT` 内；默认拒绝覆盖已有文件。
- 无法可靠推断定位器时生成 `TODO`，模型不能猜 CSS/XPath。
- 生成接口同步执行；不要在小改动中擅自改成异步任务或引入新队列。

## API 快速索引

- 项目：`/api/projects`、`/api/sprints`。
- 上传/图片/文档：`backend/app/routes/upload.py`。
- 用例与审批：`/api/generate`、`/api/cases`、`/api/cases/pending`、`/api/cases/<id>/approve`。
- 模块审批：`/api/pending-modules`、`/api/pending-modules/approved`。
- AI 配置：`/api/ai-configs`。
- 知识库：`/api/knowledge/*`。
- 自动化：`/api/automation/config`、`/api/automation/generate`、`/api/automation/generations`。

## 常用命令

- 后端：`cd backend` 后执行 `python run.py`。
- 前端开发：`cd frontend` 后执行 `npm run dev`。
- 前端构建：`cd frontend` 后执行 `npm run build`。
- Python 语法检查：`cd backend` 后执行 `python -m compileall app`。
- 差异检查：`git diff --check`。
- 前端默认地址：`http://localhost:5173`；后端默认地址：`http://localhost:5000`。

## 低 token 工作方式

- 先读本文件，再按任务只读一个入口文件、一个 API 文件和一个服务/模型文件。
- 前端问题优先从 `router/index.js`、目标 view、`api/index.js` 开始。
- 后端问题优先从 `app/__init__.py`、目标 route、目标 service/model 开始。
- 不要默认全文读取 `Upload.vue`、`CaseList.vue` 或完整 README；使用 Grep 定位函数和 API 后再读取上下文。
- 完成后只运行与改动边界匹配的验证命令。

## 已知限制和风险

- 当前项目没有用户认证和项目级权限，自动化接口沿用本地信任模型，不适合直接暴露公网。
- AI 生成的定位器必须人工审核；生成脚本不是可直接承诺通过的生产测试代码。
- 当前数据库使用 SQLite 和轻量迁移，不要假设存在 Alembic 完整迁移链。
- `README.md` 可能落后于代码；判断行为时以实际 route、view、model 和 service 为准。

## 任务完成检查

- 只修改任务需要的文件，保留用户已有改动。
- API 改动验证状态码、项目隔离和错误分支。
- 数据模型改动验证数据库初始化和已有数据兼容。
- 前端改动至少运行 `npm run build`。
- Python 改动至少运行 `python -m compileall app`。
- 最终说明改动文件、验证命令、未解决限制，不提交未经请求的 commit。
