"""用真实项目文件（脱敏）验证索引器 —— 只做观测，不修改 indexer.py。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from indexer import ASSIGN_CELL_RE, Index  # noqa: E402

ROOT = Path(__file__).parent / "real"
VAR_RE = re.compile(r"\$\{([^}]+)\}")
ASSIGN_RE = re.compile(r"^\s*\$\{([^}=]+)\}\s*=")

idx = Index.build(ROOT)

print("=" * 78)
print("1. 解析结果")
print("=" * 78)
st = idx.stats()
print(f"文件 {st['files']} 个 / 关键字 {st['keywords']} 个 / 全文 {st['chars']} 字符 "
      f"/ 签名 {st['chars'] and st['signature_chars']} 字符 / 耗时 {idx.elapsed * 1000:.1f}ms")
print()
for path, rf in idx.files.items():
    print(f"--- {path}")
    print(f"    Resource 引用 {len(rf.resources)} 条 → 解析成功 {len(rf.resolved)} 条")
    for raw, res in zip(rf.resources, rf.resolved):
        ok = "OK" if (ROOT / res).exists() else "MISSING"
        print(f"      [{ok}] {raw}")
    if rf.problems:
        print(f"    problems: {rf.problems}")
    for kw in rf.keywords:
        print(f"    * {kw.signature}")
        if kw.comment:
            print(f"        注释: {kw.comment[:60]}")

print()
print("=" * 78)
print("2. 闭包（从操作日志.resource 出发）")
print("=" * 78)
clo = idx.closure("测试用例层/日常运维/日志管理/操作日志.resource")
for p in sorted(clo):
    print("   ", p)
print(f"    闭包内 token 估算: {idx.stats(clo)['tokens_signature']}")

print()
print("=" * 78)
print("3. 检索召回（模拟一句需求 → 应命中哪个关键字）")
print("=" * 78)
CASES = [
    ("验证操作日志列表第一条记录的用户名和操作结果", "操作日志列表断言"),
    ("检查操作日志列表总共有多少条数据", "操作日志列表数据条数"),
    ("打开操作日志详情看请求内容里的对象", "操作日志详情"),
    ("没有权限时操作日志三级菜单应该不可见", "操作日志三级菜单不可见"),
    ("进入安全日志三级菜单", "进入安全日志三级菜单"),
    ("校验安全日志第二行记录的操作时长", "安全日志列表断言"),
    ("等待安全日志列表加载完成", "安全日志列表加载完成"),
]
hit = 0
for query, expect in CASES:
    res = idx.search(query, top=3)
    names = [(s, k.name) for s, k in res]
    top1 = names[0][1] if names else None
    ok = "HIT " if top1 == expect else ("TOP3" if expect in [n for _, n in names] else "MISS")
    if ok != "MISS":
        hit += 1
    print(f"[{ok}] {query}")
    print(f"       期望: {expect}")
    for s, n in names:
        print(f"       {s:>5}  {n}")
print(f"召回 {hit}/{len(CASES)}（更严格口径：top1 = {sum(1 for q, e in CASES for s, k in idx.search(q, 1)[:1] if k.name == e)}/{len(CASES)}）")

print()
print("=" * 78)
print("4. 变量可解析率（补强后：解析 *** Variables *** 与 .py 变量文件）")
print("=" * 78)
used: set[str] = set()
for kw in idx.keywords:
    local = set()
    for a in kw.args:                       # [Arguments] 声明的参数
        m = VAR_RE.search(a)
        if m:
            local.add(m.group(1))
    for line in kw.raw_body:                # 本关键字内赋值过的变量
        for cell in re.split(r"\t+| {2,}", line.strip()):
            if ASSIGN_CELL_RE.match(cell):
                m = VAR_RE.search(cell)
                if m:
                    local.add(m.group(1))
            else:
                break
    for line in kw.raw_body:
        used.update(n for n in VAR_RE.findall(line) if n not in local)
resolved = {n: idx.resolve_variable(n) for n in used}
ok = {n: v for n, v in resolved.items() if v}
miss = sorted(n for n, v in resolved.items() if not v)
print(f"关键字体内引用的变量: {len(used)} 个")
print(f"能定位到定义来源: {len(ok)} 个 ({len(ok) / max(len(used), 1) * 100:.0f}%)  "
      f"（补强前为 0%）")
print(f"仍解析不到: {len(miss)} 个 → {miss}")
print()
print("抽样（变量 → 定义文件 / 值）：")
for name in ["操作日志列表的用户名称", "操作日志列表的操作结果", "共x条", "hostIp", "hostIpbj"]:
    got = idx.resolve_variable(name)
    if got:
        where, val = got
        print(f"  ${{{name}}}")
        print(f"      {where}")
        print(f"      {val[:70]}")
    else:
        print(f"  ${{{name}}}  → 未找到")

print()
print("=" * 78)
print("5. 调用关系与内部行为（决定「复用后还要不要手工等待/断言」）")
print("=" * 78)
for kw in idx.keywords:
    if not (kw.calls or kw.flags):
        continue
    print(f"  {kw.signature}")
    print(f"      调用本地关键字: {kw.calls or '无'}")
    print(f"      内部行为: {kw.flags or '无'}")
    print(f"      库关键字: {len(kw.body_kw)} 种 {kw.body_kw[:4]}")


print()
print("=" * 78)
print("6. 交付给模型的签名清单（渲染效果，含调用与行为提示）")
print("=" * 78)
print(idx.render_signatures())
