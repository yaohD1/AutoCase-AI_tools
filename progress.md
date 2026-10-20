# 进度日志

## 会话：2026-10-19

### 当前状态
- **阶段：** 0 - 技术验证（已完成）
- **下一步：** 阶段 1 录制会话与 IR（待用户确认决策 16 与目标 RF 环境）

### 已执行操作
- 计划阶段（前六轮）：用 `init-session.sh` 初始化规划文件；只读搜索仓库；按用户回答多次修订 task_plan.md
- 确认：保留 OpenCode；RF 目标库为 SeleniumLibrary；生成用通用 agent；出错不写入
- 阶段 0（技术验证）：
  - `.venv` 安装 Playwright 1.60.0 与 Chromium（用户已同意）
  - 读取配置得到验证站点 `http://10.121.218.127:8080/login`，HTTP 200
  - 登录页只读检查：元素结构；id 为动态值
  - 夹具页录制：有头 Chromium，6 步 + 拾取 + iframe + 新标签页，全部通过
  - 真实登录页探测：只填用户名，未提交
  - 新建 `.tmp-spike/rfenv`（robotframework 7.5 + SeleniumLibrary 6.8.0）：`libdoc` 导出 182 个关键字；`robot --dryrun` 捕获未知关键字与参数个数错误
  - 停止本次启动的夹具服务（8765）；`.tmp-spike/` 加入 `.git/info/exclude`

- 决策 16 确认：方案 A。索引定位（`.nth(n)`）也接受；标黄不阻止
- 开始阶段 1（录制会话与 IR）

- 阶段 1（录制会话与 IR）核心实现：
  - 新增 `backend/app/services/recording/`：`locators.py`、`ir.py`、`intent.py`、`session.py`、`recorder.js`（从 spike 复制）
  - 新增 `backend/test_recording_core.py`（17 个单元测试）
  - 会话加 `driver` 参数（仅供测试，在录制线程内模拟操作）
  - 排查并修复：回调内同步调用导致死锁（改为收件箱）；驱动脚本未处理收件箱导致弹窗点击丢失；新标签页 url 未记录；select 别名取到选项文字
  - 未修改 `opencode.json`、`Upload.vue`、`Scripts.vue` 与现有路由

- 阶段 1 续（路由与意图接线）：
  - 核对 OpenCode 1.18.31（`.env` 中的 `OPENCODE_BIN`）接口：`POST /session/{id}/message` 为同步调用，支持 `system`、`tools`（布尔映射）、`format`；临时启动的 serve 已停止
  - 新增 `OpenCodeRunner.ask_text`、`ASK_NO_TOOLS`；`intent.SYSTEM_PROMPT`；`ask_model` 签名改为 `(system, prompt)`
  - `ir.step_type_for_intent`；会话 `insert_step` 改为 `add_intent_step`（保存原文 `text`，类型由意图决定）；新增 `RecordingBusy`
  - 新增 `backend/app/routes/recording.py` 并注册；新增 `backend/test_recording_routes.py`
  - 修复：`_call` 在会话已停止但线程未退出时会等 60 秒超时，现先检查状态
  - 真实调用：`.tmp-spike/real_ask.py`，配置模型返回 `wait_visible`，无残留临时目录
  - 测试用临时数据库（`DATABASE_URL` 指向临时目录），未改动 `storage/app.db`

- 第二轮（继续阶段 1 收尾 + 阶段 2 工具链）：
  - 会话 `_count` 保留可执行的 `spec`；步骤保存 `frame_url`；新增 `session.ir()` 与 `ir.replay_view`
  - 新增 `replay.py`（按 `page_index` 选页、按 `frame_url` 选框架，断言用 `expect`）
  - 排查并修复两处回放相关问题：共享目标的主定位被清空；新标签页后断言误用弹窗页
  - 新增 `detect.py`、`toolchain.py`；`detect` 的 `_walk` 改为剪枝遍历（避免扫描 `.venv`）
  - 新增 `test_recording_replay.py`（3）、`test_recording_toolchain.py`（9）
  - 未改动 `opencode.json`、前端、`storage/app.db`

