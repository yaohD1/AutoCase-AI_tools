# Playwright Test Healer 接入方案

> **状态**：设计完成，**未实现**。当前优先级在「代码生成 agent」，本文档留作后期复盘与落地依据。
> **结论**：值得接，但定位必须改——healer 只负责「页面改版导致的回归失败」，不负责「验收新生成的脚本」。

---

## 1. 背景

生成 agent 写完脚本后，缺少「跑一次」这个环节。现有链路的判据是 `tsc --noEmit`，它只能证明「能编译」，不能证明「能跑」。

项目里已有一个 `playwright-test-healer` agent（Playwright 官方 `run-test-mcp-server` + 修复提示词），理论上可以直接拿来做「执行 + 修复」。

本文档记录核实过的能力边界、三处设计冲突、以及正确的接入方式。

---

## 2. 现状核实

### 2.1 配置事实

来源：`opencode.json` → `agent.playwright-test-healer`

| 项 | 值 |
|---|---|
| mode | `subagent` |
| prompt | `.opencode/prompts/playwright-test-healer.md`（2837 字符） |
| edit / write 权限 | `*: deny`，仅 `autocase/**` allow |
| read 权限 | `*: allow`（`*.env` / `*.env.*` 为 ask） |
| bash | 允许 |
| MCP | `playwright-test` → `npx playwright run-test-mcp-server` |

### 2.2 实际可用的工具

```
test_run / test_debug / test_list
browser_snapshot / browser_generate_locator
browser_console_messages / browser_network_requests / browser_evaluate
read / glob / grep / edit / write / bash
```

**核心价值在 `browser_generate_locator` + `browser_snapshot`**：它能实时看当前页面，重新生成一个能命中的定位。这正是「页面改版后修旧脚本」需要的能力。

### 2.3 三处设计冲突（提示词原文）

| # | 原文 | 后果 |
|---|---|---|
| 1 | `Code Remediation: … Fixing assertions and expected values` | 会**改断言期望值**去迎合实际结果，测试变绿但断言失效 |
| 2 | `If the error persists… mark this test as test.fixme() so that it is skipped` | 跑不通就**跳过**，绿灯但零覆盖 |
| 3 | `Do not ask user questions… do the most reasonable thing possible to pass the test` | KPI 是「通过」而不是「正确」 |

补充一条细节：提示词明确禁止 `networkidle`（`Never wait for networkidle or use other discouraged or deprecated apis`）。而现有生成产物里会出现 `waitForLoadState('networkidle')`，healer 一跑就会把它改掉。

**结论：不能无约束地把修复权交给它，必须加闸。**

---

## 3. 核心判断：healer 修回归，不验收新生成

同样是「测试失败」，两种情况的成因和信息量完全不同：

| 场景 | 成因 | 谁最合适 | 原因 |
|---|---|---|---|
| **首次失败**（刚生成，从未通过） | 生成写错了 | **生成 agent** | 它手里有 IR，知道第 5 步本来要点哪个元素 |
| **回归失败**（曾经通过，现在挂了） | 页面改版 / 数据变化 | **healer** | 它能看当前页面，重新生成定位 |

让 healer 去修一个「生成错的脚本」，等于让它猜我们的意图——它不知道第 5 步原本该点「确定」还是「取消」。这是**信息差**，不是能力差。

所以正确的架构是**按失败类型分流**，而不是「生成完就丢给 healer 修到绿」。

---

## 4. 分流架构

```
生成 → 静态门禁(dryrun / tsc) → 真跑一次
                                    │
                        ┌───────────┴────────────┐
                  首次失败                  回归失败
                （从未通过）              （曾通过，现在挂）
                        │                        │
              回生成 agent（带 IR        healer 修复
              + 报错 + 快照 + 步序号）    （只允许改定位）
```

### 4.1 判断「首次 / 回归」的依据

按优先级取：

1. `records` 表里这条目标文件是否有过 `validated=True` 且试跑通过的记录
2. `git log --oneline -- <target>`：有历史提交且提交后曾经绿过
3. 都没有 → 视为首次失败，走生成 agent

### 4.2 失败原因分类（用于 finer 路由）

| 报错特征 | 归类 | 路由 |
|---|---|---|
| `locator resolved to 0 elements` / `strict mode violation` | 定位类 | healer（安全） |
| `expect(received).toBe(expected)` | 断言类 | **人工**，不进 healer |
| `Timeout ... waiting for` | 时序或定位 | 先给 healer，但禁止加 `waitForTimeout` |
| 步骤序号断裂 / 关键字不存在 | 生成类 | 生成 agent |

