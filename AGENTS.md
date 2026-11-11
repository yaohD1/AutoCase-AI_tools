# AutoCase AI 开发地图

## 工作规则

- 先确认任务涉及的前端/后端边界，再读取相关文件。
- 优先复用现有 Vue、Element Plus、Flask、SQLAlchemy、Axios 模式。
- 不修改 `storage/` 中的运行数据、上传文件和导出文件。
- 修改后端模型时同步检查 `backend/app/models/__init__.py`、数据库初始化和删除级联。
- 修改 API 时同步检查对应的前端 API 封装和调用页面。

## 项目地图

### 前端

- `frontend/src/App.vue`：全局壳层、顶部导航（品牌 + 深浅色切换 + 设置）、全局 Element 覆盖。
- `frontend/src/styles/motion.css`：全站动效，只做两件事 —— 首页首帧入场（页头 0ms 起手 → 240ms 画线 340ms → tab 400ms → 入口卡 480ms → 卡片 560ms，每块 620ms、上移 16px、`cubic-bezier(.16,1,.3,1)`）、路由切换淡入（`.page-enter-*`，240ms、上移 4px）+ 内容区顶部 1px 导航线（`.nav-line`）。
  - 首页那条「线条先行」是 `.mode-tabs::after`（tab 行下的 1px 横线）做 `scaleX`。**选择器必须带 `::after`**：挂到 `.enter-rule`（nav 本身）上会把整行横向压扁，宽度算成 0。
  - tab 文字单独动（`.mode-tab`），容器不整体淡入 —— 父级 `opacity` 会连伪元素一起淡，线就抽不出来了。
  - 页头必须 0ms 起手。让它等线画完再出现的话，前 0.3 秒页面全空、只有一条线悬着，像渲染坏了。
  - **只写 enter，不写 leave**：离开动画一存在，旧页面还没走完新页面就压上来，`keep-alive` 的缓存切换会跟着抖。
  - 一律关在 `@media (prefers-reduced-motion: no-preference)` 里；`reduce` 下 `.nav-line` 必须 `display:none`，否则会留一根永远亮着的蓝线。
  - 「从别的页回到首页」的轻版就是路由过渡本身（keep-alive 重新激活同样走 enter），不另写一套。
  - 首页入场只在本会话第一次打开时播（`Upload.vue` 的 `playEntry`，`sessionStorage['ac-entered']`），演完 1400ms 把类摘掉 —— 不摘的话 `keep-alive` 把页面搬回来时元素重新插入 DOM，动画会从头重播。
  - `main.js` 里 `router.isReady()` 之后才 `mount`：直接挂载会先渲染一个空的 `router-view`，首个路由解析完再塞页面，等于首屏白拿一次路由淡入，和首页入场叠在一起。
  - `App.vue` 的 `navTick` 只在真实换页时 +1（`from.name` 为空说明是首次进入，不扫线；只有 query 变化不算换页）。
- 品牌启动幕（冷启动，本会话一次）：`frontend/index.html` 里的 `#ac-boot` + `frontend/src/composables/useBoot.js`。
  - **幕布必须是 HTML 里的静态节点**，样式内联在 `index.html` 的 `<style>` 里。等 Vue 挂载后再插遮罩，中间那段正是白屏 / 半截页面；而放 `#app` 里面会被 `mount` 清空。
  - 判定在 `<head>` 的同步脚本里（`sessionStorage['ac-booted']`，`data-boot="1"`），和主题脚本同一段逻辑；`prefers-reduced-motion: reduce` 直接不给幕布。兜底：8s 未揭幕就自己摘掉，别留一块盖住整站的黑板。
  - 幕布底色 = 页面底色（`--bg-page` 的两套值写死在内联样式里，首帧读不到 token），所以**必须给 `::after` 一条 1px 下沿**：没有这条线，揭到一半看起来不是「幕在走」，而是「内容被裁了一半」。
  - 字标在揭幕时**只淡出、不跟着位移**，否则会看到 `AUTOCASE` 悬在页面顶端一路滑上去。
  - 冷启动这一轮**不叠阶梯入场**，改用 `.upload-page.is-boot-in` 整页淡入 380ms。原因不是重复，是方向对不上：幕布 `translateY(-100%)` 自下而上让开，阶梯自上而下落，下半屏会先露出来而那里的卡片还在 delay 里（opacity 0），揭开过程会有 100~200ms 像少渲染了一块。
  - 淡入用 `--boot-off`（`useBoot.js` 算出的延迟）而不是「揭幕后补类」：补类会让元素先以 opacity 1 亮一帧。延迟要比幕布起手**早 PRE=160ms** —— CSS 动画的 `startTime` 是首帧样式算完的那一刻，实测比写类晚 100ms 出头。
- `frontend/src/router/index.js`：路由入口。`scrollBehavior` 换页回顶（后退/前进仍由浏览器恢复），末尾有 `/:pathMatch(.*)*` 回落首页。
- `frontend/src/views/Upload.vue`：首页、图片/文档上传、知识库入口、模块分析和用例生成。
  - 生成进度是「三段真事件 + 段内缓行」：上传完成、分析返回、生成返回是仅有的三个真信号，段内后端不回传事件。所以 `progressPercent` 用 `1 - exp(-t/tau)` 指数逼近、整体上限压到 90%，最后那 10% 只由接口返回触发。**不要把它改成"真进度"**——后端一次请求到底，中间没有任何可读的中间态；同理也不要往里塞假的子步骤文案。
- `frontend/src/views/CaseList.vue`：已审批用例、用例编辑、待审审批、原型图查看和 XMind 导出。
- `frontend/src/views/ModuleList.vue`：模块列表和模块审批。
- `frontend/src/views/Scripts.vue`：Agent 自动化测试（`/scripts`）。一屏工作台：页头 + 下划线 tab（按需求 / 按用例）+ 左任务 / 右 Agent 配置两列 + 底栏唯一的蓝色「启动 Agent」。「任务记录」是页头右上角的独立按钮，不是配置卡下面的一张列表。
  - 首页入口和「自动化工具」并排成两张等宽卡（`.tool-entries`），不再是一行跳转文字——那一版比通栏弱一个量级，等于没有入口。两个入口都是跳到别的工作台，所以**不要塞进 `.mode-tabs`**：那三个 tab 是页内切换，混进去会让人以为点完还能切回来。
- `frontend/src/views/AutomationRecords.vue`：任务记录（`/scripts/records`），列表页骨架（`.list-page`），项目 / 状态两个筛选 + 表格 + 前端分页（10 条/页），点行进详情。
- `frontend/src/views/AutomationGenerationDetail.vue`：任务详情（`/scripts/generations/:id`），启动 Agent 后落在这里。
- `frontend/src/views/RecordingRecords.vue`：生成记录（`/recording/records`）。一屏两栏：左 320px 记录列表（文件名 + 目录/时间 + 状态方片），右详情（步骤 / 文件预览 / Agent 说明）。提交盒钉在右下角，列表和预览各自滚；`≤1080px` 改上下排列、整页滚动。
- `frontend/src/views/Settings.vue`：模型服务与项目/迭代管理。左侧竖排导航 + 右侧面板（不锁一屏）。模型服务按 provider 分组展示，数据来自 `opencode.models.json`（见下面「模型配置」），Key 由后端 `mask_key()` 给前 6 + 后 4，前端不再二次打码。
- `frontend/src/api/index.js`：所有前端 API 封装。
- `frontend/src/styles/tokens.css`：设计 token + **双主题**。`:root` 是浅色，`[data-theme="dark"]` 是夜墨暗色。四组：表面（`--bg-page/card/sunken/stage/raised/hover/code`）、文字（`--ink/ink-2/muted/faint`）、线（`--line*/line-ctl`）、语义（`--accent*/danger*/ok*/warn*`）。
  - **页面里不准再写颜色字面量**（`#fff`、`#f4f6f9`、`rgba(0,0,0,.06)`）。只有两种情况可以写死：原型截图里的颜色、深色面板上本来就该是白的字/图标。
  - 同一个文件里还有 Element Plus 的 `--el-*` 映射（`:root, html.dark`）：主色的九档浅色用 `color-mix` 由 `--accent` 推出来，`--el-bg-color` / `--el-text-color-*` / `--el-fill-color-*` 全部指向我们的 token。不接管会出现「组件是 Element 的灰、页面是我们的灰」，暗色下拉选中行会变成一坨褐黄。
  - 深色面板（日志、代码、终端）两套主题都保持深色，用 `--bg-code`（浅色 #12161d / 暗色 #0b0f14）：暗色下它比页面更深一档，才不会和卡片糊在一起。
- `frontend/src/composables/useTheme.js`：深浅色切换。`localStorage['ac-theme']` 存 `light` / `dark`；没存过就跟随系统。切换时同时设 `<html data-theme="dark">` 和 `.dark` 类 —— 我们的 token 认前者，Element 的 `dark/css-vars.css` 认后者，少一个就会一半暗一半亮。
- `frontend/index.html`：首屏内联脚本按同一个 key 先把主题定下来。**不要删**：等 Vue 挂载再切会先闪一帧白。键名 `ac-theme` 在三处出现（这里、useTheme.js、localStorage），改的时候一起改。
- `frontend/src/styles/buttons.css`：按钮规范，页面根挂 `ui-buttons`、`list-page`，或位于 `.el-dialog` 内生效（弹窗被 teleport 到 body，不在页面容器里）。
- `frontend/src/styles/list-page.css`：列表页骨架（页头/筛选行/表格/分页/批量条），`/cases`、`/modules`、`/scripts/records` 共用；`.flat-table` 是只借表格外观的轻量入口（首页知识库、设置页在用）。
- `frontend/src/styles/forms.css`：表单字段的全局规范（`.form-block` / `.form-field` / `.form-label` / `.form-ctl` / `.form-input` / `.form-textarea` / `.form-toggle` / `.form-num` / `.form-static`）。**新表单直接套这几个类**，不要再写 `label-width:100px` 的右对齐 `el-form`，也不要再复制一份 scoped 样式。
  - 标签 11.5px 灰在上、值 13px 在下，控件不画边框，只靠字段之间 1px 线分隔；`el-input` / `el-select` 的 wrapper 要 `box-shadow:none; background:transparent`。
  - 全局样式不能用 `:deep()`；class 挂在 `el-select` / `el-input` 上时和 `.el-select` / `.el-input` 是同一个元素，要写 `.el-select.form-ctl .el-select__wrapper`，不能写成 `.form-ctl .el-select ...`（后代选择器永远匹配不到）。