### 测试结果
| 测试 | 预期 | 实际 | 状态 |
|------|------|------|------|
| 有头 Chromium 启动 | 弹出窗口 | 通过 | ✅ |
| 注入与 binding 回传 | 事件到达 Python | 通过 | ✅ |
| click / fill / select 候选 count | 每步有唯一 primary | 6 步全部唯一 | ✅ |
| 事后重新校验 | 全部 unique | 全部 unique | ✅ |
| 拾取拦截原页面点击 | 原按钮不触发 | 未触发 | ✅ |
| iframe 内元素 | 可录 | 候选 count 1 | ✅ |
| 新标签页 | 新页操作被录到 | 录到 | ✅ |
| 真实登录页 placeholder | 唯一 | 唯一 | ✅ |
| libdoc 导出 SeleniumLibrary 6.8.0 | 含关键字与参数 | 182 个，参数名完整 | ✅ |
| 单元测试（locators / ir / intent） | 全部通过 | 17 个通过 | ✅ |
| 会话录制夹具页（headless） | 8 步、拾取、iframe、新标签页、停止重校验 | 8 步全部正确，拾取 fragile，无错误 | ✅ |
| 会话录制真实登录页（只填用户名） | goto + fill 唯一 | 通过 | ✅ |
| 同时开两个录制 | 第二个被拒绝 | 被拒绝 | ✅ |
| 会话录制首次（有回调内同步调用） | 正常停止 | 死锁，停止超时 | ❌ 已修复（改为收件箱） |
| 单元测试 test_recording_core（更新签名后） | 全部通过 | 17 个通过 | ✅ |
| 路由测试 test_recording_routes | 全部通过 | 首次 1 个失败（停止后再 stop 超时），修复后 11 个通过 | ✅ |
| 真实 OpenCode 意图解析（配置模型） | 返回合法意图，无残留 | `wait_visible`，目标 "提交"，无残留 | ✅ |
| 回放：录制后插入断言再回放 | 全部通过 | 10 步全部通过 | ✅ |
| 回放：断言写错 | 在对应步骤失败 | 第 9 步失败 | ✅ |
| 共享目标主定位（修复前） | 停止后主定位保留 | 被清空，回放第 4 步报"没有唯一定位" | ❌ 已修复 |
| 工具链（RF 环境） | 导出关键字、dryrun 正确分辨 | 9 个测试通过 | ✅ |
| robot --dryrun 未知关键字 | FAIL | FAIL，提示有效关键字 | ✅ |
| robot --dryrun 参数个数错误 | FAIL | FAIL | ✅ |

### 错误
| 错误 | 解决方案 |
|------|---------|
| `context.on('page')` 对首页也触发，导致 binding 重复注册 | 加去重集合 `WIRED` |
| `<select>` 同时触发 input 与 change，记成两步 | 忽略 select 上的 input |
| select 的 role 名称取了选项文字，candidate count 为 0 | select 的 name 只取 label |
| 点击 select / label 产生多余步骤 | 过滤这两类 click |
| 控制台中文显示为乱码 | 仅显示问题，JSON 内容正确；运行时设置 `PYTHONIOENCODING=utf-8` |
| 第一次汇总脚本读 tuple 出错 | 候选 dump 为 (kind, count) 元组，修正读取方式 |

