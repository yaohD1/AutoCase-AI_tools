# -*- coding: utf-8 -*-
"""回归：嵌入模式与独立整页在改 page-col 之后仍然能滚、并且不撑破视口。"""
import json
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

SNAP = {'id': 'x', 'status': 'recording', 'base_url': 'http://example.com', 'paused': False,
        'steps': [{'no': i + 1, 'type': 'click', 'text': '操作 %d' % (i + 1),
                   'target': {'primary': {'kind': 'role', 'desc': 'b%d' % i, 'count': 1, 'index': None},
                              'candidates': [{'kind': 'role', 'desc': 'b%d' % i, 'count': 1, 'index': None, 'relation': 'self'}],
                              'matches': ['操作']}} for i in range(14)],
        'picks': [], 'aliases': {}, 'alias_items': {}, 'panel': True, 'browser_open': True,
        'ask': {'status': 'ready'}, 'emit': {'state': 'idle'}}

M = """() => {
  const b = document.querySelector('.steps-card .el-card__body')
  const col = document.querySelector('.page-col')
  return {docScroll: document.documentElement.scrollHeight, innerH: innerHeight,
          colH: col ? Math.round(col.getBoundingClientRect().height) : null,
          client: b ? b.clientHeight : null, scroll: b ? b.scrollHeight : null,
          canScroll: !!b && b.scrollHeight > b.clientHeight + 1}
}"""

with sync_playwright() as p:
    br = p.chromium.launch()

    def mk(w, h):
        pg = br.new_page(viewport={'width': w, 'height': h})
        def h2(route):
            path = urlparse(route.request.url).path
            if path.endswith('/recording/current'):
                route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAP))
            else:
                route.fulfill(status=200, content_type='application/json', body='{}')
        pg.route(lambda u: urlparse(u).path.startswith('/api/'), h2)
        return pg

    for name, url, w, h in (
            ('嵌入模式', 'http://localhost:5173/recording?embed=1&target=http://example.com', 1600, 900),
            ('独立整页', 'http://localhost:5173/recording', 1440, 900)):
        pg = mk(w, h)
        pg.goto(url, wait_until='domcontentloaded')
        pg.wait_for_selector('.steps-card .el-card__body', timeout=15000)
        pg.evaluate("() => document.getElementById('ac-boot')?.remove()")
        pg.wait_for_timeout(400)
        print(name, json.dumps(pg.evaluate(M), ensure_ascii=False))
        pg.screenshot(path='shots/_rec_mode_%s.png' % ('embed' if 'embed' in url else 'full'))
        pg.close()
    br.close()
