# 任务计划：录制式自动化脚本生成器（自动化工具）

将此文件作为持久化路线图。阶段状态变化时更新。

## 目标

首页新增「自动化工具」卡片，进入独立页面。点「开始录制」弹出真实 Chromium 窗口，实时记录步骤与唯一定位。工具栏可插入「断言」「等待」模板，用自然语言描述（所选模型解析为固定意图），目标元素可拾取或引用已录元素。点「生成」后，通用生成 agent 按工作区检测到的框架写入目标文件；校验失败则还原并报错。之后沿用现有 generation 的预览、commit、revert。

## 范围

- **第一版框架**：Playwright TypeScript、Robot Framework（SeleniumLibrary，老版本）。其他框架只留接口。
- **运行位置**：运行 Flask 的机器（本地开发工具），不做远程画面。
- **不做**：storageState、脱敏、凭据占位、自动推断断言、改动现有 playwright generator / healer、改动 `Scripts.vue`。

## 已确认的决策

| # | 议题 | 决策 |
|---|------|------|
| 1 | 录制浏览器 | 运行 Flask 的机器弹出有头 Chromium |
| 2 | `data-testid` | 有的有、有的没有；定位标准为唯一（`count == 1`） |
| 3 | 凭据 | 录入什么记什么（字面值），不加占位。生成代码含明文，commit 前可在预览和 diff 中看到，系统不拦截 |
| 4 | 断言 / 等待 | 工具栏插入模板卡片，自然语言描述 + 目标（拾取或引用别名）；卡片下方显示理解预览，确认后生效 |
| 5 | 录制与代码 | 录制只存框架无关的 IR；代码由生成 agent 按工作区框架生成 |
| 6 | 界面 | 独立首页卡片入口；样式不限，要求统一美观。`Scripts.vue` 不改，`Upload.vue` 只加卡片 |
| 7 | 项目形态 | 在 Auto_case 内开发；录制运行时是 `backend/app/services/` 下的独立模块 |
| 8 | LLM | 断言 / 等待的自然语言理解必需 LLM。模型从 `opencode models` 列表选择，可随时切换，默认用 OpenCode 当前默认模型 |
| 9 | 代码生成 | 不用 playwright generator / healer。一个通用生成 agent 所有框架共用，框架差异放在各框架的 prompt 文件里 |
| 10 | 落盘时机 | 录制与断言填写都不写盘。点「生成」时 agent 写入目标文件，校验通过才保留，之后沿用 commit / revert |
| 11 | 出错处理 | 不静默降级。校验失败、同名冲突、定位不唯一、缺校验工具、LLM 输出不合法：都还原文件，并列出具体错误 |
| 12 | 框架检测 | 不做硬性拒绝，只给提示（见「框架检测」）。检测结果只作上下文，不阻止生成 |
| 13 | 生成 agent 权限 | 不给 bash。所有命令由后端执行，agent 只读后端准备好的材料并写目标文件 |
| 14 | 库的关键字依据 | 后端用 `python -m robot.libdoc` 导出关键字清单放进上下文；agent 只能从清单选关键字；`robot --dryrun` 校验存在性与参数个数 |
| 15 | 是否依赖 OpenCode | **已确认：保留。** 模型列表来自 `opencode models`，运行器以工作区为 OpenCode 的 cwd，文件工具与权限现成可用 |
| 17 | 意图解析的 OpenCode 调用方式 | **已决定：每次调用临时启动 serve，同步调用后立即结束。** 理由：不需要常驻进程、不留状态；代价是每次解析多几秒启动时间。若体验不可接受，再改为常驻服务 |
| 18 | 录制会话 | 全局只有一个，不绑定项目；项目、工作区、目标路径在「生成」时才确定 |
| 19 | 录制生成的记录 | **已决定：新建，与现有 generation 完全分开。** 独立表、独立接口、独立回滚逻辑，不出现在现有的生成列表里 |
| 16 | 只有 CSS 兜底或索引定位唯一时 | **已确认：方案 A。** 只要定位唯一即可，索引定位（`.nth(n)`）与 CSS 路径都接受。这类步骤标黄（`fragile`），在预览中列出，不阻止生成。不允许静默 |

## 架构

### 数据流

```
录制（Playwright 采集，内存中）
  → RecordingSession 产出 IR（步骤、定位候选、唯一性、断言/等待的原文与意图）
  → 点「生成」
  → 后端收集上下文（框架检测、libdoc 清单、样例文件、目标路径冲突检查）
  → 通用生成 agent 写入目标文件
  → 后端校验（dryrun / tsc）；失败则让 agent 修一次
  → 仍失败：还原目标文件，报错
  → 通过：挂到 generation 记录，走预览 / commit / revert
```

