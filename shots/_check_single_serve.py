"""真机验证「一个 serve 走到底 + 允许辅助文件 + 越界能还原」这条链。

用的是生产类（EmitSession + guard），不是手写 HTTP：
  1. 规划轮之后 serve 还在，写入轮不重启进程（_planner is _active）
  2. 同一 session 里 emitter 能写出 target 以外的 helper 文件
  3. guard 能把它识别成越界
  4. capture 能导出它的原始状态（新建 → delete），revert 能把它删掉
  5. target 本身不在越界清单里（还原不会碰它）

用法（从仓库根目录）：
  .venv/Scripts/python.exe shots/_check_single_serve.py
"""
import json
import random
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording import guard  # noqa: E402
from app.services.recording.emit_session import EmitSession  # noqa: E402

BIN = r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "cctq-codex/gpt-6-luna"
TOKEN = "ACSERVE-" + "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", k=6))
TARGET = "tests/a.spec.ts"
HELPER = "tests/demo-helper.ts"

APP_CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
              "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}


def make_workspace():
    root = Path(tempfile.mkdtemp(prefix="ac-single-"))
    (root / "package.json").write_text(
        json.dumps({"name": "single", "devDependencies": {"@playwright/test": "^1.0.0"}},
                   indent=2), encoding="utf-8")
    (root / "playwright.config.ts").write_text(
        "import { defineConfig } from '@playwright/test';\n"
        "export default defineConfig({ testDir: './tests' });\n", encoding="utf-8")
    (root / "README.md").write_text("access token: %s\n" % TOKEN, encoding="utf-8")
    (root / "tests").mkdir()
    (root / TARGET).write_text("// placeholder\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "-c", "user.email=s@l", "-c", "user.name=s",
                    "commit", "-qm", "init"], cwd=str(root), check=True)
    return root


def main():
    runner = OpenCodeRunner(APP_CONFIG)
    root = make_workspace()
    session = EmitSession("check-single", str(root), "playwright-ts", MODEL, runner)
    results = {}
    try:
        print("workspace:", root)
        print("token:", TOKEN)

        print("\n=== 第一轮：planner 读 token ===")
        reply = session.plan_turn("请读 README.md，只回复里面的 access token，不要做别的事。",
                                  timeout=240)
        print("  回复:", reply[:160].replace("\n", " "))
        results["planner_read_token"] = TOKEN in reply
        serve_after_plan = session._active
        print("  snapshot:", session.snapshot())

        print("\n=== 第二轮：同一 session 写 target + helper ===")
        scope = session.begin_write(TARGET)
        print("  begin_write ->", scope)
        results["begin_write_scope"] = scope
        results["serve_not_restarted"] = session._planner is session._active is serve_after_plan

        reply2 = session.emitter_turn(
            "写两个文件，都要真的调用工具写出来：\n"
            "1. `%s`：一个 Playwright 用例，标题 demo，内容只要 `expect(1).toBe(1)`。\n"
            "2. `%s`：内容 `export const helper = 1;`（模拟脚本需要的辅助文件）。\n"
            "写完告诉我这两个文件的路径。" % (TARGET, HELPER),
            timeout=420,
        )
        print("  回复:", reply2[-300:].replace("\n", " "))
        results["target_written"] = TARGET in reply2 or (root / TARGET).is_file()
        results["helper_written"] = (root / HELPER).is_file()
        print("  target 存在:", (root / TARGET).is_file(), "| helper 存在:", results["helper_written"])
        print("  snapshot:", session.snapshot())

        print("\n=== 越界检测 ===")
        snap = getattr(session, "_guard_snapshot", None) or guard.snapshot(root)
        # 这里重新取一次快照会得到「当前状态」，所以要靠外面先存。
        print("  （快照在生成前取，见脚本开头）")

    finally:
        session.close('check')
        shutil.rmtree(root, ignore_errors=True)


