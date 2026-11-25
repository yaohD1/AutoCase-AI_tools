# RF 生成改造：录制步骤 1:1 翻译

> 依据：第三轮真跑（6 次生成，0 个能跑，全部缺 `Open Browser`）+ 你两次纠正（不该强制 Setup、不该复用项目封装）。
> 目标：**录了什么就写什么。** 不查项目、不调封装、不让模型猜生命周期。

---

## 1. 为什么这么改

| 事实 | 证据 |
|---|---|
| 六个组全部跑不了，失败点相同 | `No browser is open.` —— 录制 IR 里没有"打开浏览器"这一步，因为录制时页面已开着 |
| 关键字清单反而锁死模型 | A 组重跑直接拒绝生成：「滑块验证无法用本次允许的 Robot Framework 关键字表达」 |
| 精简组的正文其实是对的 | L 组写 `登录` + `拖动滑块完成拼图`、定位全对、断言全对，只缺浏览器初始化 |
| 复用项目封装是错的 | 录了登录就写登录，项目有没有封装、封装好不好都不该影响产物 |
| 滑块能力只在 PW 侧 | `slider.py` 514 行（截图→视觉模型→标定→拖动→重试），`_ensure_slider_utils` 对 RF 直接返回 None |

---

## 2. 目标产物

```robotframework
*** Settings ***
Library    SeleniumLibrary    implicit_wait=10    timeout=10
Library    ${CURDIR}/libraries/AutoCaseLibrary.py
Library    ${CURDIR}/libraries/FilesLibrary.py
Suite Teardown    Close Browser

*** Test Cases ***
终端资产导出与用户列表校验
    # 打开页面（平台生成，用录制的 URL）
    Open Recorded Browser    https://192.168.123.135/#/
    # 输入用户名称
    Input Text    xpath=//*[@id='input-v-1']    admin@ailink.com
    # 输入密码
    Input Password    xpath=//*[@id='input-v-4']    Ailink123456
    # 点击登录
    Click Element    xpath=//*[@data-testid='login-submit']
    # 拖动滑块完成拼图
    Solve Slider    css=div:nth-of-type(1) > canvas:nth-of-type(2)
    # 点击终端管理
    Click Element    xpath=//*[@id='v-list-group--id-tooltip-demo-4']
    # 点击终端资产
    Click Element    xpath=//*[@role='link'][normalize-space(.)='终端资产']
    # 导出并校验文件
    ${file}=    Download File    xpath=//button[normalize-space(.)='导出']    min_bytes=1024
    ...
```

每个动作都来自录制：定位是录的，值是录的，顺序是录的。项目里没有任何一行代码被调用。

---

## 3. 改动清单

### 新增

| 文件 | 行数 | 职责 |
|---|---|---|
| `backend/app/services/recording/libraries/autocase_rf.py` | ~320 | `AutoCaseLibrary`：`Open Recorded Browser` + `Solve Slider`。落盘到 `${CURDIR}/libraries/AutoCaseLibrary.py` |
| `backend/app/services/recording/rf_steps.py` | ~150 | IR → 扁平动作列表；`locator_for_rf(target)` 定位渲染 |

### 修改

| 文件 | 改动 |
|---|---|
| `emit.py` | `_ensure_autocase_lib()`：有 slider 时落盘库文件；`_payload` 加 `mode='translate'`：只传扁平动作 + target_path + title；`_prompt` 加 translate 头部；`generate()` 走新通道，**头尾由程序拼** |
| `.opencode/prompts/recording-emitter.md` | RF 段从 12K 砍到 ~1.5K：只留「一行一个动作、locator 原样、不读项目、不找封装」 |
| `.opencode/prompts/recording-planner.md` | 去掉 `reusable` / `auth_setup`；只决定 `target_path` |
| `frontend/src/views/Recording.vue` | 结果卡标注「第 1 行 Open Recorded Browser 与 Teardown 由平台生成」 |

### 不动

`slider.py`（作为 Selenium 版迁移的源头，本身不改）、`FilesLibrary`、`guard`、PW 通道。

---

## 4. 四个关键实现

### 4.1 步骤展开（`rf_steps.py`）

顶层 9 步里第 2 步是 `call 登录`，子步骤在 `actions` 里。展开成：

