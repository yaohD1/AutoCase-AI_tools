# -*- coding: utf-8 -*-
"""AI 定位实验 · 第 1 步：在真实页面上标记目标元素并采集上下文。

目标取自历史录制里真实踩过坑的步骤：
  T1 发送按钮   —— 产物里是 div > ... > svg > rect:nth-of-type(2)
  T2 聊天输入框 —— 产物里是 getByRole('textbox').nth(1)
  T3 「问AI」导航 —— 产物里是 getByRole('link', { name: '问AI' })
  T4 侧边栏用户区 —— 产物里是 getByRole('button', { name: 'dyh\\n管理员\\ntest998' })
  T5 搜索框     —— 产物里没有，作为容易的一条对照

对每个目标导出：
  - snap：元素自身属性 + 父链
  - dom：目标向上 N 层的 outerHTML（裁剪）
  - 全页可交互元素清单
"""
import json

from playwright.sync_api import sync_playwright

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'
OUT = 'D:/autoproject/Auto_case/.tmp-ai-locate'

# 用「人在页面上会怎么描述」来定位目标，不依赖产物里的选择器（否则等于抄答案）
TARGETS = [
    {'id': 'T1', 'desc': '聊天输入区右下角的发送按钮（圆形/图标按钮，点了会把输入框内容发出去）'},
    {'id': 'T2', 'desc': '聊天输入框（多行文本域，placeholder 是「输入内容并发送（或按 Enter），即可生成回答」）'},
    {'id': 'T3', 'desc': '左侧导航栏里的「问AI」菜单项'},
    {'id': 'T4', 'desc': '左下角/左上角显示当前用户「test998」的入口'},
    {'id': 'T5', 'desc': '左侧导航栏顶部的搜索输入框（placeholder 是「搜索」）'},
]

MARK_JS = """(args) => {
  const { tid, desc } = args;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width >= 8 && r.height >= 8 && r.y > -200 && r.y < 20000;
  };
  const cands = [...document.querySelectorAll('*')].filter(visible);
  let hit = null;
  if (tid === 'T1') {
    // 圆形图标按钮：宽度接近高度、在输入区附近、内部有 svg
    hit = cands.filter((el) => {
      const r = el.getBoundingClientRect();
      return el.querySelector('svg') && Math.abs(r.width - r.height) < 6
        && r.width >= 16 && r.width <= 40 && r.y > 400 && r.x > 600;
    }).sort((a, b) => b.getBoundingClientRect().x - a.getBoundingClientRect().x)[0];
  } else if (tid === 'T2') {
    hit = [...document.querySelectorAll('textarea')].find(
      (el) => (el.getAttribute('placeholder') || '').includes('输入内容并发送'));
  } else if (tid === 'T3') {
    hit = [...document.querySelectorAll('a')].find((el) => norm(el.innerText) === '问AI');
  } else if (tid === 'T4') {
    hit = [...document.querySelectorAll('a')].find((el) => norm(el.innerText) === 'test998');
  } else if (tid === 'T5') {
    hit = [...document.querySelectorAll('input')].find(
      (el) => (el.getAttribute('placeholder') || '') === '搜索');
  }
  if (!hit) return null;
  hit.setAttribute('data-ai-probe', tid);
  return tid;
}"""

SNAP_JS = """(tid) => {
  const el = document.querySelector('[data-ai-probe="' + tid + '"]');
  if (!el) return null;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const describe = (node, depth) => {
    if (!node || depth < 0) return null;
    const r = node.getBoundingClientRect();
    return {
      tag: node.tagName.toLowerCase(),
      cls: node.getAttribute('class') || '',
      id: node.id || '',
      role: node.getAttribute('role') || '',
      aria: node.getAttribute('aria-label') || '',
      text: norm(node.innerText).slice(0, 60),
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      parent: describe(node.parentElement, depth - 1),
    };
  };
  const r = el.getBoundingClientRect();
  return {
    tag: el.tagName.toLowerCase(),
    cls: el.getAttribute('class') || '',
    id: el.id || '',
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    ph: el.getAttribute('placeholder') || '',
    text: norm(el.innerText).slice(0, 60),
    testid: el.getAttribute('data-testid') || '',
    box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    chain: describe(el.parentElement, 3),
  };
}"""

# 目标向上 2 层的 outerHTML，把 svg 里的 path 折掉（它们对定位没意义但极占体积）
DOM_SNIPPET_JS = """(tid) => {
  const el = document.querySelector('[data-ai-probe="' + tid + '"]');
  if (!el) return '';
  let node = el;
  for (let i = 0; i < 2 && node.parentElement; i++) node = node.parentElement;
  const clone = node.cloneNode(true);
  clone.querySelectorAll('path,defs,use,style,script').forEach((n) => n.remove());
  clone.querySelectorAll('[data-ai-probe]').forEach((n) => n.removeAttribute('data-ai-probe'));
  let html = clone.outerHTML;
  if (html.length > 6000) html = html.slice(0, 6000) + '...<!--截断-->';
  return html;
}"""

INVENTORY_JS = """() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]')
    .forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return;
      const item = {
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') || '').slice(0, 50),
        role: el.getAttribute('role') || '',
        text: norm(el.innerText).slice(0, 30),
        box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      };
      const ph = el.getAttribute('placeholder');
      if (ph) item.placeholder = ph;
      const aria = el.getAttribute('aria-label');
      if (aria) item.aria = aria;
      const tid = el.getAttribute('data-testid');
      if (tid) item.testid = tid;
      out.push(item);
    });
  return out;
}"""


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(2500)
        page.get_by_text('账号密码登录').click(timeout=10000)
        page.wait_for_timeout(800)
        page.get_by_label('手机号或邮箱').fill(PHONE)
        page.get_by_label('密码', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='登 录').click()
        page.wait_for_timeout(6000)
        page.get_by_text('dyh', exact=True).first.click(timeout=10000)
        page.wait_for_timeout(7000)
        print('当前 url:', page.url)

        inventory = page.evaluate(INVENTORY_JS)
        print('可交互元素:', len(inventory))

        collected = []
        for target in TARGETS:
            marked = page.evaluate(MARK_JS, {'tid': target['id'], 'desc': target['desc']})
            if not marked:
                print(target['id'], '未找到目标元素 —— 跳过')
                continue
            snap = page.evaluate(SNAP_JS, target['id'])
            snippet = page.evaluate(DOM_SNIPPET_JS, target['id'])
            collected.append({
                'id': target['id'],
                'desc': target['desc'],
                'snap': snap,
                'dom': snippet,
                'dom_len': len(snippet),
            })
            print('%s ok | tag=%s cls=%s text=%r | dom %d 字符' % (
                target['id'], snap['tag'], snap['cls'][:30], snap['text'][:20], len(snippet)))
            page.evaluate("(t) => { const e = document.querySelector('[data-ai-probe=\"' + t + '\"]'); "
                          "if (e) e.removeAttribute('data-ai-probe'); }", target['id'])

        with open(OUT + '/probes.json', 'w', encoding='utf-8') as fh:
            json.dump({'inventory': inventory, 'targets': collected}, fh, ensure_ascii=False, indent=1)

        # 保存打了标记的页面状态，供后续验证用（重新打开即可）
        ctx.storage_state(path=OUT + '/auth.json')
        browser.close()
        print('已保存 probes.json，目标数:', len(collected))


if __name__ == '__main__':
    main()
