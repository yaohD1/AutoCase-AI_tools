# -*- coding: utf-8 -*-
"""AI 定位实验 · 共用部分：登录、标记目标、采集真实快照。

标机沿用录制器的算法（cssPath / labelText / implicitRole），
这样喂给 AI 的信息与产品里录制时真正能拿到的一致，结论才有代表性。
"""
import json

BASE = 'https://ai.test.yygu.cn'
PHONE = '13609266230'
PASSWORD = '123456789'
OUT = 'D:/autoproject/Auto_case/.tmp-ai-locate'

# 目标取自历史录制里真实踩过坑的步骤
TARGETS = [
    {'id': 'T1', 'desc': '聊天输入区右下角的发送按钮（圆形图标按钮，点了会把输入框内容发出去）'},
    {'id': 'T2', 'desc': '聊天输入框（多行文本域，placeholder 是「输入内容并发送（或按 Enter），即可生成回答」）'},
    {'id': 'T3', 'desc': '左侧导航栏里的「问AI」菜单项'},
    {'id': 'T4', 'desc': '左上角显示当前用户「test998」的入口'},
    {'id': 'T5', 'desc': '左侧导航栏顶部的搜索输入框（placeholder 是「搜索」）'},
]

# 与 recorder.js 同源的辅助函数
HELPERS = """
const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
const cssPath = (el) => {
  const parts = [];
  let cur = el;
  while (cur && cur.nodeType === 1 && parts.length < 6) {
    let part = cur.tagName.toLowerCase();
    if (cur === document.documentElement) { parts.unshift(part); break; }
    const sibs = cur.parentElement
      ? Array.from(cur.parentElement.children).filter((c) => c.tagName === cur.tagName) : [];
    if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
    parts.unshift(part);
    cur = cur.parentElement;
  }
  return parts.join(' > ');
};
const labelText = (el) => {
  if (el.id) {
    const lab = document.querySelector('label[for="' + el.id + '"]');
    if (lab) return norm(lab.innerText);
  }
  const wrap = el.closest('label');
  return wrap ? norm(wrap.innerText) : null;
};
const implicitRole = (el) => {
  const t = el.tagName.toLowerCase();
  if (t === 'a') return el.hasAttribute('href') ? 'link' : null;
  if (t === 'button') return 'button';
  if (t === 'textarea') return 'textbox';
  if (t === 'select') return 'combobox';
  if (t === 'input') {
    const ty = (el.getAttribute('type') || 'text').toLowerCase();
    if (ty === 'checkbox') return 'checkbox';
    if (ty === 'radio') return 'radio';
    if (['submit', 'button', 'reset'].includes(ty)) return 'button';
    return 'textbox';
  }
  return null;
};
const snapshot = (el) => {
  const text = el.tagName === 'SELECT' ? null : (norm(el.innerText).slice(0, 80) || null);
  const label = labelText(el);
  const noTextName = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
  const name = el.getAttribute('aria-label') || label || (noTextName ? null : text);
  return {
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    role: el.getAttribute('role') || implicitRole(el),
    role_attr: el.getAttribute('role'),
    name: name ? name.slice(0, 80) : null,
    label: label,
    aria_label: el.getAttribute('aria-label'),
    placeholder: el.getAttribute('placeholder'),
    testid: el.getAttribute('data-testid'),
    text: text,
    id: el.id || null,
    attr_name: el.getAttribute('name'),
    cls: el.getAttribute('class') || '',
    css: cssPath(el),
  };
};
"""

MARK_JS = HELPERS + """
(args) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width >= 8 && r.height >= 8;
  };
  const all = [...document.querySelectorAll('*')].filter(visible);
  const tid = args.tid;
  let hit = null;
  if (tid === 'T1') {
    // 发送按钮：输入区右侧、近似方形、内部或自身是 svg 的图标按钮
    hit = all.filter((el) => {
      const r = el.getBoundingClientRect();
      const isIcon = el.tagName.toLowerCase() === 'svg' || el.querySelector('svg');
      return isIcon && Math.abs(r.width - r.height) <= 4
        && r.width >= 16 && r.width <= 32 && r.x > 900 && r.y > 400;
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
  return snapshot(hit);
}"""

SNAP_JS = HELPERS + """
(tid) => {
  const el = document.querySelector('[data-ai-probe="' + tid + '"]');
  if (!el) return null;
  const snap = snapshot(el);
  const r = el.getBoundingClientRect();
  snap.box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
  // 父链：AI 判断「这是列表里的第几个」需要知道容器结构
  const chain = [];
  let cur = el.parentElement;
  for (let i = 0; i < 3 && cur; i++) {
    chain.push({
      tag: cur.tagName.toLowerCase(),
      cls: (cur.getAttribute('class') || '').slice(0, 60),
      text: norm(cur.innerText).slice(0, 50),
    });
    cur = cur.parentElement;
  }
  snap.chain = chain;
  return snap;
}"""

# 目标向上 2 层的片段，去掉 path/style/script（对定位无意义但极占体积）
DOM_SNIPPET_JS = """
(tid) => {
  const el = document.querySelector('[data-ai-probe="' + tid + '"]');
  if (!el) return '';
  let node = el;
  for (let i = 0; i < 2 && node.parentElement; i++) node = node.parentElement;
  const clone = node.cloneNode(true);
  clone.querySelectorAll('path,defs,use,style,script,svg').forEach((n) => {
    if (!n.querySelector && n.parentNode) n.remove();
  });
  clone.querySelectorAll('[data-ai-probe]').forEach((n) => n.removeAttribute('data-ai-probe'));
  let html = clone.outerHTML;
  if (html.length > 8000) html = html.slice(0, 8000) + '...<!--截断-->';
  return html;
}"""

INVENTORY_JS = HELPERS + """
() => {
  const out = [];
  document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]')
    .forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return;
      const snap = snapshot(el);
      out.push({
        tag: snap.tag, role: snap.role, text: snap.text, name: snap.name,
        placeholder: snap.placeholder, aria: snap.aria_label, testid: snap.testid,
        cls: snap.cls.slice(0, 40), box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      });
    });
  return out;
}"""


def open_main(page):
    """登录 → 选身份，落到主壳。"""
    page.goto(BASE + '/login', wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2500)
    page.get_by_text('账号密码登录').click(timeout=10000)
    page.wait_for_timeout(800)
    page.get_by_label('手机号或邮箱').fill(PHONE)
    page.get_by_label('密码', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='登 录').click()
    page.wait_for_timeout(6000)
    page.get_by_text('dyh', exact=True).first.click(timeout=10000)
    page.wait_for_timeout(9000)
    return page.url


def collect(page):
    """标记全部目标并采集，返回 {id: {...}}。标记会留在页面上，供验证阶段比对。"""
    inventory = page.evaluate(INVENTORY_JS)
    result = {}
    for target in TARGETS:
        snap = page.evaluate(MARK_JS, {'tid': target['id'], 'desc': target['desc']})
        if not snap:
            print('  %s 未找到目标' % target['id'])
            continue
        full = page.evaluate(SNAP_JS, target['id'])
        snippet = page.evaluate(DOM_SNIPPET_JS, target['id'])
        result[target['id']] = {
            'desc': target['desc'],
            'snap': full,
            'dom': snippet,
            'dom_len': len(snippet),
        }
        print('  %s %s | text=%r | css=%s | dom=%d' % (
            target['id'], full['tag'], (full['text'] or '')[:16], full['css'], len(snippet)))
    return {'inventory': inventory, 'targets': result}
