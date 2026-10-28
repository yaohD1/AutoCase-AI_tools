# RF 定位优化计划

> **状态**：P0（行锚定）、P1（序号内联）、P2（id 分级）已实现并测试通过；
> 另补了两个跨框架的快照层缺陷（class 名字候选、结构路径唯一性自检，见 §8A）；P3–P4 待排期。
> **一句话结论**：RF 侧的定位质量问题，根子不在「xpath 写得不够聪明」，而在两处工程缺陷——
> **多匹配时把序号改写的活儿交给了模型**，以及**判断规则把强定位当垃圾扔掉**。

---

## 1. 边界：RF 和 Playwright 不是同一道题

生成产物是 `.robot`，执行引擎是 Robot Framework + SeleniumLibrary。这条链路上有三个
Playwright 有、RF 没有的东西，它们决定了优化方向完全不同：

| 能力 | Playwright | SeleniumLibrary |
|---|---|---|
| 定位组合 | `getByRole(...).filter(...).getByRole(...)` 任意链式 | **一个字符串**，不能链 |
| 多匹配 | strict mode violation，**大声失败** | `find_element` 取第一个，**静默成功** |
| 自动等待 | 自带 auto-wait | 每条交互前手写 `Wait Until Element Is Visible` |

第二行是本文档所有判断的前提：**同样的错误定位，在 Playwright 里会红，在 RF 里会绿。**
所以 RF 的优化目标不是「跑得快」，是「错的时候必须错得响」。

---

## 2. 现状盘点

### 2.1 候选清单

来源 `backend/app/services/recording/locators.py::build_specs`。
每条候选都在录制那一刻**回到真实页面上数一遍 count**，唯一性来自实测，不靠猜。

| kind | 生成条件 | 产物 | 强度 |
|---|---|---|---|
| `rf_testid` | `data-testid` 存在 | `xpath=//*[@data-testid='x']` | 最强 |
| `rf_id` | `id` 存在且不像动态 id | `xpath=//*[@id='x']` | 强 |
| `rf_name` | `name` 属性存在 | `xpath=//*[@name='x']` | 强 |
| `rf_aria` | `aria-label` 存在 | `xpath=//*[@aria-label='x']` | 强 |
| `rf_label` | label 文本存在 | `xpath=//*[@id=//label[...]/@for]`（for 关联）<br>`xpath=//label[...]//*[self::input or ...]`（包裹式） | 强 |
| `rf_id_loose` | `id` 带长数字串 / 控件名+序号 | `xpath=//*[@id='user-100']` | 中（可能随渲染变） |
| `rf_xpath` | placeholder / 显式 role+文本 / TEXT_TAGS 文本 | `xpath=//*[@placeholder='x']`<br>`xpath=//*[@role='button'][normalize-space(.)='发送']`<br>`xpath=//button[normalize-space(.)='发送']` | 中 |
| `rf_row_scoped` | 行内元素自身不唯一，且试出了行键 | `xpath=//tr[contains(., 'WO-2')]//button[...]` | 中（列表场景最稳） |
| `rf_class_part` | class 里剥得出名字（§8A.1） | `xpath=//*[contains(@class,'_itemMenu_')]` | 中（不依赖位置，弱于文本） |
| `rf_css` | 一定有（快照里必带 css 路径） | `css=div > div:nth-of-type(2) > ...` | 弱，兜底（已自证唯一，§8A.2） |

排序 `RF_KINDS`：属性型 → 文本型 → 结构型。`pick_for` 取第一条 `count == 1` 且**确实命中目标**
（`relation` 校验，见 §3.2）的候选。`rf_id_loose` 的位置理由见 §6.1。

### 2.2 已知的等价性保证

- `rf_testid / rf_id / rf_name / rf_aria / rf_label / rf_xpath` 都是从 Playwright 快照里的
  同一个语义信号投影出来的，**两套框架的命中集合对得上**（`test_recording_locator_parity` 守着这条）。
- 计数用的是 Playwright 的 `locator('xpath=...')`，Selenium 用的是同一个 XPath 引擎语义，
  都是**文档序**——这一点后面 P1 会用到。

---

## 3. 已确认的四类缺陷

下面四条都不是推测，是照着当前代码跑出来的结果。

