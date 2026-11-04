# 能力验证：同一 opencode session 跨 serve 续用

日期：2026-11-04　opencode：**1.18.31**（`.env` 里 `OPENCODE_BIN` 钉的那一个）
脚本：`shots/_check_emit_session.py`（验证脚本，保留下来供升级 opencode 后重跑）
model：`cctq-codex/gpt-6-luna`

## 现状：这项能力暂时没在用

写权限改成「整个工作区可写（只排除 `.git` / `.opencode` / `node_modules`）」之后，
规划轮和写入轮跑在**同一个 serve** 上，不需要中途换 config 重启进程了
（见 `emit_session.py` 的模块说明）。

这篇留着有两个用途：一是「新 serve 能续上旧 session」是 opencode 的真实能力，
以后要做「serve 崩了自动续上」时可以直接照着实现；二是升级 opencode 之后
应该重跑一遍 `shots/_check_emit_session.py`，确认这条能力没变——它现在是备选方案，
不是主路径。

## 为什么要验

把「读项目」和「写文件」合到同一个 session，前提是一条 session 能跨两次 HTTP 请求存活。
opencode 的配置（含 agent 权限）是 **serve 启动时**加载的，而写入路径要到任务书轮结束才确定，
所以写入轮必须换一份收窄配置 → 必须换 serve。**换 serve 后还能不能续上同一个 session**，
决定了整个方案是否成立。

## 结论：六项全过

| # | 检查 | 结果 |
|---|---|---|
| 1 | 杀掉 serve1 后，serve2 的 `GET /session/{id}` | **200** |
| 2 | 两个 serve 上 `GET /session/{id}/message` 条数 | **3 → 3，一致** |
| 3 | 同一 session 第二轮换 agent 后复述第一轮读到的 token | **准确复述** |
| 4 | 收窄配置下写出允许的那一个文件 | **写入成功** |
| 5 | 同一会话里换 `build` 写另一个文件 | **被拒，文件未创建** |
| 6 | 拒绝原因出现在工具结果里 | **是**，带规则原文 |

第 6 项的原文：

```json
{"permission":"*","action":"allow","pattern":"*"},
{"permission":"edit","pattern":"*","action":"deny"},
{"permission":"edit","pattern":"tests/only.spec.ts","action":"allow"}
```

第 4、5 项是分开验的：收窄配置允许 `tests/only.spec.ts`，第 5 项让 `build` 去写
`tests/forbidden.spec.ts`。之所以用 `build` 而不是 `recording-emitter`，是因为 emitter 的
提示词本身就写着「只准写 target_path」，它会在调用工具**之前**就用文字拒绝，
测不到运行时权限。`build` 没有这条约束，才是有效的探针。

## 顺带纠正的两处

### 1. `POST /session` 的 `model` 形状和 prompt 不一样

```python
# 建会话
{"title": ..., "agent": ..., "model": {"id": "gpt-6-luna", "providerID": "cctq-codex"}}
# 发消息
{"parts": [...], "agent": ..., "model": {"providerID": "cctq-codex", "modelID": "gpt-6-luna"}}
```

`modelID` 传给建会话会直接 400 `{"_tag":"BadRequest"}`。生产代码里
`emit_recording()` 用的正是 `{'providerID', 'id'}`（`_split_model` 的返回值），是对的；
新写的代码不要凭印象拼。

### 2. `prompt_async` 对同一 session 可以连续调用

返回 204，不等待。判断一轮结束仍用 `GET /session/status`：先等它进入 busy，再等它离开。
同一 session 并发会 `Session is busy`，所以 `EmitSession` 内部要有一把锁。

## 对实现的影响

- 方案 A 成立，不需要退到「一个 serve 到底 + 靠 guard 兜底」。
- 切换 agent 是**每条消息**的属性，不是会话终身的——`prompt_async` 里带 `agent` 即可。
- 收窄配置对新 serve 上续用的旧 session 生效，所以「写轮换 serve」不会丢权限保证。
- 仍然要做的自检：`begin_write` 比对消息条数。这次实测一致，但这是防「壳回来了、part 没回来」
  的唯一手段，不能省。

## 复现方式

```bash
.venv/Scripts/python.exe shots/_check_emit_session.py
```

脚本做的是：临时 git 仓库 + `README.md` 埋一个随机 token →
serve1（base config）建会话让 planner 读 token → 杀 serve1 → serve2（`_emit_config` 收窄）→
`GET /session/{id}`、比对消息数 → emitter 写允许的文件 → `build` 写禁止的文件。
升级 opencode 后重跑一次，六项全绿才说明这套设计仍然成立。
