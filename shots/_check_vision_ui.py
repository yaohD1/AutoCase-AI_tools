"""模型服务页的「多模态」开关、首页下拉的置灰提示。

用法：.venv/Scripts/python.exe shots/_check_vision_ui.py
需要前端 dev server 在 localhost:5173、后端在 5000。
"""
from playwright.sync_api import sync_playwright

SHOTS = 'D:/autoproject/Auto_case/shots'


def text_of(locator):
    """把元素文字压成一行：开关里是「多模态 + 开/关」两个 span，会带换行。"""
    return ' '.join(locator.inner_text().split())


def main():
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('console', lambda m: errors.append(m.text[:160]) if m.type == 'error' else None)
        calls = []
        page.on('request', lambda r: calls.append(r.url) if '/api/models' in r.url else None)

        page.goto('http://localhost:5173/settings', wait_until='networkidle')
        page.wait_for_timeout(1200)
        page.get_by_text('模型服务', exact=True).first.click()
        page.wait_for_timeout(900)

        chips = page.locator('.mchip')
        switches = page.locator('.mchip-mm')
        report.append('模型 %d 个，多模态开关 %d 个' % (chips.count(), switches.count()))
        assert chips.count() > 0
        assert switches.count() == chips.count(), '每个模型都要有一个多模态开关'

        labels = [text_of(switches.nth(i)) for i in range(min(4, switches.count()))]
        report.append('前几个开关：%s' % labels)
        assert all('多模态' in label and ('开' in label or '关' in label) for label in labels), labels

        # 说明文字必须在，用户不用猜「多模态」是什么意思
        note = ' '.join(page.locator('.panel-note').inner_text().split())
        report.append('说明文字：%s' % note[:60])
        assert '多模态' in note and '读图' in note

        before = text_of(switches.first)
        switches.first.click()
        page.wait_for_timeout(2600)
        after = text_of(page.locator('.mchip-mm').first)
        report.append('切换：%s → %s' % (before, after))
        assert before != after, '点了开关状态要变'
        assert any('/api/models/providers/' in u for u in calls), '应发出 PATCH'
        assert any(u.endswith('/models/apply') for u in calls), '改完要自动 apply'

        page.locator('.mchip-mm').first.click()
        page.wait_for_timeout(2600)
        back = text_of(page.locator('.mchip-mm').first)
        report.append('已还原：%s' % back)
        assert back == before, '%s != %s' % (back, before)

        page.screenshot(path=SHOTS + '/_set_7_vision.png')

        page.goto('http://localhost:5173/', wait_until='networkidle')
        page.wait_for_timeout(1200)
        page.get_by_text('选择分析模型', exact=True).click()
        page.wait_for_timeout(700)
        options = page.locator('.el-select-dropdown__item:visible')
        labels = options.evaluate_all('els => els.map(e => e.innerText.trim())')
        disabled = options.evaluate_all('els => els.map(e => e.classList.contains("is-disabled"))')
        report.append('分析模型 %d 个，置灰 %d 个' % (len(labels), sum(disabled)))
        report.append('示例：%s' % labels[:2])
        assert any('未开启多模态' in x for x in labels), '没开多模态的模型要标注出来'
        page.screenshot(path=SHOTS + '/_set_8_upload_models.png')

        report.append('console error: %s' % (errors[:2] if errors else 'none'))
        assert not errors, errors
        browser.close()
    print('\n'.join(report))


if __name__ == '__main__':
    main()