---

## 5. 让 healer 知道「原本要做什么」

这是接入的前置条件。不给原意，它只能盲修。

### 5.1 四种传递方式对比

| 方式 | 信息量 | 持久性 | 实现成本 | 评价 |
|---|---|---|---|---|
| **代码注释** | 中 | 跟文件走，长期有效 | 低（生成时拼字符串） | **推荐** |
| **调用时注入任务描述** | 高（可含候选列表） | 仅本次会话 | 低 | **推荐，与注释互补** |
| sidecar 文件 `xxx.spec.ts.ir.json` | 最高（完整 IR） | 跟文件走 | 中 | 复杂流程（iframe/多标签）时用 |
| Playwright trace | 运行时事实 | 单次 | 中 | 只作辅助诊断，不含意图 |

### 5.2 注释格式规范（生成端产出）

每步一行紧凑注释 + 可选细节行：

```ts
// ── step 3/8 · click ─────────────────────────────
// 意图: 点击「登录」按钮
// 录制时: 页面存在 2 个同名按钮，取第 2 个
await page.getByRole('button', { name: '登录' }).nth(1).click();
```

复杂步骤可加候选行：

```ts
// 候选: role=button[name=登录] | text=登录 | css=div.login>button:nth-child(2)
```

**数据来源**：`target.matches`（框选功能已采集）+ `target.candidates` + `target.chosen`，生成时直接拼接，不需要额外采集。

### 5.3 附带的收益：序号可用于事后对账

有 `step N/M` 序号后，healer 修完可以做**连续性检查**：

- 序号断裂 → 它删了步骤 → 拒绝采纳
- 序号跳号或重复 → 它合并/复制了步骤 → 拒绝采纳

这给「禁改结构」提供了一个可执行的抓手，比人工看 diff 可靠。

---

## 6. 三道闸（必须实现，约 80 行）

healer 产出后用文本 diff 校验，任一不通过则不采纳、报给人。

### 闸 1：禁止修改断言

```python
ASSERT_PAT = re.compile(
    r'\bexpect\s*\(|toHaveURL|toContainText|toHaveText|toBeVisible|toBeHidden|toHaveCount'
)
```

规则：两侧都含断言的行，**内容必须完全一致**；新增断言行允许（更严格），删除或修改一律拒绝。

### 闸 2：禁止 skip / fixme

```python
FORBID_PAT = re.compile(r'test\.fixme\s*\(|test\.skip\s*\(|test\.describe\.skip')
```

出现即判失败，直接回滚。

### 闸 3：只允许改 locator 行

白名单判定：变更行必须同时满足
- 含 `page.` 或 `locator(` 或 `getBy`
- 不含断言正则
- 不含 `test(` / `test.describe(`

不满足 → 拒绝。

### 6.1 建议落点

新增 `backend/app/services/recording/heal_guard.py`：

```python
def check(before: str, after: str) -> tuple[bool, list[str]]:
    """返回 (是否可采纳, 违规说明列表)。纯函数，可单测。"""
```

同时要 `waitForTimeout` 检测：新增的 `waitForTimeout` 行计入 warning（不拒绝，但要报告）。

---

## 7. 落地步骤

### 阶段 0：最小验证（不改产品代码）

目标：确认 healer 在这个工作区真的能跑、真的只改定位。

1. 在 `D:\autoproject\midscene-ui-test` 复制一份能跑的 spec
2. 人为改坏一个 locator（例如把 `getByRole('button',{name:'登录'})` 改成 `getByRole('button',{name:'登入'})`）
3. 执行：
   ```bash
   opencode run --agent playwright-test-healer \
     --dir D:/autoproject/midscene-ui-test \
     "修复 tests/xxx.spec.ts 的失败。原始操作序列：<第 5 节格式>"
   ```
4. 检查 `git diff`：
   - 是否只改了 locator 行
   - 有没有动断言
   - 有没有加 `waitForTimeout`
   - 有没有 `test.fixme`

**验收**：diff 只有 locator 行变化，且测试真跑通过。

### 阶段 1：生成端注入原意（低风险，独立收益）

即使不接 healer，注释对人工维护也是纯收益。