### 组件（建议布局）

- **后端 `backend/app/services/recording/`**（新模块）
  - `session.py`：录制会话（start / stop / cancel，事件队列）
  - `ir.py`：IR 定义与序列化
  - `locators.py`：候选排序、唯一性与 fragile 标记
  - `intent.py`：自然语言 → 固定 schema 的意图（调用所选模型）
  - `context.py`：框架检测与上下文收集
  - `validate.py`：按框架的校验命令
- **Prompt**：`.opencode/prompts/recording-emitter.md`（通用基础）+ `emit-playwright-ts.md`、`emit-robot-selenium.md`（框架差异）
- **`opencode.json`**：新增 `recording-emitter` agent。`tools` 不含 `playwright*`，`bash` 显式 deny
- **路由**：`/automation/recording/*`（start、events?after、steps 增删改、stop、cancel、intent、generate）
- **前端**：`frontend/src/views/Recording.vue`（新页面）、新路由、`Upload.vue` 加卡片、`frontend/src/api/index.js` 加接口

### 写权限的边界

`opencode.json` 的 `permission` 只能写静态 glob，无法表达运行时才知道的目标路径（与审计计划中 generator 的问题同源）。因此：

- agent 的 `edit` / `write` 在配置里放宽到工作区；
- 后端在运行结束后检查：只允许目标文件改动，其他文件的改动全部还原，并报错。

这是事后检测，不是事前阻止。

### 路径校验

- `workspace_path` 必须在 `AUTOMATION_WORKSPACE_ROOT` 内（`automation_generator.py:114-118`）
- 目标路径必须是相对路径，不含 `..`，且落在工作区内
- 现有 `_file_scope`（`automation.py:1075`）只放行 `specs_path` 与 `plan_path`。录制来源需要单独放行目标文件

### 录制事件规则（来自阶段 0）

- `<select>` 上也会触发 `input`，只由 `change` 记录 select，忽略 select 上的 `input`
- 点击 `<select>`、`<label>`、文本输入框不记为步骤，后续的 `change` / `input` 才是步骤
- 连续 fill 同一元素合并为一步
- 候选跳过 `id`：组件库生成的 id 是动态值（实测为 `el-id-749-6` 这类），不能用来定位
- `<select>` 的 role 名称只取 label，不取选项文字（否则 role 候选 count 为 0）
- binding 要在每个新页面上重新注册（新标签页已验证）

### 定位候选

- **Playwright**：`getByTestId` > `getByRole`（带 name）> `getByLabel` > `getByPlaceholder` > `getByText` > CSS 兜底
- **Robot Framework**：由 IR 映射为 css / xpath；不用动态 id
- 每个候选记录 `count`，主定位为第一个 `count == 1` 的候选
- 所有候选都不唯一：标记 `ambiguous`，阻止生成
- 只有 CSS 兜底唯一：标记 `fragile`（见决策 16）
- 停止录制时重新校验，失效的标记 `stale`

### 框架检测（依次尝试，任一步失败继续下一步）

1. **Python 环境**：工作区内依次找 `.venv`、`venv`、`env`（Windows 看 `Scripts/python.exe`，Unix 看 `bin/python`）。都没有就用 PATH 上的 `python`。
2. **版本**（只读元数据，不导入项目代码）：
   - 首选 `importlib.metadata` 读 `robotframework-seleniumlibrary`
   - 备选 `robot --version`
   - 再备选读 `requirements*.txt`、`pyproject.toml`、`Pipfile.lock`、`poetry.lock`、`setup.cfg`
3. **样例**：取最近修改的 1 到 3 个 `.robot` 或 spec 文件，记录 `Resource`、变量命名、定位方式偏好。
4. **都查不到**：报「未检测到」，按主流写法生成，预览中标注。

判定规则：`package.json` 含 `@playwright/test` → Playwright TS；`robotframework` 依赖或存在 `*.robot` → Robot Framework。

**例外说明**：`libdoc` 会导入 SeleniumLibrary 来导出关键字清单。这不是项目代码，但与「只读元数据、不导入」的原则不同，是为拿到关键字 API 而做的有意例外。

### 校验与失败处理

- RF：`robot --dryrun`。阶段 0 已验证：未知关键字、参数个数错误都会报 FAIL。
- TS：`tsc --noEmit`（需目标项目的 node_modules）
- 失败：把错误回给 agent 修一次；仍失败则还原文件并报错
- 工具缺失：报错，不跳过

## 各阶段

状态只能使用 `pending`、`in_progress` 或 `complete`。

