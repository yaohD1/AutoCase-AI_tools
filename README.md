# AutoCase - AI驱动的测试用例自动生成工具

## 项目简介

AutoCase 是一个基于多 AI 模型的自动化测试用例生成平台，支持从 UI 设计图/文档自动识别功能模块、智能生成测试用例，并提供审批流程和 XMind 思维导图导出。

## 核心功能

- **图片/文档上传**：支持拖拽上传 Figma 设计图、截图、需求文档
- **AI 智能分析**：自动识别 UI 界面中的功能模块和交互元素
- **多 AI 模型支持**：DeepSeek、Kimi、豆包(Volcengine) 及通用 OpenAI 兼容接口
- **用例审批流程**：待审用例 → 通过/驳回，支持编辑后审批
- **项目管理**：支持项目和迭代(Sprint)两级管理
- **多类型用例**：功能测试、UI交互测试、边界值测试、异常场景测试
- **XMind 导出**：一键导出所有用例为思维导图，按 模块→测试点→用例 结构组织

## 技术栈

### 后端
- **Flask 3.0** — Web 框架
- **SQLAlchemy** — ORM & SQLite 数据持久化
- **适配器模式** — 统一的 AI 调用接口，支持 DeepSeek / Kimi / Volcengine / 通用 OpenAI
- **requests / aiohttp** — HTTP 客户端
- **XMind Python** — 思维导图生成
- **Pillow** — 图片处理

### 前端
- **Vue 3** — 渐进式框架
- **Vite** — 构建工具
- **Element Plus** — UI 组件库
- **Pinia** — 状态管理
- **Vue Router** — 路由
- **Axios** — HTTP 客户端

## 快速开始

### 1. 环境要求

- Python 3.9+
- Node.js 16+

### 2. 后端启动

```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

# 安装依赖
cd backend
pip install -r requirements.txt

# 配置环境变量
copy ..\.env.example ..\.env
# 编辑 .env 文件，填入 API 密钥（可选，也支持页面内配置）

# 启动服务
python run.py
```

后端默认运行在 `http://localhost:5000`

### 3. 前端启动

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`

## 配置说明

### AI 服务商配置

支持两种配置方式：

**方式一：环境变量（.env）**
```
DEEPSEEK_API_KEY=your_key
GLM_API_KEY=your_key
```

**方式二：页面内配置（推荐）**

启动后在设置页面 `http://localhost:5173/settings` 添加 AI 配置，支持：
- 服务商类型选择（DeepSeek / Kimi / Volcengine / 通用）
- API Key 和自定义 API Base URL
- 模型名称、温度参数、最大 Token 数
- Vision 能力开关
- 测试连通性

支持的 AI 服务商：
- **DeepSeek**: https://platform.deepseek.com/
- **Kimi (Moonshot)**: https://platform.moonshot.cn/
- **豆包 (Volcengine)**: https://console.volcengine.com/
- **通用 OpenAI 兼容**：任何兼容 OpenAI Chat Completions 的接口

## 使用流程

1. **创建项目/迭代**：新建项目，可选添加迭代(Sprint)
2. **配置 AI 服务商**：在设置页添加 AI 模型的 API Key 和参数
3. **上传设计图/文档**：拖拽上传 UI 设计图、截图或需求文档
4. **AI 分析模块**：AI 自动识别界面中的模块、功能点和交互元素
5. **审核模块**：确认 AI 分析的模块是否准确，选择用例类型和数量
6. **生成测试用例**：确认后 AI 自动生成测试用例
7. **审批用例**：对待审用例逐一审核，编辑优化后通过或驳回
8. **导出 XMind**：导出为思维导图格式

### 用例格式

生成的测试用例遵循以下结构：

```
模块：用户登录
├── 测试点：登录功能
│   ├── 用例：正常登录 #P0
│   │   ├── 前置条件：用户已注册
│   │   ├── 步骤1：输入正确的用户名
│   │   ├── 步骤2：输入正确的密码
│   │   ├── 步骤3：点击登录按钮
│   │   └── 预期：登录成功，跳转到首页
```

## API 文档

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/projects` | 获取项目列表 |
| `POST` | `/api/projects` | 创建项目 |
| `GET` | `/api/projects/<id>` | 获取项目详情 |
| `PUT` | `/api/projects/<id>` | 更新项目 |
| `DELETE` | `/api/projects/<id>` | 删除项目 |

### 迭代管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sprints` | 获取迭代列表 |
| `POST` | `/api/sprints` | 创建迭代 |
| `PUT` | `/api/sprints/<id>` | 更新迭代 |
| `DELETE` | `/api/sprints/<id>` | 删除迭代 |

