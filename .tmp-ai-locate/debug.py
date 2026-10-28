# -*- coding: utf-8 -*-
"""调试：/global 页面到底有没有目标元素，以及 evaluate 传参是否正常。"""
from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
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
        page.get_by_label('手机号或邮箱').fill('13609266230')
        page.get_by_label('密码', exact=True).fill('123456789')
        page.get_by_role('button', name='登 录').click()
        page.wait_for_timeout(6000)
        page.get_by_text('dyh', exact=True).first.click(timeout=10000)
        page.wait_for_timeout(9000)
        print('url:', page.url)

        print('textarea 数:', page.evaluate("() => document.querySelectorAll('textarea').length"))
        print('a 数:', page.evaluate("() => document.querySelectorAll('a').length"))
        print('a 文本:', page.evaluate("() => [...document.querySelectorAll('a')].map(e => e.innerText.replace(/\\s+/g,' ').trim()).slice(0, 20)"))
        print('innerText 总长:', page.evaluate("() => document.body.innerText.length"))
        print('body 前 200 字:', page.evaluate("() => document.body.innerText.slice(0, 200)"))

        # 测试 evaluate 传参
        got = page.evaluate("(arg) => arg", {'id': 'T1'})
        print('传参测试:', got)

        page.screenshot(path=OUT + '/debug.png')
        browser.close()


if __name__ == '__main__':
    main()