改动点：
- `.opencode/prompts/recording-emitter.md`：要求每个操作前必须有 `// ── step N/M · <type> ──` 注释
- `backend/app/services/recording/emit.py`：`_payload()` 里补 `matches` 摘要（已有数据），提示词说明注释内容来源

**验收**：生成的 spec 里每一步都有序号注释，且 `matches` 摘要与实际页面一致。

### 阶段 2：healer 调用封装

改动点：
1. **权限**：`opencode.json` 里 `playwright-test-healer` 的 edit/write 白名单从 `autocase/**` 改为可配置（与 `_emit_config` 相同的写法：相对路径 + `**/` + 绝对路径三形式）
2. **runner**：`opencode_runner.py` 新增 `heal(workspace, model, prompt)`，走 `opencode run --agent playwright-test-healer`（或复用 `serve`）
3. **MCP 前置检查**：目标工作区必须能跑 `npx playwright --version`，否则直接报「该工作区未安装 Playwright」

**待验证**：`mode: subagent` 能否被 `--agent` 直接指定。若不行，两个选项：
- 把 mode 改成 `all`
- 通过一个 primary agent 用 task 工具派发

### 阶段 3：分流 + 三道闸

改动点：
1. `emit.py` 里跑完测试后，按 `heal_guard.classify(output)` 归类失败
2. 定位类 → 调 healer；其余 → 回生成 agent 或报人工
3. healer 产出经过 `heal_guard.check()`，不通过则不写盘
4. 采纳时把 diff 存进 `records`，UI 可查看「healer 改了什么」

**验收**：
- 人为改坏 locator → healer 修好 → 只有 locator 行变化
- 人为改坏断言期望值 → healer **不采纳**，报「断言被修改，请人工确认」
- 人为让它跑不通 → 不出现 `test.fixme`

### 阶段 4：接进 UI（可选）

- 生成记录页加「修复失败用例」按钮
- 展示 healer 的 diff + 三道闸结果
- 只对「曾通过过」的文件开放

---

## 8. 风险与未决项

| # | 项 | 说明 | 处理 |
|---|---|---|---|
| 1 | `subagent` 能否直接 `--agent` 调用 | 未实测 | 阶段 2 验证，不行就改 `mode: all` |
| 2 | 只支持 Playwright | `run-test-mcp-server` 不认 RF | RF 项目走 `--dryrun`，不接 healer |
| 3 | 目标工作区要有 playwright 依赖 | `npx playwright` 需本地安装 | 调用前检查，缺则报错 |
| 4 | 内网环境 | 例如 `https://192.168.123.13` | 环境不通时只能 dryrun，heal 不可用 |
| 5 | 有副作用的用例 | 增删设备、复位网元 | 需要沙箱环境，或只跑只读子集 |
| 6 | healer 可能重写整个文件 | 提示词没限制改动范围 | 三道闸兜底；必要时换成「只返回变更片段」的窄提示词 |
| 7 | 滑块/验证码 | `拖动完成拼图` 本身不稳定 | 这类失败不交给 healer，避免它去调等待时间 |
| 8 | healer 会引入 `waitForTimeout` | 它的修复习惯 | 计入 warning，超过 3 处则人工 review |

---

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `opencode.json` | healer 的 agent 定义、权限、MCP |
| `.opencode/prompts/playwright-test-healer.md` | healer 提示词（三处冲突在此） |
| `backend/app/services/recording/emit.py` | 生成主流程，需接入分流 |
| `backend/app/services/recording/records.py` | 记录「曾经通过」的状态 |
| `backend/app/services/opencode_runner.py` | 新增 `heal()` |
| `backend/app/services/recording/heal_guard.py` | **待新建**，三道闸 |
| `docs/` | 本文档 |

### 验证命令速查

```bash
# 看 healer 解析后的配置
opencode debug agent playwright-test-healer

# 直接指定 agent 跑（阶段 2 验证用）
opencode run --agent playwright-test-healer --dir <workspace> "<prompt>"

# 确认目标工作区可用
cd <workspace> && npx playwright --version && npx playwright test --list
```

---

## 10. 一句话总结

**healer 不是「生成后的兜底修复器」，是「脚本进仓库后的定位维护器」。**

接它的正确姿势是：生成端留下原意（注释 + 序号），失败按「首次/回归」分流，healer 只在回归场景、只改定位、过三道闸、diff 可见。
