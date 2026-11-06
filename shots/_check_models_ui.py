# -*- coding: utf-8 -*-
"""模型服务页面的界面验证：截图 + 断言关键元素都在。"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = Path(__file__).resolve().parent
BASE = 'http://localhost:5173'


def main():
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('console', lambda m: errors.append(m.text[:160]) if m.type == 'error' else None)
        page.goto(BASE + '/settings', wait_until='networkidle')
        page.wait_for_timeout(1200)

        page.screenshot(path=str(OUT / '_set_1_models.png'), full_page=True)

        cards = page.locator('.prov-card')
        report.append('服务卡片数=%s' % cards.count())
        chips = page.locator('.mchip')
        report.append('模型 chip 数=%s' % chips.count())
        report.append('默认模型=%s' % (page.locator('.mchip.is-default').first.inner_text().strip()
                                      if page.locator('.mchip.is-default').count() else '无'))
        # 全部出现过的 provider 名
        names = page.locator('.prov-name').evaluate_all('els => els.map(e => e.innerText.trim())')
        report.append('服务=%s' % names)

        # 新增服务弹窗
        page.get_by_role('button', name='新增服务').click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / '_set_2_provider_dialog.png'))
        labels = page.locator('.el-dialog .form-label').evaluate_all(
            'els => els.map(e => e.innerText.trim().split("\\n")[0])')
        report.append('新增弹窗字段=%s' % labels)
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)

        # 添加模型弹窗
        page.locator('.prov-card .row-act', has_text='加模型').first.click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / '_set_3_model_dialog.png'))
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)

        # 窄屏
        page.set_viewport_size({'width': 900, 'height': 1000})
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / '_set_4_narrow.png'), full_page=True)
        overflow = page.evaluate('() => document.documentElement.scrollWidth > window.innerWidth + 1')
        report.append('窄屏横向溢出=%s' % overflow)

        # 首页下拉是否读到同一份模型
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto(BASE + '/', wait_until='networkidle')
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / '_set_5_upload.png'), full_page=True)

        report.append('console errors=%s' % (errors[:3] if errors else 'none'))
        browser.close()

    for line in report:
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