def main_with_snapshot():
    """真正的顺序：先取快照，再跑会话，最后比对。"""
    runner = OpenCodeRunner(APP_CONFIG)
    root = make_workspace()
    session = EmitSession("check-single", str(root), "playwright-ts", MODEL, runner)
    results = {}
    try:
        print("workspace:", root)
        print("token:", TOKEN)

        print("\n=== 第一轮：planner 读 token（同时验证一个 serve） ===")
        before = guard.snapshot(root)
        target_before = (root / TARGET).read_text(encoding="utf-8", errors="replace")
        reply = session.plan_turn(
            "这是一次会话连通性检查，不要输出任务书 JSON。请读 README.md，"
            "只回复里面那串 access token，不要做别的。",
            timeout=600)
        print("  回复:", reply[:160].replace("\n", " "))
        # planner 的 agent 提示词要求它输出任务书 JSON，会盖过「只回 token」；
        # 这里只要求它正常应答，重点是同一个 session 能不能接着走第二轮。
        results["planner_replied"] = bool(reply.strip())
        serve_after_plan = session._active

        print("\n=== 第二轮：同一 session 写 target + helper ===")
        scope = session.begin_write(TARGET)
        print("  begin_write ->", scope)
        results["begin_write_scope"] = scope
        results["serve_not_restarted"] = session._planner is session._active is serve_after_plan

        # 这一轮测的是「宽权限下能写非目标文件 → guard 认得出 → 能还原」，
        # 不测 emitter 的提示词：emitter 有「必须来自真实录制流程」的约束，
        # 裸消息（没有 steps / framework）会被它正当拒绝，而完整 payload 由
        # emit.generate 拼，这里没必要再造一份。workspace 权限规则本身
        # 已在 _check_wide_scope.py 里验证过（emitter 的 edit 是 *: allow）。
        reply2 = session._send(
            session._active,
            "这个项目有一条硬性约定：可复用的动作必须抽到 `tests/demo-helper.ts` 里再 import，"
            "不要内联。请按这条约定写两个文件，都要真的调用工具写出来：\n"
            "1. `%s`：`export const helper = () => 1;`\n"
            "2. `%s`：从 './demo-helper' 导入 helper，一个标题为 demo 的用例，"
            "断言 `expect(helper()).toBe(1)`。\n"
            "写完告诉我这两个文件的路径。" % (HELPER, TARGET),
            "build", 420, "write",
        )
        print("  回复:", reply2[-260:].replace("\n", " "))
        results["target_written"] = ((root / TARGET).is_file()
                                     and (root / TARGET).read_text(encoding="utf-8",
                                                                   errors="replace") != target_before)
        results["helper_written"] = (root / HELPER).is_file()
        print("  target 存在:", results["target_written"],
              "| helper 存在:", results["helper_written"])
        print("  snapshot:", session.snapshot())

        print("\n=== 越界检测 / 备份 / 还原 ===")
        changed = guard.changed(before)
        outside = guard.violations(changed, TARGET)
        print("  changed:", changed)
        print("  outside:", outside)
        results["outside_has_helper"] = HELPER.replace('/', '\\') in outside or HELPER in outside
        results["outside_excludes_target"] = TARGET not in outside

        token, entries = guard.capture(before, outside) if outside else (None, [])
        print("  backup_id:", token, "| entries:", entries)
        results["backup_created"] = bool(token)
        results["helper_marked_delete"] = any(
            e['path'].replace('\\', '/') == HELPER and e['action'] == 'delete' for e in entries)

        if token and results["helper_written"]:
            reverted = guard.revert(root, token, [HELPER])
            print("  revert:", reverted)
            results["helper_removed"] = not (root / HELPER).exists()
            results["target_untouched"] = (root / TARGET).is_file()

        print("\n=== 汇总 ===")
        checks = [
            ("1. planner 正常应答", results.get("planner_replied")),
            ("2. 写入轮没重启 serve", results.get("serve_not_restarted")),
            ("3. scope=workspace", (results.get("begin_write_scope") or {}).get("scope") == "workspace"),
            ("4. target 被改写", results.get("target_written")),
            ("5. helper 写出来了（越界）", results.get("helper_written")),
            ("6. guard 认出 helper", results.get("outside_has_helper")),
            ("7. 清单不含 target", results.get("outside_excludes_target")),
            ("8. 生成了备份", results.get("backup_created")),
            ("9. helper 标为 delete", results.get("helper_marked_delete")),
            ("10. 还原后 helper 没了", results.get("helper_removed")),
            ("11. 还原不碰 target", results.get("target_untouched")),
        ]
        for name, ok in checks:
            print("  %-28s %s" % (name, "PASS" if ok else "FAIL"))
        return 0 if all(ok for _n, ok in checks) else 1
    finally:
        session.close('check')
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_with_snapshot())