### 3.1 多匹配时，序号靠模型手改字符串（会静默点错，P1 已修）

`LOCATOR_RULES` 以前这么要求写手：

> `robot-selenium` 写 `xpath=(<target.locator 去掉 xpath= 前缀后的表达式>)[n+1]`，不要改写表达式本身

把一次本该由代码完成的字符串变换，交给了一个概率模型。三种翻车方式：

1. 忘了改 → `//button[normalize-space(.)='删除']` 匹配 3 个，Selenium 取**第一个**，不报错。
   用户以为点了第 2 行，实际点了第 1 行——**这是最坏的一种**，因为它绿。
2. **候选是 `css=` 时必然写错**。规则让去掉 `xpath=` 前缀，但 `rf_css` 的 locator 是
   `css=div > ...`，没有那个前缀；拼出来是 `xpath=(css=div > ...)[2]`，Selenium 直接语法错误。
   实测这个分支真的能走到：

   ```
   locator: css=div > table > tbody > tr:nth-child(2) > td:nth-child(3) > button
   match_count: 2
   nth: 1        ← 写手按规则会产出 xpath=(css=...)[2]
   ```
3. 表达式里带 `[` 或 `|` 时括号加错位置。

### 3.2 行内重复元素没有行锚定（P0 已修）

三个「删除」按钮，第 2 个才是目标。以前只能靠 `css` 的 `nth-child` 或序号硬扛，
列表一增一删就整体错位，而且**错了不报错**（同上）。P0 的解法见 §4。

### 3.3 强定位被规则误杀（P2 已修）

```python
_DYNAMIC_ID = re.compile(r'^el-id-|\d{3,}')
if element_id and not _DYNAMIC_ID.search(element_id):
```

`search` 是**任意位置**匹配 3 个以上连续数字。于是：

| id | 修前 | 应该 | 修后 |
|---|---|---|---|
| `el-id-749-6` | 丢弃 ✅ | 丢弃（组件库每次渲染重排） | 丢弃 |
| `user-100` | **丢弃 ❌** | 保留（业务 id，稳定） | `rf_id_loose` |
| `order-20261028-001` | **丢弃 ❌** | 保留 | `rf_id_loose` |
| `:r1:` | **保留 ❌** | 丢弃（React useId） | 丢弃 |
| `input-0` | **保留 ❌** | 降级 | `rf_id_loose` |

`user-100` 这种 id 比 `//*[@placeholder='用户名']` 还稳，修前被直接扔掉；
更糟的是它连带影响 RF 组整体：丢掉最强信号后，候选池里只剩文本和 css。

### 3.4 长文本 / 动态文本没有定位可用

```python
if text and len(text) <= 40:   # 超过 40 字直接不生成文本候选
```

- **>40 字**：`span`/`p`/`h3` 这类长文本元素**一条文本候选都不生成**，直接掉到 `css`。
  实测 45 字的 span → `build_specs` 返回空列表。
- **文本会变**：`normalize-space(.)='共 3 条'` 是整段精确匹配。数据一变（`共 4 条`）就永久失配，
  而 `contains` 版本能活。
- **`div` 不在 `TEXT_TAGS` 里**：`<div>发送</div>` 这种图标按钮拿不到文本候选。

---

## 4. P0：行锚定（已实现）

### 4.1 产出形态

```robot
# 以前：结构路径，列表一增一删就整体错位
Click Element    css=div > table > tbody > tr:nth-child(2) > td:nth-child(3) > button

# 现在：先用业务字段锁定行，再在行内找
Click Element    xpath=//tr[contains(normalize-space(.), 'WO-20261028-001')]//button[normalize-space(.)='删除']
```

SeleniumLibrary 只接一个定位字符串，所以行作用域必须和内层定位写进**同一条 xpath**：
`A//B` 本身就是「A 的后代里的 B」，不需要也不能加前导点。

### 4.2 怎么保证不锚错行

行键不猜，挨个试，三条全过才用（`session._row_anchors` + `_anchor_ok`）：

1. 这个文本能让**这一行**在全页唯一命中（`count == 1`）
2. 这一行**真的包住**目标元素（`relation == 'self'`）
3. 用真实查询复验一次