- 第三轮（阶段 2 生成链路）：
  - 决策：范围检查采用方案 A（录制生成独立检查，只允许目标文件改动，不放宽 `specs_path`）
  - 新增 `guard.py`（备份 / 比对 / 还原）、`emit.py`（生成流程）、`opencode.json` 的 `recording-emitter`、提示词 `recording-emitter.md`
  - `OpenCodeRunner.emit_recording`；路由 `POST /api/automation/recording/generate`
  - 修复：`AutomationScriptGenerator` 导入路径错误；核心测试期望值更新为包含 RF 候选
  - 修复竞态：`prompt_async` 派发后立即轮询会误判"已结束"，改为先等 busy 再等结束（探测脚本证实 agent 正常运行，busy 约 1 秒出现）
  - 端到端（真实 OpenCode + 配置模型，临时 RF 工作区 `.tmp-spike/ws-rf`）：写入 `tests/recorded_login.robot`，dryrun 通过，仅目标文件变化，样例文件未动
  - 测试：`test_recording_emit`（10）、`test_recording_generate_route`（4）；全部 recording 测试 33 + 23 通过，RF 工具链 9 个在 RF 环境下通过

| 测试 | 预期 | 实际 | 状态 |
|------|------|------|------|
| emit 单元测试（假 agent：成功、越界还原、校验修复、校验失败还原、冲突、路径越界、缺工具） | 全部通过 | 10 个通过 | ✅ |
| 生成路由测试 | 404 / 409 / 400 / 200 / 422 符合预期 | 4 个通过 | ✅ |
| 真实 RF 生成（配置模型，临时工作区） | 写入目标文件，dryrun 通过，只改目标 | 写入并通过，仅目标变化 | ✅ |
| 真实生成首次运行 | 写入文件 | 未写出文件（派发后轮询竞态） | ❌ 已修复 |

- 第四轮（录制生成记录，决策 19：新建、与现有 generation 分开）：
  - 新增模型 `RecordingGeneration`（表 `recording_generations`），在 `app/models/__init__.py` 注册
  - 新增 `app/services/recording/records.py`：create / preview / commit / revert，只操作记录里的单个文件
  - 生成路由：生成前保存原内容，成功后建记录，返回 `generation_id`；新增列表、详情、提交、回滚接口
  - `emit.target_ok` 改为公开名字；`Query.get` 改为 `db.session.get`
  - 测试：`test_recording_records.py` 12 个（服务层 8 + 路由 4），含"与 automation_generations 互不影响"的断言
  - 修复测试问题：git 输出需按 UTF-8 解码；生成路由测试需自己指定临时工作区根目录
  - 录制相关测试合计：单元 45 个通过，路由 / 回放 / 工具链 23 个通过（RF 工具链 2 个未设环境变量时跳过）

- 第五轮（前端）：
  - 后端补充：`GET /automation/recording/detect`（可带 python）、`POST /steps/<no>/move`；生成前拦截定位不唯一的步骤（按框架判断，不写任何文件）；`new_page` 与前一步绑定（删除、移动、插入不拆开）；`RECORDING_HEADLESS=1` 环境开关（默认仍为有头）
  - 前端：`views/Recording.vue`（目标与配置、检测面板、录制步骤、断言/等待/固定等待模板、生成与检查清单、生成记录列表与预览、提交/撤销）；路由 `/recording`；`api/index.js` 增加录制接口；首页增加「自动化工具」入口卡片（只新增，不改现有样式）
  - 验证：`vite build` 通过；无头浏览器冒烟 13 项通过（首页跳转、检测、开始录制、断言表单校验、固定等待插入、停止、清单、空记录）；生成记录列表与预览渲染通过（临时数据库，未提交、未撤销）
  - 后端测试：录制相关全部通过（core/emit/generate_route/records/session_rules 一组，routes/replay/toolchain 一组，RF 工具链 2 个未设环境变量时跳过）
  - 环境注意：5000 端口上另有一个旧的后端进程（11:17 启动，不含本轮新增路由），请求会被分到它上面得到 404；请重启后端。5173 上的前端开发服务器也是你自己的，未改动
  - 未做：NL 解析与生成的完整 UI 流程（会真实调用模型与 agent，未在冒烟中运行）；有头模式（你自己测试）；TypeScript 端到端
