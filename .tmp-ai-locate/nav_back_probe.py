# -*- coding: utf-8 -*-
"""实测：点击导致页面变化后，go_back 能不能把「当时那一页」找回来。

这决定「自动退回」方案成不成立：
- 后退后 URL / DOM 节点数 / 目标定位的命中数是否都回到点击前；
- go_forward 能不能再回到点击后的状态（不然用户选完就被困在旧页面）。
"""
import json

from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'


def login(page):
    page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2500)
    page.get_by_text('账号密码登录').click(timeout=10000)
    page.wait_for_timeout(800)
    page.get_by_label('手机号或邮箱').fill(PHONE)
    page.get_by_label('密码', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='登 录').click()
    page.wait_for_timeout(6000)
    if page.get_by_text('请选择登录身份').count():
        page.get_by_text('dyh', exact=True).first.click(timeout=10000)
        page.wait_for_timeout(7000)


FINGERPRINT = """() => ({
  url: location.href,
  nodes: document.querySelectorAll('*').length,
  textbox: document.querySelectorAll('textarea,[contenteditable=true]').length,
  links: document.querySelectorAll('a').length,
  buttons: document.querySelectorAll('button,[role=button]').length,
  title: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 50),
  hist: history.length,
  menus: document.querySelectorAll('[class*="_itemMenu_"]').length,
  sidebar: document.querySelectorAll('[class*="_sidebarList"]').length,
})"""


def fp(page):
    try:
        return page.evaluate(FINGERPRINT)
    except Exception as exc:
        return {'error': str(exc)[:80]}


def nav_items(page):
    return page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('a[href], [role=menuitem], nav li, aside li').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 12) return;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 20);
        if (!t) return;
        out.push({ tag: el.tagName.toLowerCase(), text: t, href: el.getAttribute('href') || null });
      });
      return out;
    }""")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        login(page)
        print('登录后:', fp(page))

        items = nav_items(page)
        print('\n可点导航项 %d 个：' % len(items))
        seen = set()
        for it in items:
            key = (it['text'], it['href'])
            if key in seen:
                continue
            seen.add(key)
            print('  ', it['tag'], '|', it['text'], '|', it['href'])

        # 点「知识库」：主区整块换掉，是「真导航」里最典型的一种。
        target = {'text': '知识库'}
        before = fp(page)
        print('\n点击前:', before)
        # 主页独有元素：聊天输入框。用录制器真会给的那条定位来数。
        chat = page.get_by_role('textbox')
        print('  聊天输入框（getByRole textbox）命中:', chat.count())
        page.get_by_text('知识库', exact=True).first.click(timeout=10000)
        page.wait_for_timeout(3000)
        after = fp(page)
        print('点击后:', after)
        try:
            print('  聊天输入框命中:', chat.count())
        except Exception as exc:
            print('  聊天输入框命中: 报错', str(exc)[:60])

        page.go_back(wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        back = fp(page)
        print('后退后:', back)
        try:
            print('  聊天输入框命中:', chat.count())
        except Exception as exc:
            print('  聊天输入框命中: 报错', str(exc)[:60])

        page.go_forward(wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        forward = fp(page)
        print('前进后:', forward)

        print('\n判定：')
        print('  后退回到点击前的 URL：', back.get('url') == before.get('url'))
        print('  后退回到点击前的 DOM 规模（±5%）：',
              abs(back.get('nodes', 0) - before.get('nodes', 0)) <= before.get('nodes', 0) * 0.05)
        print('  后退后侧栏菜单项恢复：', back.get('menus'), 'vs', before.get('menus'))
        print('  前进回到点击后的 URL：', forward.get('url') == after.get('url'))
        page.screenshot(path='D:/autoproject/Auto_case/.tmp-ai-locate/nav-back.png')
        ctx.storage_state(path='D:/autoproject/Auto_case/.tmp-ai-locate/auth.json')
        browser.close()


if __name__ == '__main__':
    main()