试不出来就不加——宁可退回「取第 N 个」，也不锚错行。

### 4.3 几个实现细节

- **`recorder.js` 采集 `row_scope.tag`**：XPath 没有 `getByRole('row')` 的等价物，
  必须知道这一行是 `tr` / `li` / `div`。`tr`/`li` 直接用标签名，
  组件库的 `div[role=row]` 用 `//div[@role='row']` 收窄（否则 `//div[...]` 会把祖先也算进来）。
- **更早录的快照没有 `tag`**：按 `role` 猜 `tr` / `li`，猜错只会数不到，不会锚错。
- **两个框架各判各的**：RF 能从 testid 投影出定位、Playwright 未必有同名属性，
  一边唯一不代表另一边唯一，所以分别判断、各挂各的。
- **`rf_row_scoped` 排在 `rf_xpath` 之后**：它本身就是用文本 xpath 组出来的，
  只在「文本在页面上不唯一」时才比裸文本强，绝不能压过一条本来就唯一的语义定位。
- **`css=` 候选不参与行锚定**：css 选择器没法接在 xpath 后面，硬拼出来的东西语法上不成立。
- **停止录制复核时**：目标元素上的临时标记已经没了，行锚点重建不出来，
  只能把录制时那条搬过来重数，`count == 1` 才保留（否则丢弃、退回序号）。

### 4.4 覆盖的测试

`backend/test_recording_row_scope.py`（19 条）：原生 `<tr>`、`div[role=row]`、无 `tag` 的旧快照、
文本在两行都出现的负例、目标本身已唯一的负例、xpath 拼接的纯规则用例、引号转义。

---

## 5. P1：把序号写进定位本身（已实现）

### 5.1 做法

`locators.inline_nth()` 是唯一的拼装点，`emit._locator_info` 在 RF 框架下
对「多匹配 + 有 index + `xpath=` 前缀」的候选调用它：

```python
def inline_nth(locator, index):
    if index is None or index < 0:
        return locator, False
    expr = (locator or '').strip()
    if not expr.startswith('xpath='):
        return locator, False
    body = expr[6:].strip()
    if not body:
        return locator, False
    return 'xpath=(%s)[%d]' % (body, index + 1), True
```

payload 增加 `nth_applied: true`，`LOCATOR_RULES` 相应改成四条分支：

> `nth_applied` 为 true：序号已经写进 locator 了，原样使用；**不要再写 `.nth()`，
> 也不要再包一层 `(...)[n]`**——那会变成「第 3 个里的第 3 个」，一个都匹配不到。
> `nth` 只用来写注释。

`nth` 字段保留（注释、前端「取第几个」、`degraded` 统计都还在用），
只是不再要求写手参与拼接。写手从 `candidates` 里挑备选时，候选的 locator 也已内联
（`_candidates_for_prompt` 同样走 `_fit_nth`），不存在「主定位安全、备选仍是裸串」的缺口。

### 5.2 css 候选的降级必须显式

`css=` 无法索引。当唯一可用的候选是 `rf_css` 且 `count > 1` 时 `inline_nth` 返回
`(原样, False)`，payload 里就不出现 `nth_applied`；提示词要求写一行 TODO，
说明「本应取第 n+1 个，需改成 xpath 或让页面提供稳定属性」。
现在的行为不再是生成一句语法错误的 `xpath=(css=...)[2]`。

### 5.3 为什么这个序号是可信的

`index` 是录制时用 Playwright 在页面上 `evaluate_all` 算出来的**匹配序**。
对 `xpath=` 定位，Playwright 和 Selenium 都是**文档序**，所以两边对得上。
（`getByRole` 的顺序是 Playwright 内部的 a11y 序，不能假定等于文档序——
但 RF 不用 `getByRole`，所以不冲突。）

真浏览器验证（`test_recording_rf_nth.InlineXpathOnRealPageTest`）：
3 行表格里 `(//button[...])[1..3]` 分别落在 r1/r2/r3；裸定位 count=3 且
`first` 落在 r1 —— 后者就是「绿着点错第一行」的现场证据。

### 5.4 收益

一次性消掉 §3.1 那一整类错误，包括「静默点错第一行」这种最危险的形态。
改动集中在 `_locator_info` + `_candidates_for_prompt` + 一段提示词，不动录制侧。

