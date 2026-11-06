"""确认 el-upload 的 fileList 带 size，第 1 步的「N 张 · X MB」能取到真实值。

只上传、不调模型。

    UI_URL=http://localhost:5173/ .venv/Scripts/python.exe shots/_check_progress_size.py
"""
import os

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get('UI_URL', 'http://localhost:5173/')
IMAGE = os.path.join(ROOT, 'shots', '_case_src_login.png')

READ = """() => {
  const el = document.querySelector('.upload-head') || document.querySelector('#app');
  let inst = el && el.__vueParentComponent;
  while (inst) {
    if (inst.setupState && 'fileList' in inst.setupState) {
      const list = inst.setupState.fileList || [];
      return list.map(f => ({ name: f.name, size: f.size, rawSize: f.raw && f.raw.size }));
    }
    inst = inst.parent;
  }
  return null;
}"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.goto(URL, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)

        page.locator('input[type="file"]').first.set_input_files(IMAGE)
        page.wait_for_timeout(1500)

        files = page.evaluate(READ)
        print('fileList：%s' % files)
        print('磁盘实际大小：%d 字节' % os.path.getsize(IMAGE))

        # 带上真实文件切到分析态，看第 1 步的完成信息
        page.evaluate("""() => {
          const el = document.querySelector('.upload-head') || document.querySelector('#app');
          let inst = el && el.__vueParentComponent;
          while (inst) {
            if (inst.setupState && 'generateStep' in inst.setupState) {
              inst.setupState.generateStep = 'analyzing';
              return;
            }
            inst = inst.parent;
          }
        }""")
        page.wait_for_timeout(6000)
        notes = page.locator('.pv-note').all_inner_texts()
        print('步骤说明：%s' % notes)
        page.screenshot(path=os.path.join(ROOT, 'shots', '_pv_9_real_note.png'))

        browser.close()


if __name__ == '__main__':
    main()