- `frontend/src/styles/dialog.css`：弹窗外壳规范（`.dlg` / `.dlg-split` / `.dlg-viewer` / `.dlg-stage` / `.dlg-panel` / `.dlg-bar` / `.dlg-scroll` / `.dlg-foot` / `.line-editor`）。用法见文件头注释。
  - 标题区 / 内容区 / 操作区各一条 1px 横线；`.el-dialog__body` 归零内边距，左图右表单靠一条竖线分栏，不是两张浮起来的灰卡。
  - 「上一个 / 下一个」放右栏顶部 `.dlg-bar`，footer 只留「决定」（取消 / 不通过 / 通过 / 生成）。四颗按钮挤在 footer 时读不出主行动。
  - `.dlg-split` 高度 `clamp(400px, calc(100dvh - 200px), 700px)`；`el-dialog` 的下边距改 24px（默认 50px 会把 footer 顶到折叠线以下）。
- `frontend/src/components/CaseFields.vue`：用例字段块（模块/测试点/标题/优先级/类型/前置/步骤/预期），`CaseList.vue` 的详情与审批两个弹窗共用。
- `frontend/src/components/ModuleFields.vue`：模块字段块，`mode="review"` 只出五个文本字段，`mode="generate"` 追加用例类型 / 生成数量 / 附带原图。`CaseList`/`ModuleList`/`Upload` 四处弹窗共用，改字段只改这一个文件。
- `frontend/src/components/PathPickerDialog.vue`：工作区目录/目标文件选择对话框，被 `Recording.vue`、`Scripts.vue` 复用。

### 后端

- `backend/app/__init__.py`：Flask 应用工厂和蓝图注册。
- `backend/app/routes/`：HTTP API；`testcase.py` 还包含项目、用例和模块相关接口。
- `backend/app/models/`：SQLAlchemy 模型；`database.py` 负责 SQLite 初始化和轻量迁移。
- `backend/app/services/case_generator.py`：AI 用例生成业务逻辑。
- `backend/app/services/automation_generator.py`：旧版自然语言动作渲染器，历史兼容代码；新自动化任务不再走这里。
- `backend/app/services/opencode_runner.py`：启动 OpenCode orchestrator、写入任务 manifest、收集 Git 变更和生成归档。
- `backend/app/services/oc_text.py`：opencode 的纯文本/图片调用器（`TextWorker` 常驻 serve），用例生成、模块分析、脚本动作生成都走它。旧的 `backend/app/adapters/` 已删除。
- `backend/app/utils/`：提示词、知识库检索、文档和校验工具。

### 数据和运行目录

- `storage/app.db`：SQLite 数据库。
- `storage/uploads`：上传文件。
- `storage/exports`：导出 ZIP/XMind。
- 自动化工作区根目录由 `AUTOMATION_WORKSPACE_ROOT` 配置。

## 核心业务流

- 用例生成（首页）：上传原型图 → `/analyze-session` 拆模块并**挂起会话** → 用户在模块审核弹窗里改名字、删模块、调类型和条数 → `/generate-session` 在**同一个会话**里接续生成 → 用例审批。模块确认清单同时写入模块库（approved）。
- 用例生成（模块库）：模块审批通过的模块 → 按模块选类型和条数 → `/generate`（带 `modules`）生成。
- 自动化脚本：输入自然语言需求 → Python 按顺序驱动三个 OpenCode 会话：planner 探索和规划（校验 Seed 行和用例标题）→ generator 生成 spec → healer 执行测试并修复 → 写入服务端 Git 工作区。阶段间的文件衔接和占位符替换由 `opencode_runner.py` 完成，不再有 orchestrator agent。
- 图片审批：`TestCase.image_id/image_source` 和 `PendingModule.image_id` 负责原型图关联；“附带原图”只决定是否把图片送给 AI，不应影响审批页展示。

## 首页生成：会话挂起（不要改成两段独立会话）

中间层（模块审核弹窗）是产品的核心：用户要能在生成前改名、删模块、调类型和条数。
它和「AI 拆模块」必须是同一轮对话，否则等于放弃可控性。

实现：`session_pool` 存令牌 → 会话元数据（`session_id`/`base_url`/`model`/图片路径），
`case_generator.analyze_in_session()` 拆完模块把会话**留着不删**，
`generate_in_session()` 拿令牌在同一个会话里发「用户已确认的模块清单」。

实测（模型 `4rount-hyper/gpt-6-luna`）：

| 页面 | 同会话接续 | 新会话重发图 | 删掉的模块漏出 | 改名命中 |
|---|---|---|---|---|
| 登录页 | 184s / 30 条 | 249s / 25 条 | 0 | 5/5 |
| 作业编辑页 | 170s / 24 条 | 192s / 24 条 | 0 | 5/5 |

挂起 2 分钟后，模型仍答得出第一轮没提过的占位文字（“请输入手机号或邮箱”），
说明图还在上下文里；让它复述第一轮列过的控件，10 个全对。

三条必须守住的约束：

- **serve 不能被回收**：回收后会话发消息直接 500（临时工作目录被删）。`TextWorker.hold()` 在
  有挂起会话时挡住空闲回收，`release()` 在最后一个会话用完时解除。
- **令牌可能在别的进程**：`session_pool` 是进程内字典，多 worker 部署时拿不到。
  `generate_in_session()` 拿不到就走 `_generate_fresh()`：新开会话 + 重发图 + 用户清单，
  标 `degraded=true`，前端不打断用户。
- **一次别出太多**：单次请求超过约 40 条会顶到 300s 超时（实测 88 条直接失败）。
  `split_batches()` 按模块切批，仍在同一会话里连发，用户看到的还是「一次生成」。

另外：**不要**让模型在同一个会话里「审自己刚生成的用例」——它会只补 1-2 条
（同页面 13 条 vs 跨会话 26 条）。两段式自省必须换新会话、把用例当外部材料。

## 模型配置（opencode）

模型不在数据库里，就是 opencode 的原生配置。三层：

- `opencode.json`（进仓库）：agent 定义、mcp、写权限。**不要往这里写 provider/key。**
- `opencode.models.json`（gitignore，含明文 key）：`provider` / `model` / `autocase` 三段。设置页只改这一份。
- `opencode.runtime.json`（gitignore，自动生成）：上面两份的合并结果，`OPENCODE_CONFIG` 指向它。

读取链路：`opencode_runner._config_path()` 默认返回 `models_config.runtime_config()`；`_emit_config()` 再从这份合并配置里抠出 `recording-emitter` 改权限，所以临时权限配置天然带着 provider。测试/脚本传了 `OPENCODE_CONFIG` 时仍以显式值为准。

四条实测结论（1.18.31，改之前先看）：

- 项目级 provider 与全局 `~/.config/opencode/opencode.json` 是**深合并**，全局有的模型删不掉。
- 模型上的 `disabled: true` 会被 opencode **直接丢弃**，不生效。
- 要砍某个模型必须用 provider 级 **`whitelist`**（实测 `opencode models` 立刻只剩白名单里的）；整块隐藏用 `disabled_providers`。
- `OPENCODE_CONFIG` 与工作区自带的 `opencode.json` 是**叠加**的，不是替换。

用例生成也走 opencode：`models_config.resolve('provider/model')` 给出 `ResolvedModel`，`case_generator` 用 `oc_text.TextWorker` 发 `/session/:id/message`。不再直连 provider——直连那套的 `api_base` 语义是「完整端点」，而配置里存的是 base URL，少了 `/chat/completions` 就整条 404（这个 bug 真的发生过）。旧的 `ai_configs` 表和 `app/adapters/` 都已删除。

### 图片能力（vision）

opencode 收不收图看模型条目的 `modalities.input` 里有没有 `image`，没有就直接回
「当前模型不支持图像输入」，**请求根本不会发出去**。所以：

- 设置页每个模型 chip 上的「多模态 开/关」是个开关，写 `autocase.models[].vision`；
- `_sync_modalities()` 在生成 runtime 配置时把开关翻译成 `modalities`；
- `_vision_flag()` 反过来以 `modalities` 为真相（用户没显式设过时），保证界面和实际一致；
- 默认值是「不支持」——宁可让用户去勾，也不要界面写能看图、实际被拒。

实测：`4rount-hyper/gpt-6-luna` 开图后读截图 9.9s 正确；`cctq-codex/gpt-6-luna` 同图 33.8s，
所以默认推 4rount-hyper。

### 调用开销与坑

- `TextWorker` 是进程级单例、常驻 serve、空闲 30 分钟回收；一次调用约 9s，其中 5–7s 是
  「这个 serve 的第一次模型调用」，所以别每次新建进程。
- 结构化输出只能用 `format: {"type":"json_schema","schema":{...}}`（结果在 `info.structured`），
  `{"type":"json"}` 会 400 `Expected OutputFormat`。但 json_schema 比纯文本慢 3–4 倍
  （2 条用例 44s vs 9s），所以默认用纯文本 + `parse_json` 稳健解析。
- `PromptTemplates.IMAGE_ANALYSIS_PROMPT` 正文里有 JSON 示例，对它调 `.format()` 会
  `KeyError: '
    "module"'`；用 `.replace('{count}', ...)`，单图直接换 SINGLE 模板。

改配置后不会热生效：`/api/models/apply` 只重建 runtime 文件，正在跑的 serve 要等下一次生成才会重启。

## 录制（自动化工具）

