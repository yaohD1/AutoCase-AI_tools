"""RF 关键字索引器（验证原型，非产品代码）。

目标：
  1. 不读全文也能回答「项目里有哪些关键字、参数是什么」
  2. 从某个用例出发，只取它真正依赖的文件闭包
  3. 给一句中文查询，能在索引里找到对应关键字

只用标准库，纯确定性解析，不做语义推断。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

SECTION_RE = re.compile(r"^\*{3}\s*(settings|variables|test cases|tasks|keywords|comments)\s*\*{3}\s*$", re.I)
RESOURCE_RE = re.compile(r"^Resource\s+(.+?)\s*$", re.I)
VARIABLES_RE = re.compile(r"^Variables\s+(.+?)\s*$", re.I)
VAR_DEF_RE = re.compile(r"^([$@&]\{[^}]+\})\s*(.*)$")
ARG_RE = re.compile(r"^\[Arguments\]\s*(.*)$", re.I)
DOC_RE = re.compile(r"^\[Documentation\]\s*(.*)$", re.I)
CONT_RE = re.compile(r"^\.\.\.\s*(.*)$")
ASSIGN_CELL_RE = re.compile(r"^[$@&]\{[^}]+\}=?$")

SKIP_DIRS = {".git", "node_modules", "__pycache__", "output", "results", "report", "logs"}

# 判断关键字体「内部做了什么」用的特征词 —— 决定复用后还要不要手工等待/断言
WAIT_MARKS = ("wait until", "sleep")
ASSERT_MARKS = ("should ", "should not", "should contain", "should be")
NAV_MARKS = ("click element", "click link", "open browser", "go to")

# 常见库关键字（只为区分「调本地业务关键字」和「调库」，不求全）
LIB_KEYWORDS = {
    "click element", "click link", "input text", "input password", "clear element text",
    "get text", "get value", "get title", "get location", "open browser", "close browser",
    "go to", "reload page", "maximize browser window", "set selenium speed",
    "wait until element is visible", "wait until element contains", "wait until page contains",
    "wait until element is not visible", "wait until page contains element",
    "element should contain", "element should not contain", "element should be visible",
    "page should contain", "page should contain element", "page should not contain element",
    "page should not contain", "select from list by label", "select from list by value",
    "select checkbox", "unselect checkbox", "press keys", "execute javascript",
    "switch window", "select window", "capture page screenshot", "sleep",
    "run keyword if", "run keyword and ignore error", "run keywords", "run keyword if all tests passed",
    "return from keyword", "return from keyword if", "set variable", "should be equal",
    "should not be equal", "should contain", "log", "log many", "comment", "set test variable",
    "set suite variable", "set global variable", "evaluate", "convert to string",
    "get substring", "replace string", "split string", "should match", "should match regexp",
    "get current date", "add time to date", "subtract time from date", "convert date",
}


def split_cells(text: str) -> list[str]:
    """RF 用 2+ 空格或 tab 分列。"""
    return [c.strip() for c in re.split(r"\t+| {2,}", text.strip()) if c.strip()]


@dataclass
class Keyword:
    name: str
    args: list[str]
    doc: str
    comment: str
    path: str
    line: int
    calls: list[str] = field(default_factory=list)      # 调用的本地业务关键字
    body_kw: list[str] = field(default_factory=list)    # 调用的库/内置关键字
    flags: list[str] = field(default_factory=list)      # waits / asserts / navigates
    raw_body: list[str] = field(default_factory=list, repr=False)

    @property
    def signature(self) -> str:
        return "%s(%s)" % (self.name, ", ".join(self.args)) if self.args else self.name

    def one_line(self) -> str:
        lines = (self.doc or self.comment or "").strip().splitlines()
        hint = lines[0].strip() if lines else ""
        extra = []
        if self.calls:
            extra.append("调用 " + "、".join(self.calls[:3]))
        if "waits" in self.flags:
            extra.append("内部已等待")
        if "asserts" in self.flags:
            extra.append("含断言")
        tail = []
        if hint:
            tail.append(hint)
        if extra:
            tail.append("；".join(extra))
        return "  - %s%s" % (self.signature, ("  # " + "｜".join(tail)) if tail else "")


@dataclass
class RfFile:
    path: str
    kind: str
    chars: int
    resources: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    var_files: list[str] = field(default_factory=list)
    var_files_resolved: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    keywords: list[Keyword] = field(default_factory=list)
    testcases: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _join_continuation(body: list[str], pattern: re.Pattern) -> list[str]:
    """把 `[Arguments]` / `[Documentation]` 的 `...` 续行合并成一条。"""
    merged: list[str] = []
    collecting = False
    for line in body:
        m = pattern.match(line)
        if m:
            merged.append(m.group(1).strip())
            collecting = True
            continue
        m2 = CONT_RE.match(line)
        if m2 and collecting:
            merged.append(m2.group(1).strip())
            continue
        collecting = False
    return merged


def parse_file(path: Path, root: Path) -> RfFile:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rel = path.relative_to(root).as_posix()
    rf = RfFile(path=rel, kind=path.suffix.lstrip("."), chars=len(text))

    lines = text.splitlines()
    section = None
    pending_comment: list[str] = []
    i = 0

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        m = SECTION_RE.match(stripped)
        if m:
            section = m.group(1).lower()
            pending_comment = []
            i += 1
            continue

        if not stripped:
            pending_comment = []
            i += 1
            continue

        if section == "settings":
            rm = RESOURCE_RE.match(stripped)
            if rm:
                rf.resources.append(rm.group(1).strip())
                i += 1
                continue
            vm = VARIABLES_RE.match(stripped)
            if vm:
                rf.var_files.append(vm.group(1).strip())
            i += 1
            continue

        if section == "variables":
            vd = VAR_DEF_RE.match(stripped)
            if vd:
                rf.variables[vd.group(1)[2:-1]] = vd.group(2).strip()
            i += 1
            continue

        if section == "keywords":
            if stripped.startswith("#"):
                pending_comment.append(stripped.lstrip("#").strip())
                i += 1
                continue
            if raw.startswith((" ", "\t")):
                i += 1
                continue
            # 顶格非注释 = 新关键字
            kw = Keyword(name=stripped, args=[], doc="", comment="\n".join(pending_comment), path=rel, line=i + 1)
            pending_comment = []
            i += 1
            body: list[str] = []
            while i < len(lines):
                r2 = lines[i]
                s2 = r2.strip()
                if s2 and not r2.startswith((" ", "\t")):
                    break
                if SECTION_RE.match(s2):
                    break
                if s2:
                    body.append(s2)
                i += 1
            kw.raw_body = body
            args_lines = _join_continuation(body, ARG_RE)
            kw.args = [c for line in args_lines for c in split_cells(line)]
            doc_lines = _join_continuation(body, DOC_RE)
            kw.doc = " ".join(doc_lines).strip()
            rf.keywords.append(kw)
            continue

        if section in ("test cases", "tasks"):
            if not raw.startswith((" ", "\t")) and not stripped.startswith("#"):
                rf.testcases.append(stripped)
            i += 1
            continue

        i += 1

    # 解析 Resource / Variables 引用（相对当前文件）
    for raw_path in rf.resources:
        target = (path.parent / raw_path).resolve()
        try:
            rf.resolved.append(target.relative_to(root).as_posix())
        except ValueError:
            rf.problems.append("引用在工作区外: %s" % raw_path)
        else:
            if not target.exists():
                rf.problems.append("引用不存在: %s" % raw_path)

    for raw_path in rf.var_files:
        target = (path.parent / raw_path).resolve()
        try:
            rf.var_files_resolved.append(target.relative_to(root).as_posix())
        except ValueError:
            rf.problems.append("变量文件在工作区外: %s" % raw_path)
        else:
            if not target.exists():
                rf.problems.append("变量文件不存在: %s" % raw_path)
    return rf


def _norm(name: str) -> str:
    """RF 关键字名大小写、空格、下划线均不敏感。"""
    return re.sub(r"[\s_]+", "", name.lower())


class Index:
    def __init__(self, root: Path):
        self.root = root
        self.files: dict[str, RfFile] = {}
        self.keywords: list[Keyword] = []
        self.py_vars: dict[str, str] = {}
        self.elapsed = 0.0

    @classmethod
    def build(cls, root: str | Path) -> "Index":
        root = Path(root).resolve()
        started = time.perf_counter()
        index = cls(root)
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".robot", ".resource"):
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            rf = parse_file(path, root)
            index.files[rf.path] = rf
            index.keywords.extend(rf.keywords)
        index._link()
        index._load_py_vars()
        index.elapsed = time.perf_counter() - started
        return index

    # ---- 第二遍：调用图 + 行为标记 ----
    def _link(self) -> None:
        by_norm: dict[str, Keyword] = {}
        for kw in self.keywords:
            by_norm.setdefault(_norm(kw.name), kw)
        lib_norm = {_norm(name) for name in LIB_KEYWORDS}
        for kw in self.keywords:
            for stmt in kw.raw_body:
                low = stmt.lower()
                if any(m in low for m in WAIT_MARKS) and "waits" not in kw.flags:
                    kw.flags.append("waits")
                if any(m in low for m in ASSERT_MARKS) and "asserts" not in kw.flags:
                    kw.flags.append("asserts")
                if any(m in low for m in NAV_MARKS) and "navigates" not in kw.flags:
                    kw.flags.append("navigates")
                for cell in split_cells(stmt):
                    n = _norm(cell)
                    if not n or n[0] in "${@&'":
                        continue
                    target = by_norm.get(n)
                    if target is not None and target is not kw:
                        if target.name not in kw.calls:
                            kw.calls.append(target.name)
                    elif n in lib_norm and cell not in kw.body_kw:
                        kw.body_kw.append(cell)

    def _load_py_vars(self) -> None:
        for rf in self.files.values():
            for vf in rf.var_files_resolved:
                if not vf.lower().endswith(".py"):
                    continue
                try:
                    text = (self.root / vf).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", line)
                    if m:
                        self.py_vars.setdefault(m.group(1), m.group(2))

    def resolve_variable(self, name: str) -> tuple[str, str] | None:
        """变量来自哪个文件、值是什么。返回 (文件相对路径, 值)。"""
        for path, rf in self.files.items():
            if name in rf.variables:
                return path, rf.variables[name]
        if name in self.py_vars:
            return "(.py 变量文件)", self.py_vars[name]
        return None

    # ---- 引用闭包 ----
    def closure(self, start: str, depth: int = 2) -> set[str]:
        seen: set[str] = set()
        frontier = [(start, 0)]
        while frontier:
            path, d = frontier.pop()
            if path in seen or d > depth:
                continue
            seen.add(path)
            rf = self.files.get(path)
            if not rf:
                continue
            for ref in rf.resolved:
                if ref not in seen:
                    frontier.append((ref, d + 1))
        return seen

    # ---- 检索 ----
    @staticmethod
    def _grams(text: str) -> set[str]:
        t = re.sub(r"[\s\W_]+", "", text.lower())
        if not t:
            return set()
        grams = {t[i:i + 2] for i in range(len(t) - 1)}
        grams |= set(t)
        return grams

    def search(self, query: str, top: int = 8, limit_to: set[str] | None = None) -> list[tuple[float, Keyword]]:
        q = self._grams(query)
        if not q:
            return []
        scored: list[tuple[float, Keyword]] = []
        for kw in self.keywords:
            if limit_to is not None and kw.path not in limit_to:
                continue
            name = self._grams(kw.name)
            note = self._grams(kw.doc + kw.comment)
            args = self._grams(" ".join(kw.args))
            cover = lambda s: len(q & s) / len(q)
            score = cover(name) * 3.0 + cover(note) * 1.2 + cover(args) * 0.4
            if query in kw.name:
                score += 2.0
            if query and query in (kw.doc + kw.comment):
                score += 1.0
            if score > 0.6:
                scored.append((round(score, 2), kw))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        return scored[:top]

    # ---- 统计 ----
    def stats(self, paths: set[str] | None = None) -> dict:
        files = [f for p, f in self.files.items() if paths is None or p in paths]
        kws = [k for k in self.keywords if paths is None or k.path in paths]
        chars = sum(f.chars for f in files)
        sig_chars = sum(len(k.one_line()) + 1 for k in kws)
        return {
            "files": len(files),
            "keywords": len(kws),
            "chars": chars,
            "signature_chars": sig_chars,
            "tokens_full": int(chars / 1.2),
            "tokens_signature": int(sig_chars / 1.2),
        }

    def render_signatures(self, paths: set[str] | None = None) -> str:
        out: list[str] = []
        last = None
        for kw in self.keywords:
            if paths is not None and kw.path not in paths:
                continue
            if kw.path != last:
                out.append("\n[%s]" % kw.path)
                last = kw.path
            out.append(kw.one_line())
        return "\n".join(out)