### 文件上传

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/upload` | 上传图片/文档 |
| `GET` | `/api/upload/<id>` | 获取文件信息 |
| `DELETE` | `/api/upload/<id>` | 删除文件 |
| `GET` | `/api/images/<filename>` | 获取图片文件 |
| `GET` | `/api/documents/<filename>/content` | 获取文档文本内容 |

### AI 分析 & 生成

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/analyze-image` | AI 分析图片识别模块 |
| `POST` | `/api/generate` | 生成测试用例 |

### 用例管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/cases` | 获取用例列表 |
| `GET` | `/api/cases/<id>` | 获取用例详情 |
| `PUT` | `/api/cases/<id>` | 更新用例 |
| `DELETE` | `/api/cases/<id>` | 删除用例 |
| `POST` | `/api/cases/batch-delete` | 批量删除用例 |
| `GET` | `/api/cases/pending` | 获取待审用例（含原型图） |
| `POST` | `/api/cases/<id>/approve` | 审批通过用例 |
| `POST` | `/api/cases/<id>/fail` | 驳回用例 |

### 模块管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/pending-modules` | 获取待审模块列表 |
| `POST` | `/api/pending-modules` | 保存待审模块 |
| `GET` | `/api/pending-modules/approved` | 获取已通过模块 |
| `POST` | `/api/pending-modules/<id>/approve` | 审批通过模块 |
| `POST` | `/api/pending-modules/<id>/fail` | 驳回模块 |
| `POST` | `/api/pending-modules/batch-delete` | 批量删除模块 |

### 导出

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/export` | 导出 XMind |
| `GET` | `/api/download/<filename>` | 下载导出文件 |

### AI 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ai-configs` | 获取 AI 配置列表 |
| `POST` | `/api/ai-configs` | 创建 AI 配置 |
| `PUT` | `/api/ai-configs/<id>` | 更新 AI 配置 |
| `DELETE` | `/api/ai-configs/<id>` | 删除 AI 配置 |
| `POST` | `/api/ai-configs/test-connection` | 测试 AI 连通性 |

## 项目结构

```
Auto_case/
├── backend/
│   ├── app/
│   │   ├── adapters/        # AI 适配器（Base / DeepSeek / Kimi / Volcengine / General）
│   │   ├── models/          # 数据模型（Project / Sprint / Image / TestCase / PendingModule / AIConfig）
│   │   ├── routes/          # API 路由（upload / testcase / export / config / sprint）
│   │   ├── services/        # 业务服务（CaseGenerator / XMindBuilder）
│   │   ├── utils/           # 工具函数（prompt_templates / file_utils）
│   │   └── __init__.py      # Flask 应用工厂 & 蓝图注册
│   ├── run.py               # 启动入口
│   └── requirements.txt     # Python 依赖
├── frontend/
│   ├── src/
│   │   ├── api/             # API 请求封装
│   │   ├── components/      # 可复用组件
│   │   ├── router/          # 路由配置
│   │   ├── stores/          # Pinia 状态管理
│   │   ├── views/           # 页面组件
│   │   │   ├── Upload.vue   # 上传 & AI 分析
│   │   │   ├── ModuleList.vue  # 模块管理 & 审批
│   │   │   ├── CaseList.vue    # 用例列表 & 审批
│   │   │   └── Settings.vue    # AI 配置管理
│   │   ├── App.vue
│   │   └── main.js
│   └── package.json
├── storage/
│   ├── uploads/             # 上传文件存储
│   └── exports/             # 导出文件存储
├── docs/                    # 项目文档
├── .env.example             # 环境变量模板
└── README.md
```

## 架构设计

### AI 适配器模式

项目使用适配器模式统一多 AI 服务商的调用接口：

```
BaseAIAdapter (抽象基类)
├── DeepSeekAdapter    — DeepSeek API
├── KimiAdapter        — Kimi (Moonshot) API
├── VolcengineAdapter  — 豆包 (字节) API
└── GeneralAdapter     — 通用 OpenAI 兼容 API
```

所有适配器实现统一的 `generate_cases` / `optimize_image` / `generate_from_text` 方法，`CaseGenerator` 服务无需关心底层调用差异。

### 业务流程

```
上传设计图 → AI 分析模块 → 审核模块 → 生成用例 → 审批用例 → 导出 XMind
                ↓                            ↓
        AI 适配器调用              待审/通过/驳回 状态流转
```

## 注意事项

- 图片大小建议不超过 16MB
- 支持 JPG / PNG / WebP / DOCX / DOC / MD 格式
- API Key 请妥善保管，建议在设置页面配置而非硬编码
- 本地运行即可，暂不支持部署

## 许可证

MIT License