- 入口 `frontend/src/views/Recording.vue`（`/recording`），接口 `backend/app/routes/recording.py`，会话 `backend/app/services/recording/session.py`。
- 默认 `RECORDING_LAYOUT=embed`：录制浏览器只开**一个窗口**并最大化，主页面是前端外壳页 `?embed=1&target=<被测地址>`，左边 iframe 是被测系统（占满剩余宽度），右边是可收起/可拖拽的编排面板。外壳页是 AutoCase 自己，靠 init script 传入 `__AUTOCASE_SKIP_ORIGIN` 让 recorder 跳过（否则点面板会被录成步骤）。
- `RECORDING_LAYOUT=split` 用于目标站禁止 iframe 的场景（`/start` 探测到 `X-Frame-Options` / CSP `frame-ancestors` 时自动切）；独立 `--app` 窗口当面板，不注入 recorder、不记为 `new_page`。
  - **默认是左右并排，不是悬浮**（`RECORDING_PANEL_FLOAT=0`）：被测窗口在左、面板在右，面板宽度**按 CSS 像素折算成物理像素**（`_panel_physical_width` = `panel_width(CSS) × dpr`），和嵌入模式那块面板同一个口径。宽度不是配置写死的：`/start` 带上前端 `localStorage` 里那块面板的宽度（键 `autocase.recording.panelWidth.v2`，默认 **520 CSS** ≈ 35% 屏宽），两种布局用同一个数字——否则并排一个值、嵌入另一个值，同一块面板在两个布局里胖瘦都不一样。换过键：v1 里存的是 686（占 47%，用户实测「默认状态太宽」），不换键新默认永远被旧记忆盖住。历史上有两条弯路别再走：按屏幕比例 45% 分（页面只剩 55%，响应式后台退化成移动版）、以及悬浮（面板压住页面右缘，那一截其实看不见，2026-11 用户实测「太割裂」）。
  - **步骤行一律完整，面板宽度固定**：标题右侧那颗「展开 / 收起」文字按钮已经删掉（用户反馈它才是「上移 / 下移是不是被删了」的误会来源——两颗箭头只是被它藏起来了）。`stepsCollapsed` 现在恒 false，`/panel/width` 与前端 `syncPanelWidth()` 都不再被调用，宽度只由 `panelWidth`（localStorage v2，默认 520 CSS）决定。**别再把它接回某个开关**：一颗按钮同时改行密度和面板宽度，用户根本看不出它改了什么。
  - **面板能多窄：摆位到 420 CSS 没问题**（实测 735 物理，SetWindowPos 能压过 `--window-size` 的启动下限）。之前记的「最小 514 CSS」只对 `--window-size` 启动参数成立，别拿它当摆位下限。代码里仍保留「摆完回读 `visible_rect`，被撑宽就按实际宽度把页面退让出来」的自愈，防止别的系统上真被顶住时两窗重叠。
  - **两种布局都能「收起」**：头部 `.panel-collapse` 在 `embedMode` 下收的是本页那一栏（宽度还给 iframe），并排模式下走 `collapsePanel()` → `POST /panel/mode {mode:'hidden'}`，把整扇面板窗口滑出屏幕右缘只留一条把手，点把手 `expandPanel()` 摆回来。**别和步骤区那颗「展开 / 收起」搞混**——那颗管行密度，已经删了；这颗管窗口。
  - **「收起」= 整扇窗口滑出屏幕右缘，只留一条把手**（`PANEL_PEEK_CSS=36` CSS，乘 dpr 折物理）。窗口本身缩不到 36px，唯一做法是把窗口挪到 `ax + aw - peek`，露出的正好是面板自己被裁剩的最左边一条；页面同时让出这条。收起态由前端 `.panel-handle` 渲染——**直接复用 `.embed-rail` 那套样式和 36px 宽度**，两种布局收起后才是同一个东西；宽一点就会露出半截正常界面。还要把整扇窗口**再上移一个标题栏**（`PANEL_CAPTION_CSS=34`）：`--app` 窗口的标题栏是 Chrome 画在客户区里的，Win32 量不出来，不上移的话屏幕右缘那一条顶端会露半个标题栏（图标加半颗按钮），像窗口被裁坏了。点把手 `POST /panel/mode {mode:'expanded'}` 摆回来。收起时**不开粘合**：把手钉在屏幕边上，拖页面窗口不该把它带走。
  - 悬浮（`RECORDING_PANEL_FLOAT=1`）只在显式开启时生效，且**收起时一律走并排把手**——悬浮那边把窗口藏起来就再也叫不回来了。
  - **面板窗口要关掉遮挡检测**（`--disable-features=CalculateNativeWinOcclusion`）：整扇窗口在屏幕外时，浏览器会认为它不可见并把渲染降到几乎停止，点把手回来要白等一秒才画出来。
  - **任务书浮层打开前要先把窗口放出来**（`sheet_windows`）：收起态要先 `show_window` 再 `move_visible`，否则铺了个看不见的窗口。
  - **面板窗口优先走 `--app` 无壳形态**（`_open_panel_app`）：独立浏览器实例 + 独立临时 profile，没有标签栏/地址栏，跟被测窗口贴在一起像 AutoCase 自己的侧栏。起不来（非 Windows、无头、找不到浏览器）才退回「同浏览器再开一个窗口」。
  - 用 **系统 Edge** 而不是 Playwright 自带的 Chromium：后者是 Chrome for Testing，顶部必挂一条「仅适用于自动测试」的提示条，`--test-type` / `--disable-features=ChromeForTesting` 都关不掉（v148 实测），面板只有三四百像素宽，这条横幅能吃掉首屏一整行。Edge 还要加 `--disable-sync --no-service-autorun`（否则拿 Windows 账号弹「同步你的浏览数据」，那是个独立窗口，会盖住被测页面）；`--ignore-certificate-errors` **只在面板 URL 是 https 时加**，http 下加了会被挂一条黄色安全警告条。
  - **`GetWindowRect` 比眼睛看到的大**：Win10 每个窗口左右各约 8px 是不可见的拉伸边框，按矩形贴死视觉上漏 16px，露出来的是桌面壁纸（看着像黑缝，但**不是阴影**）。贴齐必须用 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` 拿可见边界再反推回窗口矩形。实测可见边界对齐后缝只剩 2px 一条线。
  - **粘合**（`winwindows.Glue`）：`SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE)` 监听两个窗口，拖任意一个另一个跟着走。三个坑：钩子必须装在**收消息的那个线程**（OUTOFCONTEXT 回调投递到安装线程的队列）；不能用布尔标志防乒乓（回调异步投递，标志早清了），要记住「我刚把它放到哪」；`idObject` 必须是 `OBJID_WINDOW`，否则子控件移动也会触发。
  - **跟着走 ≠ 只挪位置**（`winwindows.plan_follow`，纯函数、有单测）。两个窗口中间那条缝用户会当分隔条拖，这时必须「靠缝那侧伸缩、**外侧边钉死**」。只同步位置的话，拖接缝等于把对面整个推走（推 A 的右边 → B 整体右移 → B 的右边也跑了），手感是「拖中间，外边的边怎么也在动」。判别方式：比 src 前后两条边的位移，**相等=整体平移**（照搬位移、尺寸不动），**不等=拖边**（接缝跟走、对面从缝那侧伸缩）。高度跟 src 保持一致，不然两个窗口下沿是锯齿。
  - 拖到把对面压到 `SPLIT_MIN_W/SPLIT_MIN_H` 时，要把被拖的那条边**推回来**（`src_fix`），否则窗口继续变宽、接缝裂开。回声判定留 4px 容差：窗口管理器会取整、窗口自己的最小尺寸也会顶住，严格相等会把自己的一次摆位当成用户拖动，两个窗口开始对推。
  - **摆位函数会先还原再摆**（`winwindows.prepare`）：调用方常见场景是某个窗口被最大化过。以前 `is_usable` 直接把最大化判成不可用，整条原生路径被跳过，只剩 CDP 兜底——而**无壳面板是另一个进程的窗口，CDP 够不着**，结果「只有被测窗口动了一下、面板原地不动」。现在 `prepare()` 先 `SW_RESTORE` 再摆；`layout_windows()` 在原生失败且没有 `_panel_page` 时**如实返回 False**，不谎报成功。
  - **刚落还原的窗口要摆两遍**：还原那一刻边框尺寸才从「最大化无边框」变回「带 8px 拉伸边框」，第一遍 `move_visible` 用的还是旧边框，实测 806 会摆成 896、面板右边界直接跑到屏幕外。摆完用 `_pair_is_flush` 校验（位置 / 两窗宽度 / 接缝 0），不对就再摆，最多 3 遍。
  - **没有「重新并排」按钮**（曾有过，鸡肋已去掉）：排一次是 `_run` 启动时自动做的，之后靠粘合跟着走。`RecordingSession.relayout()` 保留成线程安全入口，只给验证脚本 / 测试用，界面上没有入口，也没有对应的 HTTP 路由。
  - **关面板要按窗口所属进程杀整棵树**（`winwindows.kill_tree`）：Edge/Chrome 的启动器把活交给真正的浏览器进程后自己就退了，`Popen.terminate()` 拿的是空壳，窗口会留在桌面上（实测残留过 7 个）。另外模块级 `atexit` 兜底，后端异常退出也不留窗口。
  - 最大化/最小化时跳过跟随（`is_usable`），否则另一个窗口会被拽到屏幕外；任务书浮层（`sheet_windows`）要先解开粘合再铺宽。

- 嵌入模式下「被测页面」的判定是 `_is_app_frame`：按 `base_url` 的 origin 过滤，外壳页（AutoCase 前端）会被排除，`target=_blank` 打开的新标签页仍算应用页面。`goto` / `new_page` 步骤都依赖它。
- 拾取模式（`_apply_pick`）要遍历 `page.frames`——嵌入模式下被测页面在 iframe 里，只对主 frame 执行会失效。
- 面板宽度由前端拖拽决定（localStorage `autocase.recording.panelWidth`），收起后 iframe 占约 97%。拖拽必须 `setPointerCapture` + 拖动期间禁用 iframe 命中测试，否则 pointermove 会被 iframe 吞掉。
- 被录制页面内的浮层（`RECORDING_HUD_PANEL`）**默认关，嵌入模式同样不注入**（`hud_on = hud_panel and not embed`）：面板本身就是常驻的，页面里再浮一张卡片只是把同样的信息说两遍，还挡住被测页面右下角（用户实测截图里两块状态同时显示）。收起态的恢复入口是**窗口自己露在屏幕右缘的那条把手**，不依赖页面里的浮层，所以「收起面板」可以放心提供。浮层代码保留（`recorder.js` 的 `.ac-pill` / `__pwHudAction('expand-panel')` 与 `panel/mode` 接口），要恢复只需把 `RECORDING_HUD_PANEL` 打开。HUD 在 closed shadow root 里：open 形态会被 Playwright 的 text 引擎穿透，把面板文字算成同名候选。
- 面板窄，排版有四条硬约束（写在 260~520px 上都不许破）：
  - **工具栏在面板 / 嵌入模式下一律 `is-lean`**（`{'is-lean': compactMode}`，三列均分 + 小一号按钮）。它以前跟 `stepsCollapsed` 联动，那颗按钮删掉后工具栏会退回松散的展开形态（3+4+2），同一块面板凭空胖一圈（用户实测原话「不行 去掉展开按钮后工具栏用未展开形态」）。**工具栏密度和步骤行密度从此解耦**：工具栏恒紧凑，步骤行恒完整。
  - **头部一律 `flex-wrap:nowrap`，状态文字不参与压缩**。`.panel-title` / `.panel-count` / `.panel-pending` 全部 `flex:none + white-space:nowrap`。少了这条，flex 会把「录制中」压到 20px 宽、一个字一行（实测 320px 面板的真实截图就是这样）。
  - **头部只放图标 + 一个收起箭头**：并排模式（`!panelFloat`）头右侧是「配置」文字按钮 + `›` 收起箭头，点了收起整扇窗口；悬浮模式是暂停 / 停止 / 更多三个 26×24 图标，拾取、配置、关闭浏览器进「更多」下拉。再往里加文字按钮，这个宽度下必然被压成竖排。
  - **工具栏在并排模式下走 `is-lean` 三列网格**（停止 / 暂停 / 拾取元素，刷新页面 / 添加断言 / 等待加载，固定等待 / 封装 / 动作库）；只有悬浮模式才用一行 4 格（刷新 / 断言 / 等待 / 更多，`grid-template-columns:repeat(4,minmax(0,1fr))` + `flex:1 1 100%`）。同理 `is-lean` 的判定是 `stepsCollapsed && !panelFloat`，两套不能混用。
  - **步骤正文单行省略，悬停展开**：`.is-panel .step-text { flex:1 1 0; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }`，`.step-main` 同时 `flex-wrap:nowrap`。不加这条，`word-break:break-word` 让 min-content 变成一个字符，长 URL 会变成每字一行。`goto` 的地址另外用 `.step-url` 单独一行等宽小字（挤在同一行只会剩「打开 https:」）。定位文本仍默认收起、`:hover` 才展开。
- **步骤行的上移 / 下移跟着「收起行 / 展开行」走，不是被删了**：`.step-actions-extra` 在 `stepsCollapsed` 时 `max-width:0 + opacity:0`（留给宽度过渡，别改成 `v-if`），展开行才滑出来。嵌入模式和并排模式共用同一个模板，所以两边永远一致——2026-11 用户以为「这两个箭头早去掉了」，其实是他点了展开；确认后决定**保留**，调序以拖拽为主、箭头做精细微调。
- 目标站若禁止 iframe（`X-Frame-Options` / CSP `frame-ancestors`），可先用 `page.route` 拦 document 响应改 header；仍不行就切 `RECORDING_LAYOUT=split`。

- 暂停录制（`POST /api/automation/recording/pause` `{paused:true|false}`）：浏览器保持可用但不再记步骤，同 Playwright codegen 的 Pause。前端在 `recorder.js` 里用 `state.paused` 直接拦掉 click/input/change（不产生 IPC），Python 侧 `_handle_event` 还有一层兜底（`pick` 例外，拾取照常）；暂停期间不判 idle 超时。状态在 `snapshot().paused`。
- 结束录制只有「停止录制」（`POST /api/automation/recording/stop`），**没有**取消按钮：`session.cancel()` 仅作为内部「中止且不重校验定位」的快捷出口（测试 teardown 在用），不再暴露 HTTP 路由。
- 关闭浏览器（`POST /api/automation/recording/close`）：后端在真正关窗口**之前**就把 `browser_open=false` 的快照回给请求线程（面板可能就活在要关的那个浏览器里）。前端必须**直接用这个响应** `applySession(data)`；再拉一次 `/current` 会和 `_shutdown()` 赛跑，拿回的还是 `true`，按钮要多停 1~2 秒，用户会以为「点一次没反应」再点一次，那时线程已结束，只会拿到 409「录制已结束」。
- 普通模式（`/recording`，不带 `panel`/`embed`）是**一屏工作区**：左边「录制步骤」吃满宽度（1440 下约 1024px），右列 336px 是「准备与生成」——顶部三条配置摘要（起始地址 / 框架 / 目标文件，点开抽屉）、中间检查项与生成结果（超高自己滚）、底部操作区：回放校验 + **钉住**的生成按钮（回放明细 132px 自己滚，别把主按钮顶出去）。整页 `height:calc(100dvh - 100px)`，不出页面级滚动条。
  - **配置不再是常驻一列**：页头「配置」按钮打开右侧 480px 抽屉，遮罩 / Esc / 关闭按钮收起；地址或工作区没填时按钮是主色，把人先引过去。抽屉里字段一个没少，只是卡头藏掉（外层已有标题），开始录制钉在抽屉底部。
  - 抽屉用 `position:fixed` 的 `.cfg-shell` + `translateX(100%)` 滑出，**面板 / 嵌入模式下同一个 DOM 退化成 `display:contents` 的内联卡**（`.cfg-shell.is-inline`），所以配置卡只写一份，别复制第二份模板。
  - 生成按钮和回放校验在 `.rail-foot`（`flex:none`），检查项与生成结果在 `.rail-scroll`（`flex:1; overflow-y:auto`）：两者混在一起，结果一长按钮就被推出视口——这正是旧版「生成按钮埋在最底部」的原因。回放放底部是因为它和生成同属收尾动作，留在中间会在按钮上方留一块空档。
  - 左侧空出来的宽度归步骤区；`.rail-brief` 那三条摘要是「配置列被撤掉」的补偿，**面板 / 嵌入模式里隐藏**（那里自己就能展开配置）。
  - 高度链必须传到底：root(flex) → `.page-col`(flex:1 min-height:0) → `.recording-body`(grid 单列，**必须 `grid-template-rows:minmax(0,1fr)`**) → `.main-layout`(普通模式才是 `grid-template-columns:minmax(0,1fr) 336px`，面板模式仍是纵向 flex)。少一环，步骤卡会被 `minmax(0,1fr)` 压成 0 高。
  - 配置抽屉里仍是单列静物式表单；Python 解释器收进「高级设置」；工作区检测结果内联成一行 chips（`.detect-inline`）。坑：`.el-form-item__content` 默认横向 flex，输入框后的提示会被挤到同一行，需要显式 `flex-direction:column`。
  - 空状态（`.steps-empty.is-roomy`）除了文案还给一颗「配置并开始录制」，直接开抽屉；工具栏首屏不重复「开始」，避免和抽屉里那颗抢主行动。
  - 面板/嵌入模式 600px 宽：`.recording-body` 回落成纵向 flex，配置仍单列；`.side-rail` 是步骤下面一张普通卡（`flex:none`、`rail-scroll` 不内滚）。空状态用 `.steps-empty` 一行提示（不要 el-empty 大插画）；未停止录制时「生成」卡用 `.gen-compact` 一行摘要，停止后才展开 `.checklist`。
  - **`.is-panel`（split 兜底布局的独立面板窗口）的 `.page-col` 必须自己吃高度**：`.recording-page.is-panel .page-col { flex:1; min-height:0; display:flex; flex-direction:column }`。漏了这条，下面几层 `flex:1` / `min-height:0` 全部无处可依，步骤一多整列被撑到 1951px，再被 `#app.is-panel-mode .el-main{overflow:hidden}` 裁掉——表现是「步骤列表滚不动、底下的步骤也点不到」，但 `.el-card__body` 上明明写着 `overflow-y:auto`。嵌入模式和独立整页各自的 `.page-col` 规则不同，别合并。
  - 窄屏（≤1100px）必须解除 `height:calc(100dvh-100px)`，并把 `.main-layout` 改回单列、右栏 `height:auto`、`.rail-scroll` 不内滚：否则步骤卡会被压扁，右栏卡在页面级滚动里被裁。
