"""验证「添加导入文件」弹窗里的选文件是否真能把字节传到后端。

这个脚本只做一件事：在真实浏览器里点开弹窗，往隐藏的 input 里塞一个文件，
然后看 /recording/assets 的响应和页面提示。之前这里是坏的——axios 默认头是
application/json，把 FormData 序列化成了 JSON，后端一直回「没有收到文件」。
"""

import sys

sys.path.insert(0, 'backend')

from playwright.sync_api import sync_playwright  # noqa: E402

ASSET = r'D:\autoproject\ums_auto - 副本\autotest\.assets\终端资产导入模板.xlsx'


def main():
    calls = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.set_viewport_size({'width': 1600, 'height': 950})
        page.on('response', lambda r: calls.append((r.status, r.url.split('/api')[-1]))
                if '/recording/assets' in r.url else None)

        page.goto('http://localhost:5173/recording', wait_until='domcontentloaded')
        page.wait_for_timeout(3000)

        button = page.get_by_role('button', name='导入文件')
        print('「导入文件」按钮数量:', button.count())
        if button.count() == 0:
            print('页面文字片段:', page.inner_text('body')[:300].replace('\n', ' | '))
            browser.close()
            return
        button.first.click()
        page.wait_for_timeout(800)

        field = page.locator('input.file-hidden')
        print('隐藏 input 数量:', field.count())
        field.first.set_input_files(ASSET)
        page.wait_for_timeout(3000)

        page.screenshot(path='docs/ui-upload-done.png')
        print('资产请求:', calls)
        body = page.inner_text('body')
        for key in ('已存入工作区', '没有收到文件', '工作区里已经有'):
            if key in body:
                idx = body.find(key)
                print('页面含「%s」->' % key, body[max(0, idx - 20):idx + 60].replace('\n', ' '))
        browser.close()


if __name__ == '__main__':
    main()