---

## 6. P2：分清「业务键」和「框架自动生成」（已实现）

旧判据 `_DYNAMIC_ID = r'^el-id-|\d{3,}'`（`search`）等于「数字多就是动态的」，
实测下来**两个方向同时判错**：

| id | 旧结果 | 应该 | 为什么 |
|---|---|---|---|
| `order-20261028-001` | 丢弃 | 保留 | 业务单号，页面上最稳的定位 |
| `user-100` | 丢弃 | 保留 | 人写的 id |
| `:r1:` | **保留** | 丢弃 | React 18 `useId`，每次挂载重新算 |
| `react-select-3-input` | **保留** | 丢弃 | React Select 实例序号 |
| `rc-tabs-0-tab-2` | **保留** | 丢弃 | rc-* 组件库（antd 底层） |
| `input-0` / `tab-2` | **保留** | 降级 | 数字不足 3 位，逃过了那条正则 |

判据换成「有没有框架生成的特征」：

```python
_DYNAMIC_ID = re.compile(r'^(?:'
    r':r[0-9a-z]+:'      # React 18 useId
    r'|el-id-'           # Element Plus
    r'|react-select-|react-aria|downshift-|headlessui-|radix-'
    r'|rc-[a-z]+-'       # rc-*（antd 底层）
    r'|mui-|ext-gen|gwt-uid|j_id|ember[0-9]'
    r')', re.I)

_ID_WITH_NUMBER = re.compile(r'\d{3,}')          # 分不清是业务键还是序号
_SEQUENTIAL_ID = re.compile(r'^(?:input|tab|item|field|row|col|btn|...)[-_]?\d+$')
```

三级处理：

1. **命中框架前缀 → 丢弃**。这类 id 重新挂载就变，回放必然找不到，没有任何补救空间。
2. **含长数字串 / 控件名+序号 → `rf_id_loose`**。分不清是业务键还是自增序号，
   所以既不丢也不当首选。
3. **其余 → `rf_id`**，仍是仅次于 testid 的强定位。

### 6.1 `rf_id_loose` 排在哪

```
rf_testid → rf_id → rf_name → rf_aria → rf_label → rf_id_loose → rf_xpath → rf_row_scoped → rf_css
```

原计划是排在 `rf_xpath` **之后**，实际实现提前到了 `rf_label` 之后。理由是原计划那句
「`row-101` 可能命中另一行」不成立：id 在 HTML 里唯一，数据变了它是**找不到**，不是
命中别人。既然不会静默点错，它就该排在文本匹配（会因文案微调而失配、也可能命中提示语里的同样文字）
之前——id 是元素身份，文本只是元素的一部分。

真正会「命中另一行」的是 `row-0`、`item-3` 这种渲染序号，它们已经被 `_SEQUENTIAL_ID` 归到
loose，并且让位给 name / aria / label。

### 6.2 降级为什么不等于丢弃

丢弃的代价是退到 `rf_css` 结构路径——那才会静默点错。保留的代价最坏是回放时
「找不到元素」，是明确失败、好查。两害相权，保留。

界面会在选中 loose id 时提示「这个 id 里带数字，可能是业务单号，也可能是渲染序号，回放前确认一次」。

---

## 7. P3：放宽文本候选

三条改动，共同的依据是——**唯一性是在真实页面上数出来的，多给候选不会降低质量，
只会多一次 `count()` 往返（实测每次 2–5ms）**。所以生成条件可以放宽，让 `pick_for` 去筛。

| 改动 | 解决 | 产物 |
|---|---|---|
| 长文本用前缀 `contains` | >40 字拿不到文本候选 | `//span[contains(normalize-space(.), '订单编号：WO-2026...')]` |
| 短文本补一条 `contains` 变体（低优先级） | 「共 3 条」这类动态文本 | `//button[contains(normalize-space(.), '提交')]` |
| `TEXT_TAGS` 允许 `div` | `<div>发送</div>` 类图标按钮 | `//div[normalize-space(.)='发送']` |

风险控制：`contains` 版本一律排在精确匹配之后，且仍然要过 `count == 1` 才算数。
代价是每步多 1–3 次页面查询，录制耗时增加约 5–15ms/步。