- 「收起」= 面板的精简模式（`stepsCollapsed`，不持久化，默认展开）。只在 `compactMode` 显示，按钮在步骤卡标题右侧（状态标签旁，`Fold`/`Expand` 图标）：
  - 步骤行只留铅笔，`v-if="!stepsCollapsed"` 掉上移/下移/删除（排序改用拖住编号拖拽，防误删）；`.is-lean .step` 收紧 padding。
  - 工具栏切 `.toolbar.is-lean`：3 列等宽网格 + 通栏细线分组，录制中 6 个按钮正好 3+3；未录制只有 5 个，主按钮 `.lean-wide` 占两列补齐整行，右边界与下一行「添加等待」对齐。
  - 嵌入模式下同时把 `panelWidth` 收到 420 并隐藏 `.embed-resizer`（`startResize` 提前返回），展开时还原收起前宽度；只收不放（本来就 <420 保持原宽）。
- 等页面加载 = `intent.type = 'page_ready'`（工具栏「等待加载」，步骤类型仍是 `wait`）。参数只有两个：`state`（`load` 默认 / `domcontentloaded` / `networkidle`）和 `timeout`（默认 15s，超时**算失败**）。
  - **不加新的 step 类型**。时间线渲染、上移下移、拖拽、回放全按 `type` 分叉，加一个 `ready` 要改一圈；走 `wait` + `intent.type` 只动四层：intent 校验、工具栏草稿卡、两条生成提示词、`replay._assert_once`。
  - 它**不在** `INTENT_TYPES` 里：那是自然语言解析的词表，让模型自己产出这个类型，它会把「等元素出现」也塞进来。这一档只由工具栏按钮直接给，`step_type_for_intent` 里单独判。
  - 要跟用户说清楚 `waitForLoadState` 的真实语义：**当前文档已经到那个状态就立刻返回**。所以它治的是「点击后页面正在跳转、还没 load 完」，不治「SPA 页内异步渲染」——后者还是得用「断言 + 等到成立」等一个具体元素。
  - Selenium 没有等价 API：RF 侧用 `Wait For Condition    return document.readyState === "complete"`。
- 点击引起的跳转不记 `goto`，但落点写在**点击那一步**的 `nav_to` 上（`session._mark_click_nav`，只回看三步，能跨过 `new_page`）。生成时据此补 `await page.waitForURL("**" + 路径)`，否则写出来是「点完直接找下一个元素」，站点一慢下一步就扑空。
  - 只取路径、不写死带查询串的整条 URL；`nav_to` 同时显示在步骤行（`.step-nav`，形如 `→ /home?tab=1`），用来解释脚本里那行 `waitForURL` 不是凭空多出来的。
- 封装动作（`services/recording/actions.py` + 步骤类型 `call`）：录到一串重复操作（登录、新增一条记录）时，选中**连续的**几步合成一个具名动作，原位置替换成一次调用。动作可反复插入、每次传不同实参，生成时写成一个函数。
  - 数据：`steps` 里出现 `{type:'call', action:'a1', args:{...}}`；动作定义在 `session._actions`，子步骤保留完整定位，只把要参数化的 `value` / `label` 换成 `{{key}}` 占位符。解封就是拿实参替换回占位符、原样铺回去，不做反推。
  - **只能选连续区间**。中间跳过一步再合体，展开后的顺序不是用户看到的那个；`_tied_range` 会把区间自动扩到不切断「点击 → new_page」的绑定。含 `call` 的区间不能再封装（不嵌套）。
  - **生成时 call 不展开**（`emit._steps_for_prompt`）：写手要看到「这是一次调用」才会写成函数调用，展开成平铺的 4 步它就真写 4 行，封装等于白做。`_payload` 另给一份 `actions`（子步骤定位 + 占位符 + `called_at`），提示词里写死「同一动作的每次调用必须复用同一个函数」。
  - **回放要展开**（`replay._expand`）：`_run` 只认原子操作。展开会重排编号，报告里的步号是展开后的。
  - 动作库按项目存 `<工作区>/.autocase/actions.json`（录制开始时随 `body.workspace` 载入）。没选工作区就只存在内存里，`snapshot().actions_saved` 为 false，界面标「仅本次」；写盘失败不打断操作，只降级。
  - 删除动作要求它**没有调用点**，否则那些步骤会变成指向空气的调用；解封不删动作定义（别处可能还在用）。改了动作名会同步所有调用卡的显示名。
  - 参数名优先取元素快照里的 `snap.name` / `placeholder`（往往就是代码里的 `username`），中文别名转不出合法标识符才退回 `arg` / `arg2`，用户在封装弹窗里可改。
  - 界面：工具栏「封装」进多选态（行内换成勾选圈、拖拽关闭、Esc 退出），底部浮出「已选 N 步」条；`el-popover` 里的按钮必须给 `popper-class` 加 `ui-buttons`，否则 `primary + plain` 会退化成蓝底蓝字（全站那份兜底规则只覆盖 `.ui-buttons` / `.dialog` 容器）。
