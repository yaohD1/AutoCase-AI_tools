# -*- coding: utf-8 -*-
"""验证一个怀疑：录制器的 cssPath 缺根锚点，导致「看着像绝对路径、实际是任意匹配」。

Playwright 的 locator('div > div:...') 不是从 document 根开始匹配，
而是「文档中任意位置满足该路径」——所以一条本该唯一的路径可能命中多个。
"""
import sys

sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')

from playwright.sync_api import sync_playwright  # noqa: E402

import compare  # noqa: E402
import harness  # noqa: E402


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        harness.open_main(page)

        cases = {
            'H3 折叠箭头': 'H3',
            'H1 会话菜单图标': 'H1',
        }
        for name, tid in cases.items():
            snap = page.evaluate(compare.MARK_HARD_JS, {'tid': tid})
            if not snap:
                print(name, '未找到')
                continue
            css = snap['css']
            print('\n=== %s' % name)
            print('  录制器 css :', css)
            for label, expr in (
                ('原样', css),
                ('加 html > body > ', 'html > body > ' + css),
                ('加 body > ', 'body > ' + css),
            ):
                try:
                    n = page.locator(expr).count()
                except Exception as exc:
                    n = 'ERR %s' % str(exc)[:60]
                print('  %-18s count=%s' % (label, n))
            page.evaluate("(t) => { const e = document.querySelector('[data-ai-probe=\"' + t + '\"]'); "
                          "if (e) e.removeAttribute('data-ai-probe'); }", tid)

        # 统计：整页有多少条录制器风格的路径会「看起来唯一其实不唯一」
        print('\n=== 抽样：录制器 cssPath 的唯一性自检 ===')
        stats = page.evaluate(harness.HELPERS + """
        () => {
          const out = [];
          document.querySelectorAll('a,button,input,textarea,svg,div').forEach((el) => {
            const r = el.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) return;
            if (out.length >= 40) return;
            const css = cssPath(el);
            if (!css.includes(':nth-of-type')) return;   // 只看带序号的，最简单的情形
            let n = 0;
            try { n = document.querySelectorAll(css).length; } catch (e) { n = -1; }
            if (n > 1) out.push({tag: el.tagName.toLowerCase(), css: css, n: n});
          });
          return {bad: out, total: document.querySelectorAll('*').length};
        }""")
        print('  扫描到 %d 条「带 nth-of-type 却不唯一」的路径（取前 10）' % len(stats['bad']))
        for item in stats['bad'][:10]:
            print('    n=%d %s' % (item['n'], item['css'][:100]))

        browser.close()


if __name__ == '__main__':
    main()