---

## 8. P4：收尾的一致性

1. **`desc` 与 locator 不统一**：RF 候选的 `desc` 是 `rf xpath://button[...]`，
   实际写进脚本的是 `xpath=//button[...]`。前端展示的是前者，用户复制出来是错的。
   建议 `desc` 直接放最终字符串（`xpath=` 前缀可省），前缀只做 UI 标签。
2. **后端 `status` 只按 Playwright 口径算**：`rank()` 选的是 PW 主定位，
   RF 侧降级了但 PW 侧没降时，落库的 status 仍是 `unique`。
   界面按框架重算（`effectiveStatus`）所以看起来是对的，但接口返回值会误导调用方。
   建议 `status` 拆成 `status_by_framework`，或至少在文档里写明口径。
3. **`stale_check` 只比 PW primary**：停止录制时 RF 那条变了不会被标 `stale`。同上，一起改。

---

## 8A. 补掉两个洞（两套框架同时生效，已完成）

这两条不属于 RF 独有，是 Playwright 和 RF 共用的**快照层**缺陷：候选再怎么排，
喂进去的原料不够，最后仍然只能退到位置路径。

### 8A.1 洞一：class 里明明有名字，却从来没用

真实页面（实测快照 `.tmp-ai-locate/hard-elements.json`）里，大量控件既没有 role、
没有 aria，也没有可当名称的文本，唯一的身份线索就是 class：

```
_sidebarListHeaderAdd_ce5l0_53      Vite CSS Modules：[name]_[local]_[hash]_[line]
_itemMenu_fxrlp_33                  哈希里连数字都没有
user-card__name                     BEM
sidebarListHeaderAdd                styled-components / 手写驼峰
```

构建哈希每次打包都变，**名字那半截不变**。所以 `[class*="_sidebarListHeaderAdd_"]`
比 `div:nth-of-type(3) > div > button` 稳得多——后者靠位置，改个布局就指到别人。

做法（`locators.class_stem`）：从 class 里剥掉哈希，留下名字。

| 输入 | 产出 | 定位 |
|---|---|---|
| `_sidebarListHeaderAdd_ce5l0_53` | `_sidebarListHeaderAdd_` | `[class*="_sidebarListHeaderAdd_"]` |
| `user-card__name` | 整词（没有哈希可剥） | `[class~="user-card__name"]` |
| `user-card--active` | `user-card`（BEM 修饰符是状态，不是身份） | `[class~="user-card"]` |
| `p-3` `w-[15.368px]` `text-white` | 丢弃 | Tailwind 工具类，改样式就变 |
| `ant-badge` `t-icon` `el-input__inner` | 丢弃 | 组件库前缀，全站同名 |
| `css-1l86wfl` `_ngcontent-c12` | 丢弃 | 构建期类名，零信息量 |

两个容易写错的点，都用测试钉住了：

- **前缀必须带边界**。只写 `t` 会把 `tab` / `title` / `tooltip` 一起吃掉，只写 `el`
  会把 `element-xxx` 吃掉——`_CLASS_VENDOR` 的正则里分隔符是必须项。
- **哈希不一定含数字**。`fxrlp` 一个数字都没有，靠「短短的小写串」认出来；而
  `active` / `primary` 这种词短但它是状态名，剥掉就把名字截断了，要留住。

RF 侧对应 `rf_class_part`：`//*[contains(@class, '_itemMenu_')]`；整词匹配用
`contains(concat(' ', normalize-space(@class), ' '), ' x ')`（XPath 没有 `~=`）。
排位在两套框架里都放在「文本之后、css 之前」：它比文本弱（文本是用户看到的东西），
但比结构路径强（不依赖元素在第几个位置）。

### 8A.2 洞二：结构路径从不自证唯一

旧 `cssPath` 只往上爬 6 层，而且**从不验证**。它只在「同层有同名兄弟」时才补
`:nth-of-type(n)`，于是 `div > div > div > div > div > button` 这种路径——每层都没有
重名兄弟——会从文档任意位置开始匹配。录出来当「结构定位」写进脚本，能跑通纯属运气。