- 生成记录独立成页：路由 `/recording/records`，视图 `views/RecordingRecords.vue`。页头用 `.list-page` 那套（灰眉标 + 21px 标题 + 一句陈述），页脚只有「刷新」+ 文字链接「返回录制」。
  - 两栏平卡：左 320px 列表（文件名 mono + 目录/时间，状态用 `.chip`），右详情（步骤表 / 深色文件预览 / Agent 说明），提交盒 `flex:none` 钉在右下角，只有「未提交」才出现；撤销改成右上角的文字按钮，不和主行动抢位。
  - 默认选中最新一条；带 `?id=` 时选中指定那条。录制页只留一个跳转按钮，records 的 state/函数/CSS 不要留在 `Recording.vue`。
  - `≤1080px` 改上下排列（列表限高 240px、整页滚动），不要继续用两栏。
- 按钮规范统一在 `frontend/src/styles/buttons.css`（原名 `workspace-buttons.css`），页面根元素挂 `ui-buttons` 或 `list-page` 类即可生效：
  - 尺寸只有三档：标准 32px、图标 30×30（步骤行）、整宽主行动 `.generate-btn` 36px（独占一行，不与任何按钮并排）；内联开关（「高级设置」）26px。圆角 8px、字号 13px、无投影。
  - 配色三支：主蓝 `#0071e3`（唯一实心）/ 危险 `#e0352b`（描边与文字，实心用 `#ff3b30`）/ 中性描边 `#dfe3e8` + 文字 `#3c4048`。进行中状态（暂停中、拾取中）统一淡蓝 `#eaf3ff`，**不要**用 Element 的金黄 `#e6a23c` 或草绿 `#34c759`。
  - 禁用一律淡出：主按钮 `#f2f4f7` 底 + `#9ba3ae` 字，次级按钮白底 + `#b6bcc5` 字。**不要**用 Element 默认的浅蓝底白字（`#a0cfff` + white，几乎看不清）。
  - 坑 1：Element Plus 在 `.el-button` 自身重新定义了 `--el-button-*` 变量，在容器上改这些变量**无效**，必须直接覆盖属性。
  - 坑 2：次级按钮规则要用 `:where(:not(.el-button--primary)...)` 包排除条件，否则 `:not()` 会抬高特异性，压过后面的 append / text / 图标按钮规则。
  - 坑 3：输入框 `append` 里的按钮高度要跟随输入框（`height:100%` + `border:0` + 透明底），否则 26px 的小白块会浮在灰底上，和输入框错位。
- 步骤可就地编辑（铅笔按钮）与拖拽排序（sortablejs，按住编号拖）：
  - `PATCH /api/automation/recording/steps/<no>` 只允许改「值」：`fill→value`、`select→value/label`、`goto→url`、`sleep→seconds`；断言/等待/点击不可编辑（换元素走拾取）。白名单在 `session.EDITABLE_FIELDS`。
  - `POST /api/automation/recording/steps/<no>/order` 收 `position`（**移除自身之后**的 0 基下标，与 sortable 的 `newIndex` 一致），「点击 → 标签页切换」成对移动。
  - 前端 sortable 的 `onEnd` 必须先把 DOM 还原（`siblings = children.filter(el => el !== item)` 再按 `oldIndex` 插入，直接用 `children[oldIndex]` 会错一位），并清掉 `step-chosen/step-ghost/step-drag` 残留类。
- 定位预览（人眼确认「生成时到底会点到谁」）：步骤行定位区右侧的准星（`.peek-btn`），**任何步骤**都能点，不只是不唯一的。
  - `POST /api/automation/recording/highlight {no, index?, framework}` → 在页面上把这步的匹配元素**全部**框出来并编号，index 指向的那个实心红；`POST .../highlight/clear` 清掉。`POST .../steps/<no>/chosen {desc, index}` 记录用户选定。
  - 编号顺序 == locator 的匹配顺序 == `nth` 的下标，必须来自同一次 `evaluate_all`（`overlay.js`），否则「选 2 号」和生成的 `nth(1)` 会对不上。
  - 框用页面坐标（`rect + scrollX/Y`）且**先 `scrollIntoView` 再算坐标**：反过来做会整体偏移一个滚动距离（实测滚动 300px 后 dx=dy=0 才算对）。
  - **`desc` 必传**：index 是「某一条候选」的匹配顺序，换一条候选就不是同一个元素。`target.chosen` 优先于任何自动选择（`emit._locator_info` 的 `mode='chosen'`），步骤行显示 `.nth(n)`、状态变「已选定」。
  - 停止录制重新校验时 `chosen.index >= count` 标 `index_invalid`（界面红标「序号失效」），**不要静默改回第一个**——那会点到别的元素，比报错更糟。
  - 录制时另存 `target.matches`（那几个元素的行文本），浏览器一关就查不到了。注意 `getByRole` 命中多个时主定位会退到 css 绝对路径（`fragile`），这种步骤**同样**要存，否则用户看不到「其实有 3 个、你点的是第 2 个」。生成时它进提示词，模型据此在 `nth` 那行写注释。
- **定位详情**（准星右边的放大镜 `.loc-detail-btn` → `el-dialog` 单栏 520）：回答「采用的到底是哪条 / 此刻还在不在 / 唯不唯一」。
  - `POST /api/automation/recording/steps/<no>/locate {framework}` → `{ok, code, kind, mode, count, exists, unique, visible, index, recorded_count, page_url, page_gone, candidates, items}`。顺带在页面上把匹配元素框出来（只给数字不画框，等于没验证），`highlight/clear` 由前端关弹窗时调。
  - **「采用哪条」必须复用 `emit._locator_info` / `_pick_locator`**，不要在 session 里另写一份判断——两份判断迟早会「弹窗说 A、生成写 B」。`_locator_info` 顺带返回 `kind/desc/spec` 就是给这个弹窗用的。
  - 候选清单只列**当前框架**那一组（`pool_for`）：Playwright 与 Robot 各有一套，混在一起列十几条用户对不上号；`adopted` 标出生成用哪条。
  - **`div` 的文字定位走 `rf_text_leaf`，不要直接把 div 加进 `TEXT_TAGS`**：`//div[normalize-space(.)='X']` 会把祖先全算进来（一行文字的 div 按钮实测命中 4 个），所以 TEXT_TAGS 一直排除 div，div 按钮只剩 `rf_css`（结构定位，改版就点错）。现在的写法多一个「不能再有同标签、同样文本的后代」约束，只留最内层：文字唯一时命中 1 个且就是被点的那个，两处同名时仍是 2 个。动这条之前先跑 `test_recording_rf_text_leaf`。
  - **Playwright 的文本白名单 = `TEXT_TAGS + ('div',)`（`PW_TEXT_TAGS`）**：PW 的文本引擎只命中「最小的那个元素」，div 可以放心用；RF 对 div 会命中祖先，只能走 `rf_text_leaf`。两边白名单必须对齐，否则同一个可点的 li/td 会出现「Robot 按文字、Playwright 按结构」——用户看到的就是「明明文字不同，系统却认不出」。以前手写的 5 种标签漏了 li/td/p/h1~h6/strong，菜单项（`<li class="el-menu-item">`）正好中招。改白名单时先跑 `test_recording_pw_text_tags`。
  - 结论只有三档：`count==1` 绿「存在 · 唯一」、`count>1` 黄「匹配到 N 个，取第 K 个」、`count==0` 红「找不到」。失败要分 `no_target / no_frame / no_candidate / frame_lost / overlay_failed` 说清原因，别让用户以为是定位坏了。
  - 口径是**此刻**：页面可能已经走到后面几步，弹窗里必须写明页面 URL + 校验时间；`page_gone` 的步骤再补一句「只代表当前页面」。
  - 弹窗宽度写成 `min(520px, calc(100vw - 24px))`（el-dialog 的 `width` 属性）：面板窗口本来就是 400~800，写死 520 在窄面板里会横向溢出。`class` 上的 `max-width` **不生效**——Element 的 `.el-dialog` 根节点拿不到 scoped 的 `data-v-*`。