### 阶段 0：技术验证（spike）
- **状态：** complete
- **结果**（脚本与夹具在 `.tmp-spike/`，已加入本地 `.git/info/exclude`，不提交）：
  - [x] Playwright 1.60.0 与 Chromium 已装入 `.venv`
  - [x] 有头 Chromium + `add_init_script` 注入 + `expose_binding` 回传事件
  - [x] click / fill / select 带候选，`count` 在操作瞬间算出；6 步全部有唯一 primary，事后重新校验全部 `unique`
  - [x] 拾取模式：页面原按钮未被触发，目标作为拾取结果返回
  - [x] iframe 内元素可录（frame 候选 count 为 1）
  - [x] 点击新标签页打开的链接后，新页面内的点击被录到
  - [x] 真实登录页 `http://10.121.218.127:8080/login`：只填用户名（未输入密码、未提交），placeholder 候选唯一
  - [x] `libdoc` 导出 SeleniumLibrary 6.8.0：182 个关键字，带参数名（Input Text、Click Element 等均在）
  - [x] `robot --dryrun`：未知关键字、参数个数错误均报 FAIL
- **发现并已修复**：`<select>` 重复记录（input + change）；select 的 role 名称；点击 select / label 产生多余步骤
- **发现但未修复（转为规则）**：动态 id 不可用；拾取的按钮 role 与 text 都是 2 个，只有 `nth-of-type` 的 css 唯一，即决策 16 的情况
- **未验证**：目标 RF 项目的真实 SeleniumLibrary 版本与关键字差异（待用户提供）
- **验收**：通过（夹具 6 步 + 拾取 + 新标签页 + iframe；真实登录页 1 步）

### 阶段 1：录制会话与 IR（后端）
- **状态：** in_progress（核心已完成，剩余项见下）
- **已完成**（代码在 `backend/app/services/recording/`，测试在 `backend/test_recording_core.py`）：
  - [x] `session.py`：`RecordingSession.start_new / stop / cancel / set_pick`；单会话限制（已验证第二个会话被拒绝）；空闲超时 15 分钟
  - [x] 线程模型：会话占一个线程，Flask 只通过命令队列交互；Playwright 回调只入队，在录制线程循环里执行
  - [x] 事件以 `seq` 递增，`events_after(after)` 按游标返回
  - [x] `locators.py`：候选排序（跳过动态 id）、`fragile` 标记（CSS 为主定位）、停止时重新校验（`stale` / `ambiguous`）
  - [x] 录制事件规则：select 不重复记录、点击 select / label 与文本输入框不记为步骤、连续 fill 合并、新标签页记为 `new_page`、跳转去重（点击触发的跳转不单独记为 goto）
  - [x] `ir.py`：步骤类型与目标结构；`public_step` 去掉内部字段
  - [x] `intent.py`：固定 schema 校验（类型、别名必须存在、值与秒数范围）；模型调用由调用方注入；JSON 提取（支持代码块）
  - [x] 手动插入断言 / 等待 / 睡眠、删除步骤（`insert_step` / `remove_step`，仅内存）
  - [x] 验证：17 个单元测试通过；真实会话录制夹具页 8 步（含拾取、iframe、新标签页）全部正确；真实登录页只填用户名通过
- **本轮新增**：
  - [x] 路由 `backend/app/routes/recording.py`（已注册到 `app/__init__.py`）：`POST /api/automation/recording/start`、`GET .../current`、`GET .../events?after=`、`POST .../pick`、`POST .../stop`、`POST .../cancel`、`DELETE .../steps/<no>`、`POST .../steps`（断言 / 等待 / 睡眠）、`POST .../intent`（解析预览，不写入）
  - [x] 意图解析接线：`OpenCodeRunner.ask_text(model, system, text)`，一次性、无工具、临时目录；沿用项目 OpenCode 配置；结束即停止 serve
  - [x] `POST .../steps` 与结构化表单共用同一套 `intent.validate`，LLM 与表单输入的校验一致
  - [x] 修复：停止后再发命令会白等 60 秒超时（改为先看会话状态）
  - [x] 验证：`test_recording_core` 17 个通过；`test_recording_routes` 11 个通过（临时数据库，不碰 `storage/app.db`）；真实调用一次（配置模型 `volcengine-plan/deepseek-v4-flash`）返回 `wait_visible`，目标通过别名校验，无残留临时目录
