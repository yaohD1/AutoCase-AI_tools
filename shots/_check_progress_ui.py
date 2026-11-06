"""进度弹窗视觉检查：直接切 generateStep 截三个阶段，不调模型、不写库。

    UI_URL=http://localhost:5173/ .venv/Scripts/python.exe shots/_check_progress_ui.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get('UI_URL', 'http://localhost:5173/')

PROBE = """() => {
  const el = document.querySelector('.upload-head') || document.querySelector('#app');
  let inst = el && el.__vueParentComponent;
  const found = [];
  while (inst) {
    const keys = inst.setupState ? Object.keys(inst.setupState) : [];
    if (keys.length) found.push({ name: (inst.type && (inst.type.__name || inst.type.name)) || '?', has: keys.includes('generateStep'), keys: keys.slice(0, 8) });
    inst = inst.parent;
  }
  return found;
}"""

SET_STEP = """(step) => {
  const el = document.querySelector('.upload-head') || document.querySelector('#app');
  let inst = el && el.__vueParentComponent;
  while (inst) {
    if (inst.setupState && 'generateStep' in inst.setupState) {
      inst.setupState.generateStep = step;
      if ('modules' in inst.setupState) {
        inst.setupState.modules = [{ module: '账号登录' }, { module: '第三方登录' }];
      }
      return true;
    }
    inst = inst.parent;
  }
  return false;
}"""


def snap(page, name):
    page.wait_for_timeout(400)
    path = os.path.join(ROOT, 'shots', '_pv_%s.png' % name)
    page.screenshot(path=path)
    print('  截图 %s' % path)
    return path


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(URL, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)

        probe = page.evaluate(PROBE)
        print('组件链：')
        for item in probe:
            print('  %-18s generateStep=%s  keys=%s' % (item['name'], item['has'], item['keys']))

        if not any(item['has'] for item in probe):
            print('没找到持有 generateStep 的组件，放弃')
            browser.close()
            sys.exit(1)

        for step, label, wait in (('uploading', '1_uploading', 600),
                                  ('analyzing', '2_analyzing', 600),
                                  ('generating', '3_generating', 600)):
            ok = page.evaluate(SET_STEP, step)
            print('切到 %s：%s' % (step, ok))
            snap(page, label)

        # 让分析阶段跑一段时间，看进度条缓行的位置和计时
        page.evaluate(SET_STEP, 'analyzing')
        page.wait_for_timeout(12000)
        snap(page, '4_analyzing_12s')

        # 生成阶段跑到后段，看进度条逼近 90% 但停住
        page.evaluate(SET_STEP, 'generating')
        page.wait_for_timeout(50000)
        snap(page, '6_generating_50s')

        # 暗色主题
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'dark'); document.documentElement.classList.add('dark'); }")
        page.evaluate(SET_STEP, 'analyzing')
        page.wait_for_timeout(9000)
        snap(page, '8_dark')

        # 窄屏：420px 弹窗在 900 宽下会不会溢出
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); document.documentElement.classList.remove('dark'); }")
        page.set_viewport_size({'width': 900, 'height': 780})
        page.wait_for_timeout(600)
        snap(page, '7_narrow')

        browser.close()

    print('console 错误：%s' % (errors or '无'))


if __name__ == '__main__':
    main()
