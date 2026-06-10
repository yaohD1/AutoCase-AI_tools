# AutoCase - AI驱动的测试用例自动生成工具

## 项目简介

AutoCase是一个基于AI的自动化测试用例生成工具，支持从UI设计图（Figma导出的图片）自动生成测试用例，并导出为.xmind文件。

## 核心功能

- **图片上传**: 支持拖拽/点击上传Figma设计图
- **AI自动生成**: 支持DeepSeek和GLM-5.1等AI模型
- **多类型用例**: 功能测试、UI交互测试、边界值测试、异常场景测试
- **用例管理**: 浏览、编辑、删除生成的用例
- **XMind导出**: 一键导出所有用例为.xmind文件，按模块-测试点-用例结构组织

## 技术栈

### 后端
- Flask 3.0
- SQLAlchemy (ORM)
- OpenAI SDK (兼容DeepSeek和GLM)
- XMind Python
- Pillow (图片处理)

### 前端
- Vue 3
- Element Plus
- Axios
- Pinia

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
cp ../.env.example ../.env
# 编辑.env文件，填入你的API密钥

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

### AI配置

创建账号并获取API密钥：

- **DeepSeek**: https://platform.deepseek.com/
- **GLM-5.1**: https://open.bigmodel.cn/

在`.env`文件中配置：

```
DEEPSEEK_API_KEY=your_deepseek_api_key
GLM_API_KEY=your_glm_api_key
```

### 用例格式

生成的测试用例遵循以下结构：

```
模块：用户登录
├── 测试点：登录功能
│   ├──用例：正常登录 #P0
│   │   ├── 前置条件：用户已注册
│   │   ├── 步骤1：输入正确的用户名
│   │   ├── 步骤2：输入正确的密码
│   │   ├── 步骤3：点击登录按钮
│   │   └── 预期：登录成功，跳转到首页
```

## 使用流程

1. **创建项目**: 点击"新建项目"，输入项目名称
2. **上传图片**: 拖拽或点击上传Figma设计图
3. **选择AI服务**: 选择DeepSeek或GLM
4. **生成用例**: 点击"生成用例"，AI自动分析图片并生成测试用例
5. **查看编辑**: 浏览生成的用例，可编辑或删除
6. **导出XMind**: 点击"导出"，下载.xmind文件

## API文档

### 项目管理

- `POST /api/projects` - 创建项目
- `GET /api/projects` - 获取项目列表
- `GET /api/projects/<project_id>` - 获取项目详情

### 文件上传

- `POST /api/upload` - 上传图片
- `DELETE /api/upload/<image_id>` - 删除图片

### 用例生成

- `POST /api/generate` - 生成测试用例
- `GET /api/cases` - 获取用例列表
- `PUT /api/cases/<case_id>` - 更新用例
- `DELETE /api/cases/<case_id>` - 删除用例

### 导出

- `POST /api/export` - 导出XMind
- `GET /api/download/<filename>` - 下载文件

### AI配置

- `GET /api/ai-configs` - 获取AI配置
- `POST /api/ai-configs` - 创建AI配置
- `PUT /api/ai-configs/<config_id>` - 更新AI配置

## 项目结构

```
Auto_case/
├── backend/                 # 后端代码
│   ├── app/
│   │   ├── routes/         # API路由
│   │   ├── services/       # 业务逻辑
│   │   ├── adapters/       # AI适配器
│   │   ├── models/         # 数据模型
│   │   └── utils/          # 工具函数
│   ├── run.py              # 启动入口
│   └── requirements.txt    # Python依赖
├── frontend/               # 前端代码（待创建）
├── storage/                # 存储目录
│   ├── uploads/           # 上传的图片
│   └── exports/            # 导出的XMind文件
└── README.md
```

## 开发指南

### 添加新的AI适配器

1. 在`backend/app/adapters/`创建新的适配器类
2. 继承`BaseAIAdapter`
3. 实现`generate_cases`方法
4. 在`CaseGenerator`中注册新适配器

### 自定义提示词模板

修改`backend/app/utils/prompt_templates.py`中的提示词模板。

## 注意事项

- 图片大小建议不超过10MB
- 支持JPG/PNG/WebP格式
- API密钥请妥善保管，不要提交到代码库
- 本地运行即可，暂不支持部署

## 许可证

MIT License