# RF 生成改造：模型只出意图，渲染器落代码

> 依据：`测试用例层/_promptlab/实验报告.md`、`实验报告-第二轮.md`  
> 目标：把 RF 通道从「agent 自由写文件」换成「模型输出 op JSON → 渲染器写 .robot」，自由写保留为降级通道。



---

## 1. 实验给出的四个硬结论

| 结论                             | 证据                                               |
| ------------------------------ | ------------------------------------------------ |
| 样例和事实**不会**变成动作，只有祈使句生效        | E/F 组加了 2 篇同层级真实用例，仍无 Suite Setup                |
| 一条祈使句不够，必须成对写「Setup 覆盖的步骤不要重复」 | H 组补了「suite_setup 不能为空」→ Setup 有了，正文双重登录         |
| 生命周期交给渲染器最稳                    | G2（不调模型，渲染器补生命周期）86.5/90，是全部方案最高                 |
| 176 条关键字清单可以完全去掉               | G 组去掉清单改 op 描述，滑块正确调 `拖动滑块完成拼图`，得分 77.1 > F 68.7 |

**另有一条实验外的发现**：G2 里 `Suite Setup    打开浏览器并登录网管HA` 这个名字在真实项目里**不存在**（真实是 `打开浏览器并登录网管`）。那是我手写的清单。说明生命周期关键字名必须由程序校验，不能靠人/模型记忆。

---

## 2. 链路对比

### 现状（A 组形态，56,673 字符）

```
plan（planner 只读 → 任务书 JSON）
  ↓
generate（emitter 在工作区自由写文件）
   payload = 176 条 SeleniumLibrary 关键字 + 框架方言 3,928 字符
           + 每步全部候选定位 + project_memory + 样例
  ↓
dryrun → 失败让 emitter 改文件 → 再 dryrun
  ↓
guard 比对工作区，越界文件列表给用户
```

问题：模型要同时做四件事——选关键字、记参数、设计生命周期、排版。实测它只稳定做到前两件。

### 目标（G2 形态，24,958 字符）

```
plan（planner 只读 → 任务书 + lifecycle）
  ↓
spec 轮（spec-writer，read-only）：模型输出 op JSON
   payload = 5 条硬规则 + op schema + 9 步 + 8 条相关封装 + 样例 1 篇
  ↓
render_rf：op → 关键字 / 补依赖 / 补 Setup·Teardown / 删被覆盖步骤 / 删死代码
  ↓
写盘 → dryrun → 失败把「文件全文 + 报错」交回模型，要求返回修正后的完整 JSON → 重渲染 → dryrun
  ↓
解析失败 / op 全未知 → 降级 agent 直写（现有链路，原样保留）
```

---

## 3. 改动清单

### 新增

| 文件                                            | 行数   | 职责                                          |
| --------------------------------------------- | ---- | ------------------------------------------- |
| `backend/app/services/recording/render_rf.py` | ~380 | op → RF 文本；依赖注入；生命周期应用；死代码清理；`notes` 记录做了什么 |
| `backend/app/services/recording/keyindex.py`  | ~90  | 把 `.probe/rf_index/indexer.py` 正式化并包一层业务接口  |
| `backend/app/services/recording/emit_spec.py` | ~220 | 5 条硬规则 + op schema + spec 轮提示词 + 解析渲染入口     |
| `.opencode/prompts/recording-spec.md`         | ~60  | spec-writer 的 agent 提示词（1.4K 字符以内）          |

### 修改

