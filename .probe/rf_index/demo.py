"""索引器验证：解析准确率、闭包收益、检索召回、token 对比。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from indexer import Index  # noqa: E402

ROOT = Path(__file__).parent / "data"
START = "测试用例/网元列表/网元列表.robot"

QUERIES = [
    "新增网元",
    "复位",
    "滑块",
    "验证码识别",
    "安全日志",
    "导出",
    "进日志管理",
    "告警",
]


def main() -> None:
    idx = Index.build(ROOT)

    print("=" * 78)
    print("1. 扫描结果")
    print("=" * 78)
    total = idx.stats()
    print(f"文件 {total['files']} 个 / 关键字 {total['keywords']} 个 / 原文 {total['chars']:,} 字符")
    print(f"解析耗时 {idx.elapsed * 1000:.1f} ms")
    print()
    print(f"{'文件':<52}{'关键字':>6}{'依赖':>6}{'字符':>8}")
    for path, rf in sorted(idx.files.items()):
        print(f"{path:<52}{len(rf.keywords):>6}{len(rf.resolved):>6}{rf.chars:>8,}")
        for problem in rf.problems:
            print(f"    ! {problem}")

    print()
    print("=" * 78)
    print("2. 引用闭包（从用例出发，深度 2）")
    print("=" * 78)
    closed = idx.closure(START, depth=2)
    c = idx.stats(closed)
    print(f"闭包 {c['files']} 个文件（全项目 {total['files']} 个），关键字 {c['keywords']}/{total['keywords']}")
    for path in sorted(closed):
        print(f"  - {path}")
    print()
    print(f"闭包原文 {c['chars']:,} 字符，仅占全项目 {c['chars'] / total['chars'] * 100:.0f}%")

    print()
    print("=" * 78)
    print("3. 检索验证（不限定闭包 / 限定闭包）")
    print("=" * 78)
    for query in QUERIES:
        hits = idx.search(query, top=3)
        limited = idx.search(query, top=3, limit_to=closed)
        print(f"\n查询：{query!r}")
        print(f"  全库  : " + (" | ".join(f"{k.signature} ({s})" for s, k in hits) or "无命中"))
        print(f"  闭包内: " + (" | ".join(f"{k.signature} ({s})" for s, k in limited) or "无命中"))

    print()
    print("=" * 78)
    print("4. token 估算（中文按 1.2 字符/token 粗估）")
    print("=" * 78)
    print(f"a. 全项目原文（不可行）        {total['chars']:>9,} 字符  ≈ {total['tokens_full']:>7,} token")
    print(f"b. 全项目签名索引              {total['signature_chars']:>9,} 字符  ≈ {total['tokens_signature']:>7,} token")
    print(f"c. 闭包原文（深度2）           {c['chars']:>9,} 字符  ≈ {c['tokens_full']:>7,} token")
    print(f"d. 闭包签名索引                {c['signature_chars']:>9,} 字符  ≈ {c['tokens_signature']:>7,} token")

    sample_query = "新增网元"
    picked = [k for _, k in idx.search(sample_query, top=5, limit_to=closed)]
    picked_chars = sum(len(k.one_line()) + len(k.doc) + len(k.comment) for k in picked)
    print(f"e. 检索命中 5 条（签名+文档）  {picked_chars:>9,} 字符  ≈ {int(picked_chars / 1.2):>7,} token")

    print()
    print("=" * 78)
    print("5. 签名索引样例（闭包内，可直接进提示词）")
    print("=" * 78)
    print(idx.render_signatures(closed))


if __name__ == "__main__":
    main()
