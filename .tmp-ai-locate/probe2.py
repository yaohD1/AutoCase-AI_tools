# -*- coding: utf-8 -*-
"""第二步：选身份进主界面，检查 DOM 与候选目标元素。"""
import json

from playwright.sync_api import sync_playwright

STATE = 'D:/autoproject/Auto_case/.tmp-ai-locate/state.json'
BASE = 'https://ai.test.yygu.cn'


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900},
                                  storage_state=STATE)
        page = ctx.new_page()
        page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(3000)
        print('起点 url:', page.url)

        # 选第一个身份：dyh 管理员 test998
        try:
            page.get_by_text('dyh').first.click(timeout=8000)
            page.wait_for_timeout(6000)
            print('选身份后 url:', page.url)
        except Exception as exc:
            print('选身份失败:', exc)
            page.screenshot(path='D:/autoproject/Auto_case/.tmp-ai-locate/fail.png')

        page.screenshot(path='D:/autoproject/Auto_case/.tmp-ai-locate/home.png')

        stats = page.evaluate("""() => {
          const all = document.querySelectorAll('*');
          const interactive = document.querySelectorAll(
            'a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]');
          const rows = document.querySelectorAll('tr,[role=row],li');
          return {total: all.length, interactive: interactive.length, rows: rows.length};
        }""")
        print('元素:', stats)

        # 打印所有可交互元素的简要信息
        info = page.evaluate("""() => {
          const out = [];
          const seen = new Set();
          document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true],svg')
            .forEach((el, i) => {
              if (seen.has(el)) return;
              seen.add(el);
              const r = el.getBoundingClientRect();
              if (r.width < 4 || r.height < 4) return;
              out.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                cls: (el.getAttribute('class') || '').slice(0, 60),
                id: el.id || '',
                testid: el.getAttribute('data-testid') || '',
                aria: el.getAttribute('aria-label') || '',
                ph: el.getAttribute('placeholder') || '',
                text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 40),
                box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
              });
            });
          return out;
        }""")
        print('可见可交互元素数:', len(info))
        for item in info[:40]:
            print(json.dumps(item, ensure_ascii=False))

        html = page.content()
        print('HTML 长度:', len(html))
        with open('D:/autoproject/Auto_case/.tmp-ai-locate/home.html', 'w', encoding='utf-8') as fh:
            fh.write(html)
        with open('D:/autoproject/Auto_case/.tmp-ai-locate/elements.json', 'w', encoding='utf-8') as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1)

        ctx.storage_state(path=STATE)
        browser.close()


if __name__ == '__main__':
    main()