| 文件                                               | 改动                                                                                                                                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/app/services/recording/emit.py`         | `_payload` 加 `mode='render'`：不传 `keywords`、不注入 `FRAMEWORK_RULES`/`LOCATOR_RULES`，改传 `reusable`（8 条封装签名）与 `lifecycle`；`generate()` 按框架+开关分流；`_check_with_repair` 支持 `spec_mode` |
| `backend/app/services/recording/emit_session.py` | 新增 `spec_turn()`，agent 用 `recording-spec`                                                                                                                                      |
| `backend/app/services/opencode_runner.py`        | `_emit_config` 支持 `recording-spec` 的权限（write/edit/bash 全 deny）                                                                                                                 |
| `opencode.json`                                  | 注册 `recording-spec` agent                                                                                                                                                      |
| `.opencode/prompts/recording-planner.md`         | framework 为 robot-selenium 时输出 `lifecycle` 字段（setup / teardown / covers_steps），并要求关键字名来自 `setup_candidates`                                                                    |
| `frontend/src/views/Recording.vue`               | 结果卡加「渲染器做了什么」清单 + 可折叠 JSON                                                                                                                                                     |

### 不动

- `guard` / 越界备份：保留（降级通道还要用）
- `_ensure_slider_utils` / `_ensure_captcha_utils` / `_ensure_files_utils`：保留
- PW 通道：完全不动

---

## 4. 关键设计

### 4.1 模型物理上写不了文件

新 agent `recording-spec` 的 `write` / `edit` / `bash` 全 deny，只留 `read` / `grep` / `glob`。

理由：G/H 两轮里模型偶尔会「顺手写文件」，即使提示词说了只输出 JSON。权限拦掉比事后比对便宜。副作用是 `write_scope` 概念在 spec 通道里失效——文件由后端写，本来也不会越界。

### 4.2 生命周期：planner 指定 + 索引器校验

`planner` 已经在读项目，让它多输出一段：

```json
"lifecycle": {
  "setup": "打开浏览器并登录网管",
  "teardown": "关闭浏览器",
  "covers_steps": [1, 2]
}
```

给了三样东西，缺一不可：

- **关键字名**：模型不该猜，从 `setup_candidates`（索引器给的 10 条登录/关闭类封装签名）里选；
- **覆盖步骤号**：这是「不要重复」从祈使句变成数据的唯一方式。渲染器直接删 `covers_steps` 里的步骤，不依赖模型自觉；
- **teardown**：`关闭浏览器` 这类名字项目里有 20 多个变体（`关闭浏览器2` / `-SIM卡` / `-开户模板`…），必须指名。

**校验**：渲染前用 `keyindex` 确认关键字存在且参数个数对得上。不存在就清空该字段，在 `notes` 里记一条「planner 给的 Setup 关键字 `xxx` 在项目里不存在，未填」，前端显示。宁可没有 Setup，也不要写一个跑不起来的。

**兜底**：planner 没给 lifecycle 时，`keyindex` 按「步骤里是否有 goto + 登录类动作」判断要不要 Setup，再从候选里选参数匹配的那条。选不出就留空并提示。

### 4.3 op 白名单 + raw 逃生口

```
goto / click / fill / download / upload
assert_text / assert_visible / assert_value
wait_until / sleep / select / press
call（调项目已有封装） / slider / captcha
raw（直接给 RF 行数组，模型自己负责）
```

`raw` 的存在是为了不让「op 表示不了」变成「整体降级」。它把风险限制在单步：这一步错了，其他步骤还是渲染器保证的。

渲染器遇到未知 op 时不静默丢弃——收集成 `problems`，若某步既无 op 也无 raw，整份 spec 判失败并降级。

### 4.4 修复轮改成「改 JSON」

```
轮 1   spec JSON → 渲染 → 写盘 → dryrun
失败   发：「渲染后的文件全文 + dryrun 输出 + 你上次的 JSON」→ 要求只返回修正后的完整 JSON
轮 2   重渲染 → 写盘 → dryrun（最多 2 轮，与现状 MAX_REPAIR_ROUNDS 一致）
```

为什么给文件全文：模型改 JSON 时看不到渲染结果，会改出一个「JSON 对但 RF 语法错」的版本。给它文件才能对齐缩进和关键字名。

### 4.5 降级策略（三层）

| 触发                                   | 行为                                        |
| ------------------------------------ | ----------------------------------------- |
| JSON 解析失败 / `test.steps` 为空          | 同一 session 换回 emitter agent，用原提示词直写（现有链路） |
| 渲染抛 `RenderError`（未知 op / 缺 locator） | 同上，并在结果里说明原因                              |
| 渲染成功但 dryrun 两轮不过                    | 不降级，报错保留文件（跟现状一致：能编译才算写对，改不出来就是真的有问题）     |

降级对用户可见：结果卡的 `mode` 字段显示 `render` 或 `agent`，agent 时显示「自由写作（结构化失败，已降级）」。

### 4.6 渲染器要做的事（`notes` 全量上报）

1. op → 关键字（`fill` 按 `secret` 选 `Input Text`/`Input Password`）
2. `download`/`upload` → 自动 `Library    ${CURDIR}/libraries/FilesLibrary.py`
3. 填 `Suite Setup` / `Suite Teardown`
4. 删 `covers_steps` 覆盖的正文步骤
5. 删因此失去引用的 `*** Keywords ***`（避免死代码）
6. 缩进与分列（2+ 空格）
7. 未知 op / 缺字段收集成 problems

每条记成一句中文，前端直接渲染。

---

## 5. 风险与对策

| 风险                       | 对策                                                   |
| ------------------------ | ---------------------------------------------------- |
| op 覆盖不了循环 / IF / 复杂断言    | `raw` op；仍不行降级直写                                     |
| 渲染器自己写错（比模型错得更隐蔽）        | 单测：9 步存档 + 3 个真实项目用例的快照对比；dryrun 兜底                  |
| planner 给的 lifecycle 不存在 | 索引器校验，不存在置空 + 提示                                     |
| 模型输出 JSON 时夹解释           | `extract_json` 取最大合法对象；解析不出就降级                       |
| 项目没有登录封装                 | lifecycle 留空，`notes` 提示「项目里没找到登录封装，请手动补 Suite Setup」 |
| 索引器扫大项目慢                 | 已有实测：8 文件 6.4ms、13 关键字 16.4ms；120s 缓存，按工作区 mtime 失效  |
| 前端改动影响现有流程               | 只加展示，不改现有字段；`renderNotes` 为空时整块不渲染                   |

---

## 6. 验收标准

用同一份 9 步存档（`d3029d90`），项目 `ums_auto - 副本/autotest`：

1. 产出 ≤ 30 行，dryrun 通过
2. `Suite Setup = 打开浏览器并登录网管`（真实存在的那个），`Suite Teardown = 关闭浏览器`
3. 正文无重复 `Go To` / 登录调用
4. 下载步骤自动带 FilesLibrary 依赖
5. 提示词 ≤ 26,000 字符（`len(prompt)` 实测）
6. 前端结果卡出现「渲染器做了什么」，条数与实际一致
7. 与 G2 产物逐行对比，差异只允许出现在注释和 Setup 关键字名（G2 那个名字是错的）

---

## 7. 顺序

1. `keyindex`（无依赖，可先跑通：扫真实项目 → 打印 10 条登录封装）
2. `render_rf` + 单测（用 G 组 JSON 做快照）
3. `emit_spec` 提示词（用实验里 G 组的文本改，不重写）
4. `emit.generate` 分流 + `recording-spec` agent + 配置
5. planner 的 `lifecycle` 字段
6. 前端 notes + 折叠 JSON
7. 端到端验收（第 6 节 7 条）