- **不唯一 = 必选弹窗，不是提醒**（`el-dialog.loc-dialog`，没有关闭按钮，点遮罩和 Esc 都不关）。出口只有两个：「就这个，继续」和「不看这个，允许降级」（`POST /steps/<no>/degrade`，落 `target.degraded`，之后 `blockers` / `needsLocConfirm` / `reviewable` 都放过它）。
  - **不再有「回到这一步的页面」这条主路径**。取而代之的是按下鼠标那一刻截的图：`pointerdown` 的 `precount` 比 `click` 和跳转都早，`_capture_shot()` 就在那里截一张**视口**图（只在不唯一时截，唯一的一次点击不付这 50~80ms），存 `self._shots[shot_id]`，只在内存、不落盘、不进工作区。
  - **图必须裁到被测 frame**：embed 模式的顶层页面是 AutoCase 外壳（左边 iframe + 右边编排面板），`page.screenshot()` 截整页会把我们自己的面板也拍进去；而框的坐标是 iframe 内的，画在整页图上会整体偏移。`_shot_clip()` 取 `frame.frame_element().bounding_box()` 作为 `clip`，`shot.w/h` 直接用 clip 的 CSS 尺寸；元素在主 frame 时返回 None（整页就是被测页面）。这也顺带修好了「被测系统自己套了一层 iframe」的偏移。
  - **fill / press 也要截**：它们没有 `pointerdown`，只有 `input` / `keydown`，以前永远没有图——「很多都显示没截到」主要就是它们。`_shot_for()` 在事件到达时截（这两类不会把页面带走）；`fill` 每敲一个字符来一条，合并到上一条 fill 时直接沿用它的 `shot`，另有 `FILL_SHOT_REUSE=1.5s` 的兜底窗口，否则打字会被一张 80ms 的截图拖死。
  - 截图失败不再 `return None` 吞掉：`_capture_shot()` 回 `{error: '…'}`，`_register_shot()` 原样转给前端，弹窗里写「页面立刻跳转了 / 截图超时 / 浏览器关了」。`SHOT_TIMEOUT_MS=1500`，不能用 Playwright 默认的 30s——截图期间录制线程是停着的，会堵住后面的点击。
  - 图给前端的是 `target.shot = {id, w, h}`（没截到时是 `{error}`），`w/h` 是 **CSS 像素**：有 clip 时就是 clip 尺寸；没有 clip 时从 PNG 的 IHDR 读像素宽高再除以 `devicePixelRatio` 折回来。不折的话 125% 缩放的机器上框会整体偏小一圈。
  - 框用百分比定位（`left = x / w * 100%`），所以弹窗宽度、图片缩放怎么变都对得上；完全在视口外的元素不画框，列表里标「不在视口里」。
  - **整页图直接缩进 500px 的框里是 0.26 倍，这是「看不出 A 和 B 是哪个」的真正原因**，不是没截到。现在分两层：`.shot-stage`（画布，宽高比 = 聚焦框的比）+ `.shot-inner`（整张图按原比例放大后的那块，`left/top/width/height` 全用百分比，所以画布宽度由 CSS 决定也不用重新量）。
  - 聚焦框 `focusRect`（`Recording.vue`）算法：候选框求并集 → 四周留 `FOCUS_PAD=44` → 宽度下限 `FOCUS_MIN_PX=150`（这个数就是放大倍数：画布约 460，460/150≈3 倍）→ 比例夹进 `[1.15, 3.0]`（防止一行表格压成一条横缝、一列按钮压成一条竖缝）→ 夹回图内 → 占满整图 90% 以上就退回整页（候选散在四角时放大反而看不出关系）。**只有宽度设下限，高度别兜底**：给高度兜一个「至少四分之一屏」会把一行按钮压成半屏白。
  - 右侧每个候选带一张「证件照」：同一张图当放大镜，`loc-thumb img` 按元素中心对齐、`thumbScale = min(1, 46*0.78/item.w)`（**最多 1:1，放大只会把几十像素插值成一团糊**），圈用 `thumbRingStyle` 画在元素位置上。屏幕外的元素没有像素可裁，标「屏外」且不画框。
  - 图上和列表都用 **A/B/C 字母**（`LOC_TAGS`），不用数字：数字在缩略图上只有 5px 高。徽标默认压框左上角，会盖住元素开头的字，所以上方有 20px 空间就挪到框外（`shotTagOutside` → `.is-tag-out`）。
  - **`is-origin`（录制时点的那个）不能铺满底色**：`var(--warn-soft)` 会把元素本身的文字盖掉，等于没截。只留 2px 琥珀边 + 7% 透明底。同理 `.loc-thumb-ring` 只描边不填色。
  - 悬停联动：指列表某行（或图上某个框）→ 该框加粗、其余 `.is-dim{opacity:.3}`。两个同名按钮挨在一起时，光靠字母还是会看串行。
  - 元素坐标在 `MATCH_SUMMARY_JS` 里顺手取（`getBoundingClientRect`，视口坐标），和截图同一时刻，所以对得上；`overlay.js` 那套是页面坐标，只用于实时框选，别混用。
  - 有截图就**不再调 `highlight`**：它会 `scrollIntoView`，等于替用户把被测页面滚了——那正是「回到刚才那页」难用的根源。
  - 弹窗版式走 `styles/dialog.css` 的 `.dlg-split`（左图右候选，900 以下自动上下）。**`align-center` 在 Element Plus 2.4 里并不能真正垂直居中**：它只给弹窗加 `margin:auto`，遮罩层还是 `overflow:auto` 的块容器，弹窗会沉到底部。必须在全局样式里把 `.loc-overlay .el-overlay-dialog` 改成 flex（写在 scoped 里无效：遮罩层不是本组件的元素，没有 data-v）。
  - 窄屏（≤900）要覆盖 dialog.css 的 `.dlg-viewer{height:280px}` 成 `height:auto` + `.dlg-stage{flex:none}`：聚焦后的截图是一条横带，撑满固定高度就会上下各留一大块灰。
  - 用 `locator.click()` 写测试时截图会丢：它把 down/up 挤在一起，`precount` 那条 binding 还没到 Python 页面就被 click 带走了。测试里要 `mouse.down()` → 停 100ms → `mouse.up()`（见 `test_recording_shot._click_like_a_human`）。真人点击天然有这段间隔。
  - 生成前还剩 `blockers` 时不再弹确认框放行，而是直接把这几步排队弹出来，选完才能写（`executePlan` 里 `startLocReview()` + return）。
  - **弹窗的生命周期 = 录制的生命周期**：只在 `status === 'recording'` 且浏览器还开着时自动弹；`applySession()` 里一旦发现录制停了、浏览器关了、会话 404 或线程已结束，立刻 `dropLocConfirm()`（只清本地状态，不调后端——这时后端已经只会回 409「录制已结束」）。没有这条，弹窗会变成一块没有关闭按钮的遮罩，把整页锁死，用户只能刷新。
  - **弹窗覆盖三类状态**：`ambiguous`（真·不唯一）、`suspect`（看着唯一、其实指向别的元素）、`fragile`（页面上有多个同名，只有退回 css 结构路径才唯一）。第三类最容易漏：点「删除」这类元素会连同所在行一起消失，录制时只能落到结构定位，事后回列表再框必然框错，所以必须当场选。`row_scoped`（按行锚定）仍然算 `unique`、不弹——它能按业务字段唯一锁定，弹了等于列表每行都吵一次。
  - **自动弹的和手动「逐条看」打开的要分开**（`locAuto`）：前者一停录就收；后者是用户自己要看的，只要浏览器还活着就留着，否则 `applySession()` 每 2 秒轮询会把它刚打开就关掉。
  - 两个动作（选候选 / 允许降级）的 `catch` 里要认 `录制已结束 | 没有录制`：命中就收起弹窗并给一条 info，而不是原地 toast——弹窗还在，用户就没法继续操作。
- 生成入口只有一个任务、两个窗口：主界面 `/recording` 和录制面板 `/recording?panel=1` 都能点「生成并写入工作区」，状态放服务端（`services/recording/progress.py`，随 `snapshot()` 的 `emit` 字段返回），任一处点击后另一处最迟 2 秒变成同一份进度并禁用按钮；后到的请求被 `PROGRESS.start()` 拦成 409（不是错误，前端按提示跟着进度走）。
  - 阶段由 `emit.generate(progress=...)` 打点：prepare → detect → snapshot → writing → check → validate（校验失败退回 repair 再 validate）→ run（执行失败退回 run_repair）→ record → done。百分比只给基准值，前端按 `stage_at` 指数逼近 `cap` 插值（`emitPercent`），避免模型写脚本那几分钟里进度条卡死或提前满格。
  - **停止录制后不能停轮询**：`syncPolling()` 把 stopped/idle_timeout/closed 降到 2 秒继续轮，否则两个窗口都收不到生成进度；`pollEnabled` 只由 keep-alive 的 onDeactivated 关。
  - **两个窗口的配置不是同一份**：面板是另一个浏览器实例（split 模式用系统 Edge + `tempfile.mkdtemp()` 的一次性 profile，embed 模式用受控 Chromium 打开 `?embed=1`），本地存储完全隔离，只在点「开始录制」那一刻由 `_prefill` 把 7 个字段拼进 URL 快照过去，之后两边各改各的、没有回传通道。所以面板里的「录制起始地址 / 工作区 / 解释器」是只读的（改了对本次录制没有意义，只会让下一次莫名其妙换值），只留「生成框架 / 模型 / 目标文件 / 用例名称」可改，并在卡片顶部挂一条 `.cfg-note` 说明「只对本次生成生效」。**不要给面板加隐式回写**：两边同时打开时没有冲突仲裁，会互相覆盖。
  - **框架的权威顺序：用户选择 > 自动检测**。`config.frameworkSource`（`user` / `auto`，随配置一起持久化）决定 `detectWorkspace()` 能不能写 `config.framework`：`user` 时只更新检测 chip 并露出一行「项目里检测到 X，当前按 Y 生成 + 改为 X」，绝不覆盖；面板 URL 里带来的 `framework` 一律算 `user`（那是点开始录制那一刻用户选的）。没有这条守卫时，进页面 / 选完工作区 / 点「检测」都会把 RF 静默改回 Playwright，还会被 `watch(config)` 写回 localStorage——用户的选择再也留不住（实测：`midscene-ui-test` 检测成 `playwright-ts`，本地存 RF，2.5 秒后变 Playwright 且本地被改写）。
  - **后端会话也记框架**：`RecordingSession.framework` 由 `/start` 写入、随 `snapshot()` 返回；`/highlight`、`/steps/<no>/locate`、`/plan`、`/generate` 缺 `framework` 时用它兜底，**不再默认 `playwright-ts`**——面板刷新或旧书签丢了参数，定位也不会静默换一套写法。请求里显式带了就以请求为准（用户中途换框架要能生效）。
  - **`chosen` 只在当前框架内有效**：用户可能在 Playwright 下挑过候选、之后切到 RF，那条 desc 属于另一套候选池。`emit._pick_locator` 不认它，前端 `chosenOf(target)` 也必须一起过滤（`locatorText` / `effectiveStatus` / `reviewable` / `needsLocConfirm` / 截图序号），否则界面会显示「已选定 + Playwright 写法」，而脚本里写的是另一条。

  - 失败原因写进 `emit.error`，冲突（目标文件已存在）`reset()` 成 idle——那是让用户选覆盖或改名，不是失败。
- `recording-emitter` 的写权限有两种范围（`_emit_config(..., scope=)`）：
  - **`workspace`（默认，产品走这条）**：`edit` / `write` 对**整个工作区** allow，只把 `.git`、`.opencode`、`node_modules` deny 掉——能写 git hook 等于能执行任意命令，能改 `.opencode` 等于能改自己的权限规则。规则是「最后命中者生效」，所以 `*` 在前、这三条 deny 在后。
  - **`file`（保底）**：只 allow `target_path` 一个文件。它匹配的是**相对工作区路径**：opencode 内部是 `ask({permission:"edit", patterns:[path.relative(worktree, filepath)]})`，所以根目录文件必须原样写 `"x.spec.ts": "allow"`，只写 `**/x.spec.ts` 或绝对路径**一条都命中不了**，最后只剩 `*: deny`，表现是「agent 说权限被拒、没写出目标文件」。
  - 相对路径是按 **git worktree** 算的，不是 cwd。工作区不是 git 仓库时算出来的路径对不上任何规则，写入一律被拒（报 `a rule prevents you from using this specific tool call`）。真实流程里 `validate_workspace` 强制「git 仓库根」，所以没问题——但**写验证脚本时必须先 `git init` + commit**，否则会误判成权限坏了（实测踩过三次）。
  - 1.18.31 暴露给 recording-emitter 的**写工具叫 `apply_patch`，没有 `write` / `edit` 这两个工具名**（配置里 `"write": true` 只是把它打开）。提示词里不要写「用 write 工具」，那会让模型以为自己没有写能力。
  - `bash` / `task` 两种范围都 deny。`tools.bash` 也保持 `false`：**只改 permission 不打开 tools，工具根本不会出现**（踩过一次，模型说「我没有 bash」其实是没这个工具）。