- **第二轮新增**：
  - [x] IR 回放 `backend/app/services/recording/replay.py`：按步骤记录的标签页（`page_index`）与 iframe（`frame_url`）执行；断言用 Playwright `expect`，失败带步骤号
  - [x] 修复：断言与点击共用同一目标时，停止后的重新校验把主定位清空（按目标去重，并用记录它的框架重算）
  - [x] 修复：新标签页打开后，断言误用弹窗页面（改为按步骤的 `page_index` 选页）
  - [x] 验证：录制 → 插入断言 → 停止 → 回放全部通过；断言写错时在对应步骤失败（`test_recording_replay.py`，3 个）
- **仍未完成**：
  - [ ] 录制会话里的测试驱动参数 `driver` 仍在生产代码中，只供测试使用（见「待你确认」）
  - [ ] 有头模式（你真正看到的窗口）尚未实际运行验证，我的测试都是无头模式

### 阶段 2 进展（工具链，不依赖 OpenCode）
- [x] `detect.py`：找 Python（工作区 `.venv` / `venv` / `env` → PATH，用户可覆盖）；读 SeleniumLibrary 版本（`importlib.metadata`，不导入）；备选 `robot --version`；依赖清单提示；最近修改的 `.robot` 样例（跳过虚拟环境目录）；框架判定（`.robot` 或 robot 依赖 → robot-selenium；`@playwright/test` → playwright-ts；都没有 → 报告未识别）
- [x] `toolchain.py`：`export_keywords`（`robot.libdoc`）；`dryrun`（单文件，缺 robot 时报错）；`tsc_check`（缺 node_modules 时报错）
- [x] 验证：`test_recording_toolchain.py` 9 个通过（RF 相关 3 个需设置 `RECORDING_TEST_RF_PYTHON`，本轮已用 `.tmp-spike/rfenv` 跑通）
- [x] 范围检查与还原（方案 A，已确认）：`guard.py` 运行前备份工作区内容，运行后只允许目标文件改动，越界与校验失败都还原（新文件删除，已有文件恢复）
- [x] 生成 agent：`opencode.json` 新增 `recording-emitter`（无 bash、无 playwright 工具）；提示词 `.opencode/prompts/recording-emitter.md`（通用 + RF 映射）。Playwright TS 的分支提示词已写，尚未端到端验证
- [x] `OpenCodeRunner.emit_recording`：临时 serve，工作目录为目标工作区，`prompt_async` 后先等 busy 再等结束，结束后中止会话并停止 serve
- [x] `emit.py`：收集上下文（检测、关键字清单）→ 调用 agent → 范围检查 → 校验（RF dryrun / TS tsc）→ 失败把输出回给 agent 修一次 → 仍失败还原
- [x] 路由 `POST /api/automation/recording/generate`：需先停止录制；工作区复用 `AutomationScriptGenerator._resolve_workspace`；冲突 409，失败 422 带 details
- [x] 录制生成记录：独立于 `automation_generations`（决策 19）。新表 `recording_generations`（模型 `app/models/recording_generation.py`），服务 `app/services/recording/records.py`，接口 `/api/automation/recording/generations`（列表、详情含预览、commit、revert）。生成前保存原内容用于回滚；生成后记录内容指纹，文件被改动时拒绝提交和回滚；提交与回滚只针对记录里的那一个文件，不调用现有的回滚逻辑
- [ ] Playwright TypeScript 端到端验证尚未做（需要带 node_modules 的测试项目）
- [x] 目标 RF 环境：未提供真实项目，已用 `.tmp-spike/ws-rf` 的临时工作区完成端到端验证

### 阶段 2：生成链路（后端）
- **状态：** pending
- [ ] 框架检测（`context.py`），输出结果与依据
- [ ] 上下文收集：版本、libdoc 清单（`python -m robot.libdoc <Lib> <out.json>`，读取 `keywords[].args[].name`）、样例文件、目标路径冲突检查（已存在则返回冲突）
- [ ] 新增 `recording-emitter` agent（`opencode.json`），`tools` 不含 `playwright*`，`bash` deny
- [ ] 基础 prompt 与两份框架 prompt（`emit-playwright-ts.md`、`emit-robot-selenium.md`）
- [ ] 后端校验（`robot --dryrun` / `tsc --noEmit`），从输出中提取 FAIL 行回给 agent；一次修复重试；失败还原
- [ ] 范围检查：只允许目标文件改动，其他改动还原
- [ ] 生成结果挂到 generation 记录。核对 `models/automation.py` 的 generation 字段能否表达录制来源，不能则加字段
- [ ] 核对 `opencode_runner.py` 的运行目录（cwd），确认 agent 在工作区内运行
- **验收**：两个框架各成功生成一次；人为制造语法错误、同名文件、缺 robot 三种情况，都不留文件，并返回具体错误。

