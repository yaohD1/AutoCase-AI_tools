# -*- coding: utf-8 -*-
"""一体化：登录 → 选身份 → 进问AI，采集真实页面用于 AI 定位实验。"""
import json

from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'
OUT = 'D:/autoproject/Auto_case/.tmp-ai-locate'


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(2500)

        page.get_by_text('账号密码登录').click(timeout=10000)
        page.wait_for_timeout(800)
        page.get_by_label('手机号或邮箱').fill(PHONE)
        page.get_by_label('密码', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='登 录').click()
        page.wait_for_timeout(6000)
        print('登录后:', page.url, '| 有身份列表?', page.get_by_text('请选择登录身份').count())

        # 选身份
        card = page.get_by_text('dyh', exact=True).first
        print('dyh 命中数:', page.get_by_text('dyh', exact=True).count())
        card.click(timeout=10000)
        page.wait_for_timeout(7000)
        print('选身份后:', page.url)
        page.screenshot(path=OUT + '/main.png')

        stats = page.evaluate("""() => ({
          total: document.querySelectorAll('*').length,
          interactive: document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]').length,
          html: document.documentElement.outerHTML.length,
        })""")
        print('元素统计:', stats)

        # 采集可见元素（含 svg rect 这种历史上踩过坑的）
        info = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true],svg,svg > rect')
            .forEach((el) => {
              const r = el.getBoundingClientRect();
              if (r.width < 4 || r.height < 4 || r.y < 0) return;
              out.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                cls: (el.getAttribute('class') || '').slice(0, 50),
                id: el.id || '',
                aria: el.getAttribute('aria-label') || '',
                ph: el.getAttribute('placeholder') || '',
                text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 30),
                box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
              });
            });
          return out;
        }""")
        print('可见元素数:', len(info))
        for item in info:
            print(json.dumps(item, ensure_ascii=False))

        with open(OUT + '/main.html', 'w', encoding='utf-8') as fh:
            fh.write(page.content())
        with open(OUT + '/main-elements.json', 'w', encoding='utf-8') as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1)

        browser.close()


if __name__ == '__main__':
    main()