- **规划轮和写入轮是同一个 opencode session**（`services/recording/emit_session.py`：`SessionRegistry` + `EmitSession`）。以前 `/plan` 和 `/generate` 各起一个 serve、各建一个 session，模型读完项目，写轮从零开始；修复轮还要再来一次。
  - **一个 session、一个 serve**：写权限一开始就是整个工作区（只排除 `.git` / `.opencode` / `node_modules`），规划期就能用最终那份配置起 serve——规划走只读的 `recording-planner`，写入和修复换 `recording-emitter`，中途不重启进程。
  - **为什么不再收窄到单个文件**：收窄依赖 `target_path`，而那个路径要到任务书轮结束才知道，只能换 config 重启 serve（就是上一版的「一个 session、两个 serve」）。更关键的是它**看着严、实际脆**：opencode 按 `path.relative(worktree, filepath)` 匹配白名单，工作区不是 git 仓库根时一条都命中不了（验证脚本连踩三次）；1.18.31 上模型实际调用的是 `apply_patch`，它的路径检查和白名单对不上，被误拒过；而它只覆盖三个写工具，拦不住任何别的通道。现在改成「允许写 + 事后清单 + 手动还原」，用能还原替代拦得住。
  - **降级只有一级**：serve 还在就正常走完三轮；serve 提前退出 → `begin_write` 返回 `reused=False`，调用方退回一次性生成（重新读项目，界面会说）。没有第二个进程可退了。
  - **越界不自动还原**：`guard.capture()` 把越界文件在生成前的内容导出到 `.autocase/backups/<id>/manifest.json`（`restore` 有原内容 / `delete` 是新建 / `skip` 拿不到），前端列清单 + 单个/全部还原（`POST /recording/backups/<id>/revert`）。不自动删的理由：模型确实可能需要写 helper，自动删会把有用的东西一起删，也会误伤用户在生成期间自己的编辑。**导出必须紧接生成结束**——`stash create` 的 commit 没有 ref，git gc 之后就取不到了。
  - **`bash` / `task` 仍然 deny**：文件改动能还原，命令造成的破坏不能。实测放行 `npx playwright test*` 也不划算——模型看输出必然加 `2>&1 | tail`，而管道会被**整条**拒掉（opencode 会解析 `&&` / `;` / `|` 后逐段校验，这点反而是好事），每拒一次浪费一轮。`tools.bash` 也保持 `false`：只改 permission 不打开 tools，工具根本不会出现。
  - **写轮仍然发全量 payload**：规划轮用的是 `_plan_step`（故意不含 locator），也没有 `keywords` / `samples` / 用户确认后的 plan。会话只负责省掉「重新读项目」，事实仍由 payload 保证；即使 opencode 触发了上下文压缩也不丢。
  - **生命周期**：`/plan` 建，`/generate` 的 `finally` 关；文件冲突 409 **不关**（用户点覆盖还要接着写）；新录制开始 `close_all()`；1h 空闲 / 4h 上限 / 8 轮上限兜底。
  - **建会话和发消息的 model 形状不同**：`POST /session` 要 `{id, providerID}`，`prompt_async` 要 `{providerID, modelID}`。传错直接 400 `{"_tag":"BadRequest"}`，实测踩过一次。
  - **模型名要先校验再派发**（`OpenCodeRunner._resolve_model`）：OpenCode 对不存在的 provider / model 既不报错也不排队——`prompt_async` 照样 204，会话却永远不进 busy，只能等满 60 秒再抛「OpenCode 派发后 60 秒内没有开始处理」。库里真实的失败记录就是 provider 首字母写成 `4Rount-hyper`（配置里是 `4rount-hyper`）：写对 1.5 秒开始，写错 60 秒不动，会话里连一条 assistant 消息都不留，事后完全查不出原因。现在派发前先查 `models_config.flat_models()`：只是大小写不一致就按配置里的写法纠正并打一条 warning，真不存在就直接抛错并给出最接近的三个名字。读不到模型列表时跳过校验，不因为读不到配置挡住生成。agent 名写错的表现一模一样（也是 204 + 永不 busy），但 agent 名是代码里写死的，不在这里校验。
  - **任务书存服务端**（`progress.remember_plan()` → `/recording/current` 的 `emit.plan`）：刷新页面能恢复，不用重规划——重规划意味着模型重新读一遍项目。
  - **取消**：`POST /automation/recording/emit/cancel` → `REGISTRY.cancel()` 先 abort 再收进程树。路由在 `EmitError` 分支里先看 `progress.cancelled()`，把「用户取消」和「生成失败」分成两种说法。

- **执行闭环（Playwright）**：`toolchain.run_playwright()` 在静态校验之后真跑一次生成的脚本，失败交回 `recording-emitter` 修，最多 2 轮（`MAX_RUN_REPAIR_ROUNDS`），同一错误会早停（指纹先把数字抹掉，否则 Playwright 的 `14 × locator resolved` 每轮都变）。
  - 登录态不写进用例：`session._capture_storage_state()` 在**停止录制前**抓（先抓再重校验，重校验会跳页面），`_prepare_generation` 把它传给 `emit.generate`，运行器写成临时文件、用 `AUTOCASE_STORAGE_STATE` 注入，跑完即删。里面有 cookie，不能落进用户仓库。
  - 临时配置必须放在**项目根**（`.autocase.verify.config.ts`）：Playwright 的 `testDir` / `globalSetup` / `webServer.cwd` 都相对配置文件所在目录解析，放进 `.autocase/` 会让 `testDir: './tests'` 变成 `.autocase/tests`，一个用例都匹配不到（实测过）。产物 `--output` 放仓库外临时目录。
  - 判失败要同时看 `stats.unexpected` 和**顶层 `errors`**：配置错时 stats 全是 0，只看 stats 会把「一个用例都没跑起来」误判成「跑通了」。
  - `env_error`（`ERR_CONNECTION_REFUSED` 等）不自动修，只标注「站点不可达」；没有登录态时也必须在结果里说明「失败可能来自登录」，否则用户会去改本来正确的定位。
  - Robot Framework 不跑：录制侧只有 Playwright，Selenium 环境在另一边，硬跑只会得到假失败。`run` 为 `null`，界面照旧只显示静态校验。
  - 跑失败**不算生成失败**：文件保留、记录照建，结果里 `run.ok=false`，界面用琥珀色标出来。校验失败才走 `EmitError`。
- **项目记忆 = 项目地图**（`services/recording/projectmap.py` + `services/recording/memory.py` + 表 `project_memories`，agent `recording-profiler`）：录制一开始后台读一遍项目，产出一份 markdown「项目地图」（整个仓库的目录职责 + 用例写法 + 现成封装），之后每次规划和写入直接注入，不再现场读项目。
  - **不记「坑」**：`pitfalls` 字段已删除（schema / 提示词 / 地图 / 卡片都不再有）。让模型自己判断「哪里容易写错」，产出的常是项目里并不存在的绕行做法，写手会当成项目惯例照抄，反而把代码带偏。要描述项目**是什么**，不是「可能会踩什么」。
  - **结构先由脚本扫，再让模型标**：`projectmap.scan()` 走三层目录（跳过 node_modules / 构建与测试产物 / 隐藏目录），拿到目录树、每个目录的直接子文件数和值得打开的文件清单，作为客观事实塞进提示词；模型只在树上写 `areas`（path/kind/role/use），不再自己 ls/glob。不先扫的话模型会漏目录、会按通用习惯编。
  - **扫描的抗噪声三件套**（`projectmap.scan()`）：① 跳过名单合并项目自己的根 `.gitignore`（只做跳过，不实现 `!` 还原）；② 文件计数只算代码类文件，`SCAN_SKIP_SUFFIXES` 把截图 / 报告 / 二进制 / `report.html` / `output.xml` 全部排除，连数都不数；③ 每个目录最多贡献 `PER_DIR_FILES=120` 条路径。三条缺一不可——实测一个 Robot 项目 `screenshots/` 下有 8036 张截图（3.2G），旧实现只扫到 3 个目录（根、`libspecs`、`screenshots`），四个中文业务目录一个都没进地图，画像的 `layout` / `naming` / `helpers` 全是 null、`areas` 把截图目录当成项目结构。修完是 60 个目录 / 498 个文件，同一个模型产出 8 个真实关键字、10 条断言、完整 snippet，`confidence` 从 low 到 medium。
  - **中文目录名排在字典序最后**：`os.walk` 先走 ASCII 目录，任何「扫到上限就 break」的写法都会先牺牲中文目录。所以 `_pick_dirs()` 超 `MAX_DIRS` 时按「文件多的优先」挑深层目录，而不是按名字截断；深层的空目录直接丢掉（对写手没有信息量），一级目录永远保留。
  - **画像提示词必须框架中立**：不写死 `package.json` / `playwright.config.*` / `test.describe` / `@playwright/test`，改成「先判断框架，再从 README、运行脚本、配置文件里读到实际跑法」。Robot 项目（`.robot` / `.resource` / `Suite Setup` / `Variables`）和 Playwright 项目共用同一份提示词；某一侧不存在的概念（例如 Robot 没有 fixture）明确要求写进 `case_shape`、`fixtures` 留空，而不是硬套。
  - 地图落两处：`<workspace>/.autocase/project-map.md`（给人看、能 diff；`guard.FRAMEWORK_ARTIFACT_PREFIXES` 已排除 `.autocase/`，不算 agent 改文件）和 `project_memories.map_md`（卡片显示，换机器也在）。写文件失败只记日志，不影响学习。
  - `render()` 会**丢掉不在扫描结果里的 areas 路径**：模型偶尔自己拼一个看起来合理的路径，编出来的路径比没有更糟。超 `MAX_MAP_CHARS=8000` 截断。
  - 记录口径：`workspace`（绝对路径，unique）是唯一 key；同一目录就是同一套写法。`fingerprint` = **git HEAD + 关键文件大小 + 用例数量/最新 mtime + 一级目录结构签名**，故意不用 mtime 判内容——切分支会把 mtime 全刷新，白白重学。
  - 学习时机只有两个：`/recording/start` 触发 `start_background()`（延迟 6 秒，别和录制抢 CPU）、用户在卡片上点「重学」。**生成链路里不学**（`_memory_for` 纯读）：一次学习 1~2 分钟，塞进「点生成」等于让用户赌运气，还会和写手抢 CPU。
  - 过期不阻塞：`load()` 返回 `stale=True` 仍注入（旧画像比没有强，提示词里让模型自己留意），下次开始录制时指纹对不上会自动重学。
  - **刷新失败不能作废旧画像**：`learn()` 里 `had_profile` 为真时，except 分支把状态退回 `ready`（只写 `error`）。否则一次网络抖动就让下次生成退回「现场猜项目」。
  - schema 白名单在 `_clean()`：只留 `PROFILE_KEYS`（含 `areas`）。模型爱塞 `best_practices` / `examples`，那些没人校验又会被当惯例注入，必须在入库这一步挡掉。`snippet.code` 不压空白（缩进是代码的一部分），其余文本 `' '.join(split())`。
  - `parse()` 用 `JSONDecoder().raw_decode` **只取第一个完整 JSON 对象**：模型经常在 JSON 后面补一段「说明」，按「第一个 { 到最后一个 }」切会变成 `Extra data` 解析失败（真实踩过一次，白等三分钟）。
  - `parse()` 认「没学到」：runner / layout / case_shape / helpers / snippet 全空就返回 None，不覆盖上一份。实测在只有一个空 `.robot` 的假工作区上，模型会老实回 `case_shape: "无法观察"` + `confidence: low`，这正是想要的。
  - 注入：**有地图就只给地图**（`for_plan()` 返回 `{'map'}`，`for_writer()` 再补 `snippet`）。地图本身就是 layout / naming / helpers 的渲染结果，两份一起给等于同一段话说两遍，还会在截断边界上互相打架。没有地图（老记录、渲染失败）才退回结构化字段。
  - `snippet` 仍然要经 `emit._payload` 顶掉 `detect.samples`：mtime 取的前 60 行常常只有 import 和 describe 开头，helper 与断言习惯全在后面。
  - 优先级写进提示词：`plan`（本次任务书）> `project_memory`（项目地图）> 通用规则，冲突时以 plan 为准；地图是某个时间点的快照，和眼前文件冲突时以文件为准。
  - `start_background()` 在 `app.config['TESTING']` 下直接返回：路由测试会走 `/start`，不拦的话后台线程会真调 provider、真往库里写临时工作区的画像（实测污染了 4 条）。
  - 删画像走 `DELETE /api/automation/recording/memory`。**这条通道必须留着**：模型学歪了没有擦除手段，用户就只能关掉整个功能。