现在改成「最短且唯一」：每加一层就在页面上 `querySelectorAll` 数一次，命中 1 个立刻停手；
6 层不够就继续往上爬（上限 12 层），爬到根还不唯一才返回最长的那条，交给 count 机制
按 `.nth(i)` 降级。元素在 shadow root 里时在同一个根里数（`document` 看不见 shadow 内部）。

真实页面实测（`ai.test.yygu.cn` 主壳，400 个可交互元素）：

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 路径在页面上不唯一的 | 9 / 400 | **0 / 400** |
| 平均路径长度 | 6.0 段 | **2.9 段** |
| 需要爬到 7 层以上才唯一的 | — | 9 条（正是旧路径出错的那 9 条） |
| 单次快照耗时 | 0.007 ms | 0.972 ms（每次点击 2 次快照 ≈ +2 ms） |

路径变短不是副作用而是目的：`div > button` 比 `html > body > div > div:nth-of-type(2) > div > button`
更能扛住布局调整，前提是它确实唯一——现在这一点是**验过的**，不是假设。

顺带一点：`[class*=]` 候选在这批元素里只有 6/400 能直接当主定位（页面被 796 个同名
`_itemMenu_` 占满），它的主要价值在于「唯一的那个组件」和「重复元素的行内锚点」，
不是把 css 全部替掉。

### 8A.3 验收

- `test_recording_class_part`（16 条）：真实 class 串的哈希剥离、工具类/组件库前缀拒绝、
  前缀边界、最长名字优先、两套框架候选同时产出、优先级位置、真浏览器 count/relation。
- `test_recording_css_path`（6 条）：**直接注入真 recorder.js**，点一下目标再读它 emit 的
  快照，断言路径唯一、指向目标、浅页面不过度加长、表格行内唯一、`class_raw` 被采到；
  另外把改造前的算法照抄一遍，证明它给出的路径真的命中 2 个（否则这个测试没有意义）。

---

## 9. 验收方式

1. **单测**：`test_recording_row_scope`（P0，19 条）、`test_recording_rf_nth`（P1，19 条，
   含真浏览器断言：`(//button)[1..3]` 分别落在三行；裸定位 count=3 且 first 落在第一行）、
   `test_recording_rf_id`（P2，9 条：17 种框架 id 全丢、业务键保留、放松 id 让位给 name/aria/label）。
   后续 P3 新增自己的文件，不改这三份。
2. **真机回归**：三个页面各录一遍再生成——原生 `<table>`、`div[role=row]+div[role=cell]`
   组件库表格、`<li>` 平铺列表。判据是「生成的 xpath 粘回页面能唯一命中目标」，
   用 `locator.count()` 数，不靠肉眼。
3. **与人工写法比对**：把生成的 xpath 和手写的放一起看，差距应当只在「行键选得对不对」上。

---

## 10. 不做什么

- **不接 AI 定位兜底**。RF 侧接 midscene 需要 Node 进程 + CDP 桥接，
  而且 AI 给的是坐标、RF 要的是 xpath，中间那层转换未必比原定位稳。
  真要做，形态应该是「录制时用 AI 帮忙选行键，生成时写纯 xpath」，而不是回放期调用。
- **不做 xpath 候选的 union 降级链**。`//a | //b[not(//a)]` 语法上能表达优先级，
  但可读性极差，维护成本高于收益。RF 的兜底应该是「重新拾取」，不是「一条 xpath 里塞五个候选」。
- **不动 SeleniumLibrary 的等待写法**。`FRAMEWORK_RULES` 里的显式等待是 RF 的必需品，
  和定位质量无关。

---

## 11. 风险

| 风险 | 影响 | 处理 |
|---|---|---|
| 放宽候选增加录制延迟 | 每步 +5~15ms | 只在语义候选全不唯一时才试行锚点；其余放宽项排在精确匹配之后 |
| `rf_id_loose` 命中别的行 | 静默错 | 低优先级，只在没有更强候选时使用 |
| 序号内联后模型仍手拼一次 | 语法错误 | `nth_applied` 在提示词里显式说明，且测试断言提示词内容 |
| 行键取自会变的字段（状态、时间） | 换数据后失配 | 现在是「数到唯一才算」，失配是**大声失败**，可接受；更好的做法见 P3 的 `contains` |
