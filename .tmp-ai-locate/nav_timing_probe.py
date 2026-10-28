# -*- coding: utf-8 -*-
"""实测二：换页到底有多快，以及是不是所有导航都走 replace。

两个结论直接对应两个方案：
- 若会话切换也不进历史栈 → 「自动退回」整体不成立（不只导航，连列表点击也会退丢）；
- 页面在 click 后多少毫秒发生变化 → 决定「点击前截图」来不来得及。
"""
from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'

WATCH = """() => {
  window.__p = { t0: null, goneAt: null, urlAt: null, url0: location.href };
  document.addEventListener('click', () => {
    if (window.__p.t0 != null) return;
    window.__p.t0 = performance.now();
    window.__p.url0 = location.href;
    const tick = () => {
      if (window.__p.t0 == null) return;
      if (window.__p.goneAt == null
          && document.querySelectorAll('textarea,[contenteditable=true]').length === 0) {
        window.__p.goneAt = performance.now() - window.__p.t0;
      }
      if (window.__p.urlAt == null && location.href !== window.__p.url0) {
        window.__p.urlAt = performance.now() - window.__p.t0;
      }
      if (performance.now() - window.__p.t0 > 3000) return;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, true);
}"""


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


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        login(page)
        print('登录后 url:', page.url, '| history.length:', page.evaluate('() => history.length'))

        # ---- 1. 页面变化的延迟：点「知识库」，量输入框多久消失 ----
        page.evaluate(WATCH)
        hist0 = page.evaluate('() => history.length')
        page.get_by_text('知识库', exact=True).first.click(timeout=10000)
        page.wait_for_timeout(3000)
        watch = page.evaluate('() => window.__p')
        hist1 = page.evaluate('() => history.length')
        print('\n[1] 点「知识库」')
        print('    click → 聊天输入框消失: %s ms' % (
            round(watch['goneAt'], 1) if watch.get('goneAt') is not None else '没消失'))
        print('    click → URL 变化: %s ms' % (
            round(watch['urlAt'], 1) if watch.get('urlAt') is not None else '没变化'))
        print('    history.length: %s → %s（相等说明是 replace，不产生历史条目）' % (hist0, hist1))
        print('    当前 url:', page.url)

        # ---- 2. 直接 goto 能不能回到 /global ----
        page.goto(BASE + '/global', wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        restored = page.evaluate("""() => ({
          url: location.href,
          textbox: document.querySelectorAll('textarea,[contenteditable=true]').length,
          menus: document.querySelectorAll('[class*="_itemMenu_"]').length,
        })""")
        print('\n[2] 直接 goto /global 之后:', restored)

        # ---- 3. 会话切换会不会进历史栈 ----
        before_hist = page.evaluate('() => history.length')
        before_url = page.url
        items = page.evaluate("""() => Array.from(document.querySelectorAll('[class*="_itemMenu_"]'))
          .filter(el => (el.innerText || '').trim())
          .slice(0, 8)
          .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 16))""")
        print('\n[3] 侧栏会话项前 8 个:', items)
        if items:
            page.get_by_text(items[0], exact=True).first.click(timeout=10000)
            page.wait_for_timeout(2500)
            after_hist = page.evaluate('() => history.length')
            print('    点会话「%s」后:' % items[0])
            print('    url: %s → %s' % (before_url, page.url))
            print('    history.length: %s → %s' % (before_hist, after_hist))

        browser.close()


if __name__ == '__main__':
    main()
