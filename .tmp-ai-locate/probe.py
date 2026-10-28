# -*- coding: utf-8 -*-
"""探路：真实登录 ai.test.yygu.cn，看 DOM 规模与目标元素，为 AI 定位实验做准备。

只读，不写业务数据。输出：
  - 页面 HTML 总长度（决定「整页 DOM 喂 AI」是否可行）
  - 可交互元素数量
  - 几个历史踩坑元素的快照
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(3000)
        print('title:', page.title())
        print('url:', page.url)

        # 切到账号密码登录
        try:
            page.get_by_text('账号密码登录').click(timeout=8000)
            page.wait_for_timeout(800)
            print('切换到账号密码登录 OK')
        except Exception as exc:
            print('切换失败:', exc)

        page.get_by_label('手机号或邮箱').fill(PHONE)
        page.get_by_label('密码', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='登 录').click()
        page.wait_for_timeout(8000)
        print('登录后 url:', page.url)

        # 进问AI
        try:
            page.get_by_role('link', name='问AI').click(timeout=10000)
            page.wait_for_timeout(4000)
            print('问AI url:', page.url)
        except Exception as exc:
            print('进问AI失败:', exc)

        html = page.content()
        print('HTML 长度:', len(html))

        stats = page.evaluate("""() => {
          const all = document.querySelectorAll('*');
          const interactive = document.querySelectorAll(
            'a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]');
          return {total: all.length, interactive: interactive.length};
        }""")
        print('元素总数:', stats['total'], ' 可交互:', stats['interactive'])
        page.screenshot(path='D:/autoproject/Auto_case/.tmp-ai-locate/after-login.png', full_page=False)

        # 保存 HTML 供离线分析
        with open('D:/autoproject/Auto_case/.tmp-ai-locate/page.html', 'w', encoding='utf-8') as fh:
            fh.write(html)
        print('已保存 page.html')

        ctx.storage_state(path='D:/autoproject/Auto_case/.tmp-ai-locate/state.json')
        browser.close()


if __name__ == '__main__':
    sys.exit(main())
