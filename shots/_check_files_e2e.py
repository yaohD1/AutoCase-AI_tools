"""真浏览器验证：上传塞文件、下载捕获并校验。

单测只能证明「逻辑对」，证明不了「Playwright 真能把文件塞进那个看不见的 input」，
也证明不了 blob / Content-Disposition 触发的下载能被 expect_download 接住。
这两件事一旦不成立，整套东西就是空的，所以单独跑一遍。

不访问外网：所有响应由 context.route 直接喂。
用法：../.venv/Scripts/python.exe shots/_check_files_e2e.py
"""
import base64
import io
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.services.recording import assets, ir, replay  # noqa: E402


def make_xlsx(rows):
    buf = io.BytesIO()
    cells = ''.join(
        '<row r="%d"><c r="A%d"><v>%d</v></c></row>' % (i + 1, i + 1, i) for i in range(rows)
    )
    with zipfile.ZipFile(buf, 'w') as bundle:
        bundle.writestr('xl/worksheets/sheet1.xml',
                        '<?xml version="1.0"?><worksheet><sheetData>%s</sheetData></worksheet>' % cells)
    return buf.getvalue()


def candidate(selector):
    spec = {'kind': 'css', 'desc': 'css=%s' % selector,
            'method': 'locator', 'args': ['css=%s' % selector]}
    return {'kind': 'css', 'desc': 'css=%s' % selector, 'count': 1, 'index': None, 'spec': spec}


def target(selector, alias):
    return {'alias': alias, 'snap': {}, 'candidates': [candidate(selector)],
            'primary': candidate(selector), 'status': 'ok'}


PAGE = """<!doctype html><html><body>
<div class="up">
  <button type="button" onclick="document.getElementById('f').click()">选择文件</button>
  <input id="f" type="file" style="display:none"
         onchange="document.getElementById('picked').textContent = this.files[0].name">
</div>
<p id="picked">未选择</p>
<a id="dl" href="#" download>导出报表</a>
</body></html>"""

# blob 下载要用的字节从 Python 注入：和真实前端「导出后 createObjectURL」是同一条路径，
# 也不会被 route 拦截干扰（拦截下来的响应不会产生 download 事件）。
BLOB_JS = """(b64) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
  const a = document.getElementById('dl');
  a.href = URL.createObjectURL(blob);
  a.setAttribute('download', 'report.xlsx');
  return a.href.length;
}"""


def main():
    workspace = tempfile.mkdtemp(prefix='ac-e2e-')
    run_dir = tempfile.mkdtemp(prefix='ac-e2e-run-')
    upload_bytes = make_xlsx(5)
    download_bytes = make_xlsx(5)
    saved = assets.save(workspace, '导入模板.xlsx', upload_bytes)
    print('资产：', saved['path'], saved['size'], '字节')

    failures = []

    def check(name, ok, detail=''):
        print(('  ok   ' if ok else '  FAIL ') + name + (('  ← ' + detail) if detail and not ok else ''))
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True, ignore_https_errors=True)

        def handle(route):
            route.fulfill(status=200, body=PAGE, content_type='text/html; charset=utf-8')

        context.route('**/*', handle)
        page = context.new_page()
        page.goto('http://local.test/')
        page.evaluate(BLOB_JS, base64.b64encode(download_bytes).decode())

        # 1. 上传：target 是按钮，真 input 藏在 .up 里且 display:none
        step = ir.make_step('upload', 1, target=target('.up button', '选择文件'), file=saved)
        try:
            chosen, detail = replay._do_upload(step, page, replay.PW_FRAMEWORK, workspace)
            picked = page.inner_text('#picked')
            check('从我到 input：文件真的进了控件', picked == '导入模板.xlsx', picked)
            check('上传返回体积', detail['file']['size'] == len(upload_bytes), str(detail))
        except Exception as exc:  # noqa: BLE001
            check('上传分支', False, repr(exc))

        # 2. 下载：a 标签 + Content-Disposition，文件名以响应头为准
        step = ir.make_step('download', 2, target=target('#dl', '导出报表'),
                            expect={'name': '*.xlsx', 'parse': 'xlsx', 'min_rows': 3})
        try:
            chosen, detail = replay._do_download(step, page, replay.PW_FRAMEWORK, run_dir)
            check('下载分支：文件落盘', detail['file']['ok'], str(detail))
            check('下载分支：行数正确', detail['file']['rows'] == 5, str(detail))
            check('下载分支：用了 a[download] 给的文件名', detail['file']['name'] == 'report.xlsx',
                  detail['file']['name'])
            check('下载分支：文件真的在磁盘上',
                  (Path(run_dir) / 'downloads' / 'report.xlsx').is_file())
        except Exception as exc:  # noqa: BLE001
            check('下载分支', False, repr(exc))

        # 3. 空表要被抓住：同样的下载，要求 10 行
        step = ir.make_step('download', 3, target=target('#dl', '导出报表'),
                            expect={'parse': 'xlsx', 'min_rows': 10})
        try:
            replay._do_download(step, page, replay.PW_FRAMEWORK, run_dir)
            check('空表/行数不足应当失败', False, '没有抛错')
        except replay.ReplayError as exc:
            check('空表/行数不足应当失败', '行' in str(exc), str(exc))

        # 4. 同名下载不覆盖：前一步已经落了一份 report.xlsx，这里必须换名
        step = ir.make_step('download', 4, target=target('#dl', '导出报表'),
                            expect={'name': '*.xlsx'})
        try:
            detail = replay._do_download(step, page, replay.PW_FRAMEWORK, run_dir)[1]
            name = detail['file']['name']
            check('同名不覆盖', name != 'report.xlsx' and name.startswith('report-'), name)
        except Exception as exc:  # noqa: BLE001
            check('同名不覆盖', False, repr(exc))

        browser.close()

    print()
    if failures:
        print('失败 %d 项：%s' % (len(failures), '、'.join(failures)))
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