### 阶段 3：前端
- **状态：** pending
- [ ] `Upload.vue` 新增「自动化工具」卡片，只加卡片，不改现有样式
- [ ] 新路由与 `Recording.vue`；不改 `Scripts.vue`
- [ ] 首页到本页的切换与其它页面一致，解决此前「割裂」的问题
- [ ] 顶部配置条：工作区、目标目录、框架（检测结果，可改）、baseUrl、模型选择（可随时切换）。读写复用 `/automation/config`
- [ ] 左侧工具栏：开始录制、停止、拾取、断言、等待、生成
- [ ] 中间时间线：动作、别名、候选下拉、删除、上移；`fragile` 步骤标黄
- [ ] 断言 / 等待模板卡片：别名下拉、拾取、描述输入、理解预览、确认
- [ ] 生成区：代码预览、目标路径、冲突与错误列表
- [ ] 视觉：按 `frontend-design` 做统一美观，样式不限
- **验收**：完整流程可在界面走完；错误以列表形式显示，并指出具体步骤与原因。

### 阶段 4：加固与验证
- **状态：** pending
- [ ] 覆盖 alert / confirm、文件上传、自定义下拉、SPA 跳转
- [ ] 可选：生成后运行一次目标框架的测试命令（做成按钮，默认不跑）
- [ ] 失败案例与原因记录到 `progress.md`

### 阶段 5：交付
- **状态：** pending
- [ ] 检查改动，确认与 `case_markdown.py`、现有 generation 流程、`opencode.json` 无冲突
- [ ] 使用说明：录制、拾取、断言 / 等待、生成、提交
- [ ] 交付给用户

## 风险

- **字面凭据进入 git**：用户已决定，系统不拦截。
- **LLM 断言语义可能错误**：靠校验与预览确认兜底，不能保证正确。
- **写权限收不紧**：配置层无法按运行时路径限制，靠后端事后检测与还原。
- **CSS 兜底唯一但不稳定**：阶段 0 已出现。`nth-of-type` 一旦 DOM 变化就会失效，需要标黄并让用户看到。
- **libdoc 依赖目标环境**：缺库则报错，不猜关键字。
- **框架检测可能不准**：只作提示，不阻止生成。
- **目标 RF 版本未知**：SeleniumLibrary 老版本的关键字可能与 6.8.0 不同，阶段 2 需要用目标版本的 libdoc 结果校验。

## 待你确认或提供

1. **目标 RF 项目的 Python 环境路径与 SeleniumLibrary 版本**（阶段 2 需要，用于确认关键字差异）。阶段 1 不依赖它。
2. **测试用的 `driver` 参数**：`RecordingSession.start_new(..., driver=...)` 只供测试在录制线程里模拟操作，目前留在生产代码中。推荐：保留（改动小，测试必需）。如果你不希望生产代码里有测试入口，我再把它挪到测试辅助里。
3. **意图解析的延迟**：每次解析都会临时启动一个 OpenCode serve（实测整次调用几秒）。推荐：先这样用。如果你觉得慢，我再改成常驻服务。

## 已确认（本轮）

- 保留 OpenCode（决策 15）
- 验证站点：路由器登录页（来自 `automation_configs.base_url`），只做不提交的探测
- 安装 Python Playwright 与 Chromium：已完成
- 同名冲突：弹窗让用户选择

## 备注

- 本轮修改了 `.venv`（装入 Playwright 与 Chromium），未修改任何业务代码。
- 阶段 0 完成，阶段 1 未开始。
- `CLAUDE.md`、`task_plan.md`、`findings.md`、`progress.md` 仍未提交。

## 阶段 3（前端）进展（第五轮）
- [x] 首页「自动化工具」入口卡片（`Upload.vue`，只新增）
- [x] 路由 `/recording` 与页面 `views/Recording.vue`
- [x] 目标与配置（含检测面板、Python 解释器可选填写，配置存在浏览器本地）
- [x] 录制步骤时间线：开始 / 停止 / 取消、拾取、上移 / 下移 / 删除、定位与状态标签（不唯一、结构定位标黄、页面已变化）
- [x] 断言 / 等待 / 固定等待模板：自然语言解析（需选模型）、确认插入；结构化表单（不选模型也可用）；拾取元素后自动选中
- [x] 生成检查清单（不唯一步骤阻止生成）、生成、覆盖确认弹窗、错误列表
- [x] 生成记录：列表、预览、提交说明、提交、撤销 / 回滚
- [x] 验证：`vite build` 通过；无头浏览器冒烟 13 项通过；记录列表与预览渲染通过
- [ ] 未在冒烟中运行：NL 解析与生成的完整流程（真实模型与 agent 调用）、有头模式（用户自行测试）
