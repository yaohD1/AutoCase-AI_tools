"""录制配置卡：主界面 vs 录制面板，只读项与可编辑项是否符合预期。

    .venv/Scripts/python.exe shots/_check_rec_cfg.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('UI_URL', 'http://localhost:5173')


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---- 主界面：完整可编辑 ----
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(BASE + '/recording', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)
        print('主界面 卡片标题：%s' % page.locator('.config-card .card-title').inner_text())
        print('主界面 说明条：%d 条' % page.locator('.cfg-note').count())
        print('主界面 只读标记：%d 个' % page.locator('.cfg-ro').count())
        print('主界面 更换按钮：%d 个' % page.locator('.cfg-act:has-text("更换")').count())
        print('主界面 已记住上次设置：%d 处' % page.locator('.cfg-saved:has-text("已记住")').count())
        page.screenshot(path=os.path.join(ROOT, 'shots', '_rec_cfg_main.png'))
        page.close()

        # ---- 面板：展开配置 ----
        panel = browser.new_page(viewport={'width': 480, 'height': 900})
        panel.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        panel.on('pageerror', lambda e: errors.append(str(e)))
        panel.goto(BASE + '/recording?panel=1&base_url=https://app.corekg.com/login',
                   wait_until='networkidle', timeout=60000)
        panel.wait_for_timeout(2000)
        print('\n面板 配置卡可见（未展开前）：%s' % panel.locator('.config-card').is_visible())

        toggle = panel.locator('.panel-actions button:has-text("配置")')
        print('面板 配置按钮：%d 个' % toggle.count())
        if toggle.count():
            toggle.first.click()
            panel.wait_for_timeout(700)

        print('面板 配置卡可见（展开后）：%s' % panel.locator('.config-card').is_visible())
        print('面板 卡片标题：%s' % panel.locator('.config-card .card-title').inner_text())
        print('面板 说明条：%d 条' % panel.locator('.cfg-note').count())
        if panel.locator('.cfg-note').count():
            print('  说明文字：%s' % panel.locator('.cfg-note').inner_text())
        print('面板 只读标记：%d 个' % panel.locator('.cfg-ro').count())
        print('面板 更换按钮：%d 个' % panel.locator('.cfg-act:has-text("更换")').count())
        print('面板 不会保存提示：%d 处' % panel.locator('.cfg-saved:has-text("不会保存")').count())

        # 点起始地址不该变成输入框
        before = panel.locator('.config-card .cfg-input').count()
        panel.locator('.cfg-field--hero .cfg-view').first.click()
        panel.wait_for_timeout(500)
        after = panel.locator('.config-card .cfg-input').count()
        print('点起始地址后输入框数 %d → %d（应保持不变）' % (before, after))

        # 可编辑项还在：目标文件点一下应出现输入框
        panel.locator('.cfg-field:has-text("目标文件") .cfg-view').first.click()
        panel.wait_for_timeout(500)
        print('点目标文件后输入框数：%d（应+1）' % panel.locator('.config-card .cfg-input').count())
        panel.keyboard.press('Escape')
        panel.wait_for_timeout(300)

        panel.screenshot(path=os.path.join(ROOT, 'shots', '_rec_cfg_panel.png'))

        panel.evaluate("() => { document.documentElement.setAttribute('data-theme','dark'); document.documentElement.classList.add('dark'); }")
        panel.wait_for_timeout(600)
        panel.screenshot(path=os.path.join(ROOT, 'shots', '_rec_cfg_panel_dark.png'))

        browser.close()

    print('\nconsole 错误：%s' % (errors or '无'))


if __name__ == '__main__':
    main()
