"""真实走一遍上传 + 分析，确认第 1 步的完成信息（张数/体积）能取到。

不点「确认生成」，因此不写用例库；分析会话到期自动回收。

    UI_URL=http://localhost:5173/ .venv/Scripts/python.exe shots/_check_progress_real.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get('UI_URL', 'http://localhost:5173/')
IMAGE = os.path.join(ROOT, 'shots', '_case_src_login.png')


def pick_select(page, label, index=0):
    field = page.locator('.form-field').filter(has_text=label).first
    field.locator('.el-select').first.click()
    page.wait_for_timeout(400)
    items = page.locator('.el-select-dropdown__item:visible:not(.is-disabled)')
    if items.count() <= index:
        print('  「%s」下拉没有可选项' % label)
        return False
    items.nth(index).click()
    page.wait_for_timeout(300)
    return True


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(URL, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)

        print('选项目：%s' % pick_select(page, '项目'))
        print('选分析模型：%s' % pick_select(page, '原型分析模型'))
        print('选生成模型：%s' % pick_select(page, '用例生成模型'))

        page.locator('input[type="file"]').first.set_input_files(IMAGE)
        page.wait_for_timeout(2000)

        # 上传后会先弹「填写功能介绍」，关掉它才能点生成
        done = page.locator('.el-overlay:visible button:has-text("完成")')
        if done.count():
            done.first.click()
            page.wait_for_timeout(800)
            print('已关闭功能介绍弹窗')

        page.locator('button:has-text("生成测试用例")').first.click()
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(ROOT, 'shots', '_pv_real_1.png'))
        print('  截图 _pv_real_1.png（分析初期）')

        page.wait_for_timeout(8000)
        page.screenshot(path=os.path.join(ROOT, 'shots', '_pv_real_2.png'))
        print('  截图 _pv_real_2.png（分析中段）')

        notes = page.locator('.pv-note').all_inner_texts()
        print('步骤说明：%s' % notes)

        # 等分析结束（模块审核弹窗出现），确认流程没被显示层改动卡住
        try:
            page.wait_for_selector('.el-dialog:has-text("模块")', timeout=180000)
            print('分析完成，模块弹窗已出现')
            page.screenshot(path=os.path.join(ROOT, 'shots', '_pv_real_3.png'))
        except Exception:
            print('模块弹窗没等到，看截图判断')
            page.screenshot(path=os.path.join(ROOT, 'shots', '_pv_real_3.png'))

        browser.close()

    print('console 错误：%s' % (errors or '无'))


if __name__ == '__main__':
    main()