```
1 goto    https://192.168.123.135/#/
2 fill    xpath=//*[@id='input-v-1']    admin@ailink.com
3 fill    xpath=//*[@id='input-v-4']    Ailink123456
4 click   xpath=//*[@data-testid='login-submit']
5 slider  css=div:nth-of-type(1) > canvas:nth-of-type(2)
6 click   xpath=//*[@id='v-list-group--id-tooltip-demo-4']
...
```

`call` 展开是程序行为，不交给模型。展开后每个动作都有完整定位（子步骤的 `snap.id` / `snap.testid` 都在）。

### 4.2 定位渲染（`locator_for_rf`）

子步骤的 `candidates` 里带 `rf_id` / `rf_testid` 候选，`spec.args` 已经是 `xpath=//*[@id='input-v-4']`。优先级：

1. `rf_id` / `rf_testid` / `rf_xpath`（录制时就验证过的）
2. `snap.id` → `xpath=//*[@id='...']`
3. `snap.testid` → `xpath=//*[@data-testid='...']`
4. `snap.css` → `css=...`
5. 都没有 → 抛「第 N 步缺少定位，请重新拾取」

### 4.3 `AutoCaseLibrary`

```python
def open_recorded_browser(self, url, download_dir=None):
    options = webdriver.ChromeOptions()
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--no-sandbox')
    options.add_experimental_option('prefs', {
        'download.default_directory': download_dir or str(Path.cwd() / 'downloads'),
        'download.prompt_for_download': False,
    })
    sl.create_webdriver('Chrome', chrome_options=options)
    sl.go_to(url)
    sl.maximize_browser_window()

def solve_slider(self, selector=None, attempts=2, timeout=40):
    # 迁移 slider.py 的算法，page.xxx → driver.xxx
```

`Solve Slider` 的算法完全照搬 `slider.py`：找容器 → 藏拼图块 → 元素截图 → 放大 2 倍 → 问视觉模型 → 24px 试探标定比例 → `ActionChains` 分 26 步拖动 → 轮询失败提示 → 失败重开（重新出题重新问）。

### 4.4 提示词（目标 2~3K）

```
请把下面这些录制动作逐条翻译成 Robot Framework（SeleniumLibrary）语句。
- 一行一个动作，顺序照抄，不要合并、不要省略、不要补等待（库里已有 implicit_wait）。
- locator 原样用，不要改写、不要换成别的定位方式。
- 每个动作上方写一行中文注释，说明它做什么。
- 只用 SeleniumLibrary 的标准关键字，不要调用本项目里的任何封装。
- 只写 *** Test Cases *** 里的步骤，不要写 Settings、不要写 Open Browser、不要写 Close Browser——那三样由平台生成。
```
+ 扁平动作列表 JSON

不给：关键字清单、样例、项目画像、任务书（`reusable` 已无意义）。

---

## 5. 风险

| 风险 | 处理 |
|---|---|
| `slider.py` 迁移到 Selenium 有行为差异（`page.screenshot(clip)` vs 元素截图） | 先用元素截图实现，跑一次真实滑块验证；失败再看裁剪范围 |
| 视觉模型调用耗时（一次 20s 量级） | 与 PW 一致，`attempts=2` 最多 3 次；`on_tick` 类似的进度输出 |
| 某些站点 `id` 不稳定 | 落盘后 dryrun + 真跑；真跑失败再降级到 css |
| 展开后动作数变多（9 → 12） | 这是必要的，录制里确实是 12 个动作 |
| 项目里没有 `input-v-1` 这类 id 的站点 | 定位渲染有 5 级回退，最后报「请重新拾取」 |

---

## 6. 验收

同一份 9 步存档，真跑：

1. 产物不含任何项目封装的调用（`grep -c '登录\|拖动滑块完成拼图\|打开浏览器并登录'` = 0）
2. 每个动作都能对回一个录制步骤（12 动作 ↔ 录制的 9 步 + 3 个展开子步骤）
3. `robot` 真跑 **PASS**
4. 提示词 ≤ 3,500 字符
5. 脚本里第一行是 `Open Recorded Browser <录制URL>`，Teardown 是 `Close Browser`

---

## 7. 顺序

1. `rf_steps.py`：展开 + 定位渲染（纯数据，可单测）
2. `AutoCaseLibrary.open_recorded_browser`（小，先把浏览器起起来）
3. 程序拼头尾 + 提示词 + agent 精简 → 跑通「除滑块外的 11 个动作」
4. `AutoCaseLibrary.solve_slider`（迁移 slider.py）
5. 端到端真跑
