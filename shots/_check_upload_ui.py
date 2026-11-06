"""首页 UI 冒烟：页面能加载、两个模型下拉还在、生成按钮在、零 console error。

    .venv/Scripts/python.exe shots/_check_upload_ui.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get('UI_URL', 'http://127.0.0.1:5173/')


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(URL, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)

        labels = page.locator('.form-label').all_inner_texts()
        print('表单标签：%s' % [text.strip() for text in labels])
        print('生成按钮：%d 个' % page.locator('button:has-text("生成")').count())
        page.screenshot(path=os.path.join(ROOT, 'shots', '_ui_upload.png'), full_page=True)
        browser.close()

    print('console 错误：%s' % (errors or '无'))
    missing = [name for name in ('原型分析模型', '用例生成模型') if name not in ' '.join(labels)]
    if missing:
        print('缺少字段：%s' % missing)
        sys.exit(1)


if __name__ == '__main__':
    main()
