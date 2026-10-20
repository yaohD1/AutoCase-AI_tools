# 发现与决策

## 需求
- 录制 → 实时拿到唯一定位 → 工具栏插入断言 / 等待（自然语言 + 拾取或引用已录元素）→ 点生成后按工作区项目类型写入代码
- 录制只保存步骤与定位；代码框架不统一，需要适配器
- 首页新增「自动化工具」卡片，进入独立页面
- 凭据记录原值，不加占位
- 第一版 Playwright TS + Robot Framework（SeleniumLibrary 老版本）

## 代码研究（阶段计划期）
- 仓库内没有任何录制 / codegen 相关代码（业务目录 grep 为空，排除了 `.venv`、`node_modules`、`.git`）
- `backend/requirements.txt` 与 `frontend/package.json` 原本都没有 Playwright；本轮只在 `.venv` 中加入（见 progress）
- 进度接口 `/automation/generations/<id>/events` 以 `after` 为游标轮询（`automation.py:969`，前端 `api/index.js:191`）
- 现有 `commit`（`automation.py:1051`）、`revert`（`:1125`）、`artifact`（`:1005`）以 generation id 为入口
- 工作区与路径校验：`automation_generator.py:114-118`；`automation.py:367-369`
- 配置接口：`GET /automation/config`（`automation.py:389`）、`PUT /automation/config`（`:444`）；前端 `api/index.js:167`、`:173`
- 模型列表：`GET /automation/models`（`automation.py:421`），由 `opencode models` 命令输出解析（`automation.py:421-442`）
- 运行器以工作区为 cwd 启动 OpenCode 服务（`opencode_runner.py:405`）
- `_file_scope`（`automation.py:1075`）只放行 `specs_path` 与 `plan_path`
- 现有 generator / healer 依赖 Playwright MCP 工具（`opencode.json` 中 `playwright-test*`）与 Playwright test runner，不能用于 SeleniumLibrary
- 首页是 `frontend/src/views/Upload.vue`；`Scripts.vue` 不改
- 验证站点来自 `storage/app.db` 的 `automation_configs.base_url`：`http://10.121.218.127:8080/login`。`.env` 只含 `AUTOMATION_WORKSPACE_ROOT`、`OPENCODE_BIN`、`OPENCODE_TIMEOUT`，没有路由器账号变量
- 环境：Windows（Git Bash / MINGW64），`.venv` 为 Python 3.9.13

## 阶段 0 实测
- Playwright 1.60.0 + Chromium 可用；有头窗口正常弹出
- 录制规则：`<select>` 的 `input` 会与 `change` 重复；点击 select / label 会产生多余事件；候选不能用动态 id
- 登录页：用户名 placeholder 唯一；输入框的 `id` 为 `el-id-*` 动态值（Element Plus 生成）
- SeleniumLibrary 6.8.0 的 `libdoc` 输出为 JSON，`keywords` 数组中每项含 `name` 与 `args`（参数名可直接取用）
- `robot --dryrun` 能检测未知关键字（并给出建议）与参数个数错误
- 拾取目标的 role 与 text 候选都为 2，只有 `nth-of-type` 的 css 唯一，这是 fragile 的典型情况

## 阶段 1 实测（录制会话）
- **Playwright 同步 API 不能在 `expose_binding` 回调里再调用同步方法**，否则死锁（`frame.count()` 永远等不到返回）。解决：回调只入队，由录制线程的循环在安全位置执行。异步 API 版本（spike）没有这个问题，因为是 `await`。
- 同步 API 只在调用期间分发事件，所以循环里要定期调用 `wait_for_timeout` 推进事件；驱动脚本也要主动处理收件箱，否则弹窗的 binding 来不及绑定，弹窗内的第一次点击会丢失。
- 新标签页在绑定前可能已经跳转完成，`framenavigated` 不会再触发，需要在绑定时读取当前 url。
- `<select>` 的 `innerText` 包含全部选项文字，不能作为元素名称。
- 弹窗里的第一次操作如果发生在绑定完成之前会丢失；循环每 150ms 处理一次收件箱，正常人工操作不会踩到，自动化驱动需要主动处理。

## OpenCode 一次性调用（已核对，1.18.31）
- `POST /session` 创建会话；`POST /session/{id}/message` 同步返回 `{info, parts}`，文本在 `parts[type=text]`
- 请求体可带 `system`、`tools`（工具名 → 布尔）、`format: {type: 'text'}`、`model: {providerID, modelID}`
- 关闭工具后未出现 `tool` 类 part；`info.error` 非空表示失败
- 与运行器使用的异步 `prompt_async` + SSE 不同，意图解析不需要事件流

## 技术决策
| 决策 | 理由 |
|------|------|
| 定位用自研 JS 注入 + `expose_binding` 回传 | `playwright codegen` CLI 只输出文本，无法实时拿到结构化候选 |
| 录制步骤用模板；断言 / 等待的自然语言理解由 LLM 完成 | 录制定位已确定，无需 LLM；自然语言需要 LLM |
| 代码由通用生成 agent 完成，框架差异放 prompt 文件 | 项目库版本与写法不同，固定模板读不了；不复用 playwright generator / healer |
| 保留 OpenCode 作为底座 | 模型列表、文件工具、权限、运行器已在用 |
| 录制结果存框架无关 IR | 目标项目类型不统一 |
| 唯一定位（`count == 1`）为硬门槛；CSS 兜底唯一但其他候选不唯一时标 fragile | 用户要求「能唯一就行」；fragile 需要用户看见 |
| 出错不静默降级，还原文件并报错 | 用户明确要求 |
| libdoc 导入 SeleniumLibrary 为有意例外 | 为拿到关键字清单；导入的是库，不是项目代码 |

## 遇到的问题
| 问题 | 解决方案 |
|------|---------|
| grep 结果被 `.venv` 刷屏 | 排除 `.venv`、`node_modules`、`.git` 后重搜 |
| 前版计划把录制放进 `Scripts.vue` | 已改为独立页面，并在决策表记录撤销 |
| 多份计划片段仍保留旧方案描述 | 完整重写 task_plan.md |
| 录制 spike 中的重复注册、select 重复事件 | 见 progress.md 的错误表，均已修复 |