### 调试回放（`/recording/replay`）

- **和「快速校验」是两条路，别合并**。`POST /replay` 一口气跑完只给一份「第几步失效」的报告（`replay.py`）；调试回放要让人看着它跑，所以自己解释执行 IR（`debug.py`），多了步间节奏、控制闸门、画面帧三样东西。两者共用同一份 IR、同一套 `_locator` / `_run_step`，结论不会互相打架。
- **为什么不用 `playwright test` / `robot` 子进程跑生成好的脚本**：那两个一启动就由不得我们，题目要的「暂停在某一步」在子进程里做不到（只能 kill）。要停就必须自己一步一步调 Playwright。「跑真实脚本」是另一件事，另开一条沙箱链路，不要试图塞进这里。
- **暂停的物理边界**：Playwright 的一次 `click()` / `expect()` 不能从外部打断，按暂停会在**当前这一步执行完之后**停在下一步前。这不是 bug，UI 上也别承诺「立刻停」。闸门在每步之前（`_gate`），暂停期间自己抓帧，画面才不会冻在上一帧。
- **单步不走步间停留**（`_dwell` 看到 `_stepped` 直接返回）：用户点「单步」的意思是「走一步然后停」，再停 0.55 秒只会让他以为按钮没生效。同理，`_dwell` 里要检查 `_paused` / `_stopped` 提前退出。
- **当前步指向「即将执行的那一步」**：`index` 在进闸门之前就前移。反过来的话暂停时高亮的是刚做完的那步，看着像卡在上一行不动。
- **元素高亮改元素自己的 `outline`，不插 DOM 节点**（`_HL_ON`）：插一个 div 当覆盖层会被自己的定位器数进去，回放时「命中 1 个」突然变「命中 2 个」，排查半天才发现是自己画的框。备份值存在元素的 JS 属性 `__acOutline` 上，不进 DOM 属性。
- **画面走 MJPEG**（`/debug/stream`，`<img src>` 直接吃）：比 SSE 少一层 base64（省 33%），比自己写 canvas 少一堆重连逻辑。抓帧是执行线程里 `page.screenshot(type=jpeg, quality=55)`，0.09 秒最小间隔（≈11fps）；暂停和步间停留期间持续抓，执行中每步前后各抓一张。**同一画面重复压进来只是浪费带宽**，长度没变就当没变（误判的代价只是少发一帧）。
- **外部浏览器的 screencast 拿不到帧**：`connect_over_cdp` 连上去之后 `Page.startScreencast` 一帧都不出（实测），只能 `Page.captureScreenshot` 轮询（实测 25.6 fps，够用）。自己 `launch` 的浏览器 screencast 正常（13 fps），但 RF/Selenium 沙箱那条链路必须是外部连，所以统一按截图轮询设计。
- **失败自动暂停**：`status=failed` 且 `_paused=True`，画面停在出事那一刻。前端靠 `state.error` + 失败步骤的 `error` 两处显示——列表里只有一行红字，底部错误条放完整原因（定位原文、实际值、调用日志）。
- **断言/等待步骤必须有中文 label**（`INTENT_LABELS`）：这类步骤没有 `alias`，不补的话右侧列表和底部详情只剩一个「assert」，用户不知道它在等什么。
- 前端轮询 `/debug/state`（260ms），收到 `done/failed/stopped` 就停；`<img>` 断流后重连一次拿最后一帧（后端在 5 秒无新帧时主动断开，避免中间层掐死后永久黑屏）。
- 单例：同时只允许一场调试回放（`DebugBusy`）。页面 `onBeforeUnmount` 会 `stop`，刷新页面则先 `refresh` 接上已有会话，不要重开一个把人家顶掉。

## 自动化脚本约定

- 当前只支持 Playwright + TypeScript；自动化入口直接调用 OpenCode，不依赖 AutoCase 已生成用例。
- `Base URL`、工作区、脚本目录、浏览器、storageState、OpenCode 模型和最大修复次数保存在 `AutomationConfig`。
- 生成批次保存在 `AutomationGeneration`，接口位于 `backend/app/routes/automation.py`。
- 工作区路径必须位于 `AUTOMATION_WORKSPACE_ROOT` 内；Agent 产物按批次写入 `autocase/runs/<run_key>/`，默认拒绝覆盖已有文件。
- 工作区必须是后端可访问的 Git + Playwright 项目；脚本会保持未提交状态。
- OpenCode 配置由服务端 `OPENCODE_CONFIG` 固定，前端不能传任意命令、配置或 prompt 路径。
- `storageState` 必须存在且位于工作区内。凭据可由 agent 从 `.env*` 读取；生成的脚本通过 `process.env` 取值，不写死在脚本里。
- 生成接口返回任务后在后台运行，OpenCode 超时由 `OPENCODE_TIMEOUT` 控制；状态和事件写入 `AutomationGeneration`。
- 自动化详情页展示 planner 文档、生成脚本、healer 记录和 Git 变更；本地 commit 只提交当前 `run_key` 批次文件，不自动 push。
- `GET /api/automation/generations?project_id=&limit=`：默认 20 条（脚本页只用来判断「有没有任务在跑」，按钮上的小蓝点），记录页传 100；上限 200。
- 页面分工：`/scripts` 只管发起新任务 + 保存配置；`/scripts/records` 管历史；`/scripts/generations/:id` 管单次运行。三处互相跳转都带 `project_id`，不要把它丢了。
- 详情页布局：页头（返回 / 状态 / 需求 / 进度 / 5 段阶段条）+ 两栏（左「文件与记录」，右日志）。左侧分组区自己滚，**提交盒 `margin-top:auto` 钉在底部**——不要把它放进滚动区，否则文件一多就找不到提交/撤回。侧栏文件只显示文件名（同批次目录都一样），完整路径放 `title`。
- 脚本页一屏锁 `calc(100dvh - 116px)`；配置卡九项在 900 高下刚好放满（字段 32 高、上下留白 9/6），再加字段先量 `bodyScroll > clientHeight`。`max-height:840px` 时解除锁定改整页滚动，否则配置卡会截在中间。

## 自动化实时日志机制

- 不用 `opencode run --attach`（IDE/沙箱里会死锁）；改起 headless `opencode serve` + HTTP 驱动：每个阶段 `POST /session`（agent=planner/generator/healer+model）建会话 → `POST /session/{id}/prompt_async` 派发该阶段的 dispatch 行（agent 必须写在 prompt 请求里）→ 订阅 `/event` SSE 旁观活动。阶段顺序由 `run()` 里的 `run_phase` 调用决定；测试执行是 healer 内部的 `test_run` 工具动作。
- `opencode_runner.py` 把 SSE 事件映射成前端日志三元组 `(stage, event_type, message)`（`_map_bus_event`/`_map_part`/`_map_delta`）。约定：`session.idle` 只表示“本轮结束”、措辞不得暗示任务成功；`patch`/`step-*`/`file` 等内部 part 过滤不入日志。
- 前端 `AutomationGenerationDetail.vue` 按 agent 分组渲染（总控 orchestrator + planner/generator/healer），每组一个 sticky 标签、组内日志持续追加，不逐条重复 agent 名（仿 OpenCode TUI）；测试执行日志归入 healer 组。事件存 `AutomationGeneration.event_log`，前端按 `event_cursor` 轮询增量拉取。
- 时间：后端统一存 UTC（`datetime.utcnow()`），前端 `parseUtc` 补 `Z` 再转本地（北京）显示；勿把 UTC 当本地直接 `new Date()`。
- 判定都在 runner 的 `run_phase` 等待循环：当前阶段会话 `session.idle`（busy 后）=本阶段完成；`OPENCODE_TIMEOUT`（默认 3600s）是全局共享总时长（所有 agent 共用一份，非每个各算）；启动 180s 无活动=未启动；运行中 busy 且 180s 无新事件=停滞（`stalled`）提前中止。

## API 快速索引

- 项目：`/api/projects`、`/api/sprints`。
- 上传/图片/文档：`backend/app/routes/upload.py`。
- 用例与审批：`/api/generate`、`/api/cases`、`/api/cases/pending`、`/api/cases/<id>/approve`。
- 模块审批：`/api/pending-modules`、`/api/pending-modules/approved`。
- AI 配置：`/api/ai-configs`。
- 知识库：`/api/knowledge/*`。
- 自动化：`/api/automation/config`、`/api/automation/generate`、`/api/automation/generations`、`/api/automation/generations/<id>/artifact`、`/api/automation/generations/<id>/commit`。
- 录制与路径浏览：`backend/app/routes/recording.py`（`/api/automation/recording/*`）、`backend/app/routes/browse.py`（`/api/automation/browse` 列工作区目录、`/api/automation/browse/files` 列工作区内目标文件）。两者都是只读，且只能浏览 `AUTOMATION_WORKSPACE_ROOT` 内；前端 `PathPickerDialog.vue` 复用这两个接口，避免手填绝对路径。

## 常用命令

- 后端：`cd backend` 后执行 `python run.py`。
- 前端开发：`cd frontend` 后执行 `npm run dev`。
- 前端构建：`cd frontend` 后执行 `npm run build`。
  - **必须用磁盘上的真实路径大小写**（本机是 `d:\autoproject\...`，小写 `autoproject`）。用 `D:\AutoProject\...` 这种"看着对但大小写不同"的写法进来，Vite 的 `html-inline-proxy` 会因为 `realpathSync.native` 解析出的小写路径和入口 id 对不上而报 `No matching HTML proxy module found`（`index.html` 里有内联 `<style>` 就必现）。这不是代码问题，换回真实大小写即可。
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
- 加/改颜色必须同时验证两套主题：`localStorage.setItem('ac-theme','dark')` 后刷新，用脚本扫「背景亮度 > 0.72 的非图片元素」确认没有漏掉的白块（浅色下反过来扫 < 0.22）。
- Python 改动至少运行 `python -m compileall app`。
- 最终说明改动文件、验证命令、未解决限制，不提交未经请求的 commit。
