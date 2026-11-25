"""生成到目标项目里的滑块库源码（Robot Framework + SeleniumLibrary）。

对应 Playwright 侧的 `utils/slider.ts`，算法完全一致：
找容器 → 藏拼图块 → 截图 → 放大 → 问视觉模型 → 试探标定比例 → 拖动 → 轮询结果 → 失败重开。

两处必须不同，原因都写在代码里：
1. 没有 Pillow —— 放大改在浏览器里用 canvas 做，`toDataURL` 拿 base64；
2. 没有 requests —— 用标准库 `urllib.request`。
"""

SLIDER_PY = r'''"""滑块验证（Robot Framework + SeleniumLibrary）。

由 AutoCase 生成。平台重新生成时会覆盖，不要手改。

只对**前端校验**的拼图组件有效（vue-puzzle-vcode 及同类）：拖对了组件自己放行。
带服务端风控的（极验、腾讯验证码那类）不保证 —— 它们还会看轨迹和设备指纹。

一次求解的顺序和理由：
找容器 → 藏拼图块 → 放大截图 → 问视觉模型 → 试探标定比例 → 分步拖动 → 轮询结果。
失败重试不是「再拖一次」：组件复位后会重新出题，缺口位置变了，
所以是「重找容器 → 重新截图 → 重新问模型」。
"""

import base64
import json
import logging
import os
import random
import re
import time
import urllib.request

from robot.libraries.BuiltIn import BuiltIn
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)

CONTAINER_SELECTORS = (
    '.vue-puzzle-vcode',
    '[class*="puzzle-vcode"]',
    '[class*="slide-verify"]',
    '[class*="verify-slider"]',
    '[class*="captcha"]',
)

HANDLE_SELECTORS = (
    '.range-btn',
    '[class*="range-btn"]',
    '[class*="slider-btn"]',
    '[class*="drag-btn"]',
    '[class*="verify-btn"]',
)

# 只收「明确表示没通过」的句子。「拖动滑块完成验证」是常驻的初始提示，收进来会每轮都判失败。
FAIL_WORDS = ('验证失败', '验证不通过')

SCALE = 2        # 发给模型前放大倍数
PROBE_PX = 24    # 标定位移比例时先走的鼠标距离
MAX_SIDE = 640   # 容器宽超过这个值多半不是验证码


class SliderError(Exception):
    """求解失败，message 直接给用户看。"""


def _short(value, width=80):
    text = str(value or '').replace('\n', ' ')
    return text if len(text) <= width else text[:width - 1] + '…'


def _driver():
    lib = BuiltIn().get_library_instance('SeleniumLibrary')
    driver = getattr(lib, 'driver', None)
    if driver is None:
        driver = lib._current_browser()
    return driver


def _box(el):
    """元素在页面里的位置和尺寸。取不到就返回 None，不要把异常抛给调用方。"""
    try:
        rect = el.rect or {}
        width = rect.get('width') or 0
        height = rect.get('height') or 0
        if not width or not height:
            return None
        return {'x': rect.get('x') or 0, 'y': rect.get('y') or 0,
                'width': width, 'height': height}
    except Exception:  # noqa: BLE001 - 元素在遍历中消失是常事
        return None


def _plausible(box):
    if not box:
        return False
    return (200 <= box['width'] <= MAX_SIDE and 110 <= box['height'] <= 300
            and box['width'] > box['height'])


def _elements(driver, css):
    try:
        return driver.find_elements(By.CSS_SELECTOR, css)
    except Exception:  # noqa: BLE001 - 非法选择器由上层翻译成可读错误
        return []


def _canvas_boxes(driver, root=None):
    """候选画布。root 是拾取到的容器或画布，None 表示整页。

    容器本身不能进候选：它和背景画布往往一样大，按面积排序会排在前面被当成画布，
    而 div 没有 width/height 属性，后面取尺寸就是 None。
    """
    els = []
    if root is not None:
        try:
            els = driver.execute_script(
                'const el = arguments[0];'
                'const inner = Array.prototype.slice.call(el.querySelectorAll("canvas"));'
                'return el.tagName === "CANVAS" ? [el].concat(inner) : inner;',
                root) or []
        except Exception:  # noqa: BLE001
            els = []
    else:
        els = _elements(driver, 'canvas')
    out = []
    for el in els:
        box = _box(el)
        if box:
            out.append({'el': el, 'box': box})
    return out


def _find_handle(driver, bg_box):
    """拖动手柄：优先按类名找，再按「贴着容器底部、90 见方以内」的几何找。

    贴的是**底部**不是顶部：实测 bg 在 y=245 高 160，手柄在 y=425，即容器下方 20px。
    """
    bottom = bg_box['y'] + bg_box['height']
    for sel in HANDLE_SELECTORS:
        for el in _elements(driver, sel):
            box = _box(el)
            if not box:
                continue
            if not (30 <= box['width'] <= 90 and 30 <= box['height'] <= 90):
                continue
            if abs(box['x'] - bg_box['x']) < 60 and 0 <= box['y'] - bottom < 90:
                return {'el': el, 'box': box, 'selector': sel}
    return None


def _find_piece(canvases, bg_box):
    """拼图块：宽不到 100、高度够，初始位置贴背景图左上角。"""
    for item in canvases:
        box = item['box']
        if box['width'] >= 100 or box['height'] < 60:
            continue
        if abs(box['x'] - bg_box['x']) < 40 and abs(box['y'] - bg_box['y']) < 24:
            return item
    return None


def find_scene(driver, root=None):
    """找一组「背景 + 拼图块 + 手柄」。找不到返回 None。"""
    canvases = _canvas_boxes(driver, root)
    if not canvases:
        return None
    candidates = [c for c in canvases if _plausible(c['box'])]
    # 容器本身可能也落在尺寸区间里，但它配不上手柄（手柄在它内部、贴的是底边外侧），
    # 所以按面积从大到小试，配不上就自然落到内部真正的画布上。
    for bg in sorted(candidates, key=lambda c: -(c['box']['width'] * c['box']['height'])):
        handle = _find_handle(driver, bg['box'])
        if handle is None:
            continue
        return {'bg': bg, 'piece': _find_piece(canvases, bg['box']), 'handle': handle}
    return None


def _scene_by_selector(driver, selector):
    """按录制的选择器定位，但要回到它的容器里找，不能只在它内部找。

    录制给的可能是容器，也可能直接是背景画布。后者如果拿它当 root，
    querySelectorAll('canvas') 只能找到它自己，拼图块（兄弟节点）就丢了，
    截图里块没被隐藏，模型会把块本身当成缺口。
    """
    for el in _elements(driver, selector):
        root = el
        try:
            root = driver.execute_script(
                'return arguments[0].closest("div") || arguments[0].parentElement',
                el) or el
        except Exception:  # noqa: BLE001
            pass
        scene = find_scene(driver, root=root)
        if scene:
            return scene
    return None


def _locate(driver, selector):
    """先按选择器找，找不到再全页自动识别（选择器失效不该让整步直接失败）。"""
    scene = _scene_by_selector(driver, selector) if selector else None
    return scene if scene is not None else find_scene(driver)


def _set_piece_visible(driver, piece, visible):
    if not piece:
        return
    try:
        driver.execute_script('arguments[0].style.visibility = arguments[1];',
                              piece['el'], '' if visible else 'hidden')
    except Exception:  # noqa: BLE001 - 藏不住就继续，最多是识别精度下降
        logger.debug('切换拼图块可见性失败', exc_info=True)


ENLARGE_JS = """
const el = arguments[0], scale = arguments[1];
const w = el.width, h = el.height;
if (!w || !h) { return null; }
const c = document.createElement('canvas');
c.width = w * scale;
c.height = h * scale;
const ctx = c.getContext('2d');
ctx.imageSmoothingEnabled = true;
ctx.imageSmoothingQuality = 'high';
ctx.drawImage(el, 0, 0, c.width, c.height);
try { return c.toDataURL('image/png'); } catch (e) { return null; }
"""


def _capture(driver, scene):
    """截一张「只有缺口、没有拼图块」的图，返回 (base64, 实际放大倍数)。

    拼图块和缺口形状完全一样、叠在同一片区域。不藏起来模型会把块本身当成缺口，
    这是实测踩过最多的一个坑。放大在浏览器里做，避免依赖 Pillow。
    """
    el = scene['bg']['el']
    _set_piece_visible(driver, scene['piece'], False)
    try:
        data = driver.execute_script(ENLARGE_JS, el, SCALE)
    finally:
        # 必须恢复：拖动靠组件自己移动那块 canvas，藏着的状态下拖了也看不见。
        _set_piece_visible(driver, scene['piece'], True)
    if data and ',' in data:
        return data.split(',', 1)[1], SCALE
    # canvas 被跨域图污染时 toDataURL 会抛错，退回元素截图（不放大）。
    png = el.screenshot_as_png
    return base64.b64encode(png).decode(), 1


def _prompt(width, height, scale):
    return ('这是滑块拼图验证码的区域截图（已放大 %d 倍，实际 %dx%d）。'
            '图里只有一个白色缺口，拼图块已被隐藏。请给出缺口左边缘的 x 坐标，'
            '坐标系按这张 %dx%d 的图算。只输出 JSON：{"x": 整数}'
            % (scale, width, height, width * scale, height * scale))


def _parse_x(text):
    match = re.search(r'\{[^{}]*"x"\s*:\s*(-?\d+)[^{}]*\}', text or '')
    if match:
        return int(match.group(1))
    match = re.search(r'"x"\s*:\s*(-?\d+)', text or '')
    return int(match.group(1)) if match else None


def _read_env():
    """项目根 .env 里的 SLIDER_MODEL_*（platform 生成时写入）。

    不能写死往上几层：库放在 `<用例目录>/libraries/` 下，用例目录本身的深度不定。
    一直往上找到磁盘根为止，谁先有 .env 就用谁。
    """
    out = {}
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        path = os.path.join(current, '.env')
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as handle:
                    for line in handle:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        key, value = line.split('=', 1)
                        out.setdefault(key.strip(), value.strip().strip('"\''))
            except OSError:
                pass
            if out:
                return out
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return out


def _model_config():
    env = _read_env()

    def pick(name):
        return (os.environ.get(name) or env.get(name) or '').strip()

    return {
        'base': (pick('SLIDER_MODEL_BASE_URL') or pick('CAPTCHA_MODEL_BASE_URL')).rstrip('/'),
        'name': pick('SLIDER_MODEL_NAME') or pick('CAPTCHA_MODEL_NAME'),
        'key': pick('SLIDER_MODEL_API_KEY') or pick('CAPTCHA_MODEL_API_KEY'),
    }


def ask_model(b64, width, height, scale):
    """把截图发给视觉模型，要缺口左边缘的 x（原图坐标系）。返回 (x, 说明)。"""
    conf = _model_config()
    if not conf['base']:
        raise SliderError('没有配置滑块模型：项目根 .env 里缺 SLIDER_MODEL_BASE_URL。')
    body = {
        'model': conf['name'],
        'max_tokens': 1200,
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': _prompt(width, height, scale)},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
        ]}],
    }
    request = urllib.request.Request(
        conf['base'] + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + conf['key'],
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode('utf-8')
    except Exception as exc:  # noqa: BLE001 - 网络错误统一翻成可读提示
        raise SliderError('调用滑块模型失败：%s' % _short(exc)) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SliderError('滑块模型返回的不是 JSON：%s' % _short(raw, 120)) from exc
    message = (payload.get('choices') or [{}])[0].get('message') or {}
    # content 是答案，reasoning_content 是思考过程；两个都找，模型偶尔只写在思考里。
    for field in ('content', 'reasoning_content'):
        value = _parse_x(message.get(field))
        if value is not None:
            x = value / float(scale)
            if x < 0 or x > width:
                raise SliderError('模型给的坐标 %s 超出图片宽度 %s，这次识别不可用。' % (x, width))
            return x, '%s · %s' % (conf['name'], field)
    raise SliderError('模型没有返回可解析的坐标：%s'
                      % _short(message.get('content') or '', 120))


def _read_gap(driver, scene):
    """截图 + 问模型，返回 (缺口 x, 说明, 原图宽)。重试也要走一遍。"""
    b64, scale = _capture(driver, scene)
    try:
        width, height = driver.execute_script(
            'return [arguments[0].width, arguments[0].height]', scene['bg']['el'])
    except Exception as exc:  # noqa: BLE001
        raise SliderError('读不到画布尺寸：%s' % _short(exc)) from exc
    if not width or not height:
        raise SliderError('定位到的不是背景画布（取不到 width/height），换一个 selector 重试。')
    x, note = ask_model(b64, int(width), int(height), scale)
    return x, note, int(width)


def _nudge(x, width):
    """识别不出来时的兜底：在上次坐标附近挪几像素。"""
    return min(max(x + random.choice((-2, 0, 2)), 1), max(width - 1, 1))


def _body_text(driver):
    try:
        return driver.execute_script(
            'return document.body ? document.body.innerText : ""') or ''
    except Exception:  # noqa: BLE001 - 跳转中读不到 body
        return ''


def _wait_reset(driver, timeout=4.0):
    """等上一次的失败提示消失。提示还挂着时会把这次的结果误判成失败。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(word in _body_text(driver) for word in FAIL_WORDS):
            return
        time.sleep(0.2)


def _piece_x(piece):
    if not piece:
        return None
    box = _box(piece['el'])
    return box['x'] if box else None


def _drag(driver, scene, piece_offset, target):
    """拖到目标位置。返回 (鼠标总位移, 实测比例)。

    比例不能写死：CSS 缩放、组件实现都会让它偏离 1:1。
    先走一小段量出「鼠标走 N，拼图块走多少」，再按比例算剩下的距离。
    """
    handle = scene['handle']['el']
    piece_start = _piece_x(scene['piece'])
    try:
        ActionChains(driver).move_to_element(handle).click_and_hold(handle).perform()
    except Exception as exc:  # noqa: BLE001
        raise SliderError('按住滑块失败：%s' % _short(exc)) from exc
    time.sleep(random.uniform(0.1, 0.16))

    ActionChains(driver).move_by_offset(PROBE_PX, 0).perform()
    time.sleep(0.04)
    moved = None
    if piece_start is not None:
        now = _piece_x(scene['piece'])
        moved = (now - piece_start) if now is not None else None
    ratio = 1.0
    if moved and moved > 4:
        ratio = min(max(moved / float(PROBE_PX), 0.5), 2.0)

    total = max((target - piece_offset) / ratio, PROBE_PX + 4)
    remain = total - PROBE_PX
    steps = 12
    for _ in range(steps):
        ActionChains(driver).move_by_offset(remain / float(steps), 0).perform()
        time.sleep(random.uniform(0.008, 0.016))
    ActionChains(driver).release().perform()
    return total, ratio


def _alive(driver, el):
    try:
        return bool(driver.execute_script('return arguments[0].isConnected', el))
    except Exception:  # noqa: BLE001 - 上下文销毁说明页面已经跳走
        return False


def _wait_result(driver, bg_el, timeout):
    """等组件给结论：消失=通过，出现失败字样=失败，到点还挂着=超时。

    不能用固定 sleep：校验是异步的，松手后要过一两秒才有结果。
    """
    started = time.time()
    while time.time() - started < timeout:
        if not _alive(driver, bg_el):
            return 'pass', round(time.time() - started, 1)
        if any(word in _body_text(driver) for word in FAIL_WORDS):
            return 'fail', round(time.time() - started, 1)
        time.sleep(0.25)
    return 'timeout', round(time.time() - started, 1)


class SliderLibrary(object):
    ROBOT_LIBRARY_SCOPE = 'GLOBAL'
    ROBOT_LIBRARY_VERSION = '1.0'

    def solve_slider(self, selector=None, attempts=2, timeout=40):
        """在当前页面过滑块。通过则返回结果字典，不通过抛 AssertionError。

        selector  录制的容器选择器，空则自动识别
        attempts  失败后最多重试几次（默认 2，即最多拖 3 次）
        timeout   单次等结果的秒数
        """
        driver = _driver()
        attempts = max(0, min(int(attempts or 2), 5))
        timeout = max(5, min(int(timeout or 40), 90))
        selector = str(selector or '').strip()

        started = time.perf_counter()
        scene = _locate(driver, selector)
        if scene is None:
            raise AssertionError('当前页面上没有找到滑块（没找到符合尺寸的 canvas 和拖动手柄）。')

        ai_x, model_note, width = _read_gap(driver, scene)
        reads = [{'attempt': 1, 'x': round(ai_x, 1), 'via': 'model'}]
        last = {'verdict': 'timeout', 'wait': 0, 'ratio': 1.0, 'distance': 0, 'attempt': 0}

        for attempt in range(1, attempts + 2):
            if attempt > 1:
                time.sleep(1.2)
                _wait_reset(driver)
                fresh = _locate(driver, selector)
                # 重绘有一两百毫秒空窗：失败提示没了，但新画布还没插回来。
                for _ in range(4):
                    if fresh is not None:
                        break
                    time.sleep(0.3)
                    fresh = _locate(driver, selector)
                if fresh is None:
                    raise AssertionError(
                        '滑块没有通过，重试时组件已经从页面上消失了（可能被关掉或页面跳走）。'
                        '上一次识别的缺口坐标是 %s。' % round(ai_x))
                scene = fresh
                try:
                    ai_x, model_note, width = _read_gap(driver, scene)
                    reads.append({'attempt': attempt, 'x': round(ai_x, 1), 'via': 'model'})
                except SliderError as exc:
                    ai_x = _nudge(ai_x, width)
                    reads.append({'attempt': attempt, 'x': round(ai_x, 1),
                                  'via': 'fallback', 'why': _short(exc, 60)})

            bg_box = scene['bg']['box']
            piece_offset = (scene['piece']['box']['x'] - bg_box['x']) if scene['piece'] else 0
            distance, ratio = _drag(driver, scene, piece_offset, ai_x)
            verdict, wait = _wait_result(driver, scene['bg']['el'], timeout)
            last = {'verdict': verdict, 'wait': wait, 'ratio': round(ratio, 4),
                    'distance': round(distance), 'attempt': attempt}
            if verdict == 'pass':
                break

        result = dict(last)
        result.update({
            'ok': last['verdict'] == 'pass',
            'ai_x': round(ai_x, 1),
            'model': model_note,
            'elapsed': round(time.perf_counter() - started, 1),
            'reads': reads,
        })
        if not result['ok']:
            reason = {
                'fail': '拖到了位置但组件判定不对（缺口坐标 %s，实测比例 %s）。'
                        % (result['ai_x'], result['ratio']),
                'timeout': '拖完 %s 秒仍没有结果，可能组件没接收这次拖动。' % timeout,
            }.get(last['verdict'], '滑块没有通过。')
            raise AssertionError('滑块未通过：%s' % reason)
        return result
'''
