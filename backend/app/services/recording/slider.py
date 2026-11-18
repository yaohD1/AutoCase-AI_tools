"""滑块验证码求解：拼图缺口型。

只做一件事：把可拖的拼图块拖到缺口上。不碰账号密码、不碰登录接口，
也不假设站点地址 —— 容器和手柄都是运行时从页面上找出来的。

能力边界（决定了它能用在哪儿）：
- 只对**前端校验**的拼图组件有效（vue-puzzle-vcode 及同类开源组件）。
  拖对了组件自己放行，服务端不二次校验。
- 带服务端风控的（极验、腾讯验证码这类）不保证：它们还会看轨迹、设备指纹。
- 点选文字、算术题、短信验证码都不属于这里。

一次求解的步骤和顺序都是实测出来的，改之前先读注释：
找容器 → 隐藏拼图块 → 截图放大 → 问视觉模型 → 自标定比例拖动 → 轮询结果。
失败重试不是「再拖一次」：组件复位后会重新出题，缺口位置变了，所以是
「重找容器 → 重新截图 → 重新问模型」，只有识别也失败才退回上次坐标。
"""

import base64
import io
import json
import logging
import math
import random
import re
import time

import requests
from PIL import Image

from app.services import models_config

logger = logging.getLogger(__name__)

# 常见拼图组件的容器类名。只用来「先试一遍」，试不到还有基于 canvas 尺寸的兜底，
# 所以这是个捷径列表，不是白名单 —— 新站点加不进来也能用。
CONTAINER_SELECTORS = (
    '.vue-puzzle-vcode',
    '[class*="puzzle-vcode"]',
    '[class*="slide-verify"]',
    '[class*="verify-slider"]',
    '[class*="captcha"]',
)

# 拖动手柄的类名。
HANDLE_SELECTORS = (
    '.range-btn',
    '[class*="range-btn"]',
    '[class*="slider-btn"]',
    '[class*="drag-btn"]',
    '[class*="verify-btn"]',
)

# 失败提示。不同组件措辞不一样，这里只收「明确表示没通过」的句子。
# 特别注意别把「拖动滑块完成验证」这类**初始提示**收进来——它一直在页面上，
# 收进来会让每次松手都立刻判失败。
FAIL_WORDS = ('验证失败', '验证不通过')

SCALE = 2           # 发给模型前放大倍数：小图上视觉精度不够，2 倍是实测够用的档
PROBE_PX = 24       # 标定位移比例时先走的鼠标距离
MAX_SIDE = 640      # 容器宽超过这个值多半不是验证码，直接不认


class SliderError(Exception):
    """求解失败，message 直接给用户看。"""


def _short(value, width=80):
    text = str(value or '').replace('\n', ' ')
    return text if len(text) <= width else text[:width - 1] + '…'


def _plausible(box):
    if not box:
        return False
    return (200 <= box['width'] <= MAX_SIDE and 110 <= box['height'] <= 300
            and box['width'] > box['height'])


def _canvas_boxes(page):
    out = []
    for el in page.query_selector_all('canvas'):
        try:
            box = el.bounding_box()
        except Exception:  # noqa: BLE001 - 元素在遍历中消失是常事
            continue
        if box:
            out.append({'el': el, 'box': box})
    return out


def _find_handle(page, bg_box):
    """拖动手柄：优先按类名找，再按「贴着容器底部、50 见方」的几何找。

    注意是贴着**底部**而不是顶部：实测这个组件 bg 在 y=245 高 160，
    手柄在 y=425，即容器下方 20px。按「距顶部 160 以内」写过一次，全部落空。
    """
    bottom = bg_box['y'] + bg_box['height']
    for sel in HANDLE_SELECTORS:
        for el in page.query_selector_all(sel):
            try:
                box = el.bounding_box()
            except Exception:  # noqa: BLE001
                continue
            if not box:
                continue
            if not (30 <= box['width'] <= 90 and 30 <= box['height'] <= 90):
                continue
            if abs(box['x'] - bg_box['x']) < 60 and 0 <= box['y'] - bottom < 90:
                return {'el': el, 'box': box, 'selector': sel}
    return None


def find_scene(page):
    """在当前页面上找一组「背景 + 拼图块 + 手柄」。

    返回 None 表示这一步现在没有滑块。调用方据此给「页面上没有找到滑块」，
    而不是抛一个看不懂的 Playwright 异常。
    """
    canvases = _canvas_boxes(page)
    if not canvases:
        return None
    big = [c for c in canvases if _plausible(c['box'])]
    if not big:
        return None
    bg = max(big, key=lambda c: c['box']['width'] * c['box']['height'])

    # 拼图块和背景同尺寸关系：宽不到 100、高度接近容器、初始位置贴左上。
    piece = None
    for item in canvases:
        box = item['box']
        if box['width'] >= 100 or box['height'] < 60:
            continue
        if abs(box['x'] - bg['box']['x']) < 40 and abs(box['y'] - bg['box']['y']) < 24:
            piece = item
            break

    handle = _find_handle(page, bg['box'])
    if handle is None:
        return None
    return {'bg': bg, 'piece': piece, 'handle': handle}


def _set_piece_visible(piece, visible):
    if not piece:
        return
    try:
        if visible:
            piece['el'].evaluate("el => { el.style.visibility = ''; }")
        else:
            # 拼图块和缺口形状完全一样、叠在同一片区域。不藏起来，模型会把块本身
            # 当成缺口 —— 这是实测踩过最多的一个坑，六轮失败全栽在这。
            piece['el'].evaluate("el => { el.style.visibility = 'hidden'; }")
    except Exception:  # noqa: BLE001 - 藏不住就继续，最多是识别精度下降
        logger.debug('切换拼图块可见性失败', exc_info=True)


def _enlarge(png):
    img = Image.open(io.BytesIO(png)).convert('RGB')
    width, height = img.size
    img = img.resize((width * SCALE, height * SCALE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), width, height


def _prompt(width, height):
    return ('这是滑块拼图验证码的区域截图（已放大 %d 倍，实际 %dx%d）。'
            '图里只有一个白色缺口，拼图块已被隐藏。请给出缺口左边缘的 x 坐标，'
            '坐标系按这张放大后的 %dx%d 图算。只输出 JSON：{"x": 整数}'
            % (SCALE, width, height, width * SCALE, height * SCALE))


def _parse_x(text):
    match = re.search(r'\{[^{}]*"x"\s*:\s*(-?\d+)[^{}]*\}', text or '')
    if match:
        return int(match.group(1))
    match = re.search(r'"x"\s*:\s*(-?\d+)', text or '')
    return int(match.group(1)) if match else None


def ask_model(png, width, height, ref=None):
    """把截图发给视觉模型，要缺口左边缘的 x（原图坐标系）。

    返回 (x, 说明)。失败抛 SliderError。
    """
    ref = ref or models_config.slider_model()
    if not ref:
        raise SliderError('没有配置滑块模型。请到录制回放页的设置里选一个能看图的模型。')
    resolved = models_config.resolve(ref)
    if resolved is None:
        raise SliderError('滑块模型 %s 不存在，请到设置里重新选择。' % ref)

    b64 = base64.b64encode(png).decode()
    body = {
        'model': resolved.model,
        # 这个模型会先输出 reasoning，给 150 会被截断成空 content（实测）。
        'max_tokens': max(1200, int(resolved.max_tokens or 0)),
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': _prompt(width, height)},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
        ]}],
    }
    url = (resolved.api_base or '').rstrip('/') + '/chat/completions'
    try:
        resp = requests.post(url, headers={'Authorization': 'Bearer ' + (resolved.api_key or ''),
                                           'Content-Type': 'application/json'},
                             json=body, timeout=180)
    except requests.RequestException as exc:
        raise SliderError('调用滑块模型失败：%s' % _short(exc)) from exc
    if resp.status_code != 200:
        raise SliderError('滑块模型返回 %d：%s' % (resp.status_code, _short(resp.text, 160)))

    payload = resp.json()
    message = (payload.get('choices') or [{}])[0].get('message') or {}
    # content 是答案，reasoning_content 是思考过程；两个都找一遍，
    # 因为模型偶尔会把 JSON 只写在思考里（content 空）。
    for field in ('content', 'reasoning_content'):
        value = _parse_x(message.get(field))
        if value is not None:
            x = value / float(SCALE)
            if x < 0 or x > width:
                raise SliderError('模型给的坐标 %s 超出图片宽度 %s，这次识别不可用。' % (x, width))
            return x, '%s · %s' % (ref, field)
    raise SliderError('模型没有返回可解析的坐标：%s' % _short(message.get('content') or '', 120))


def _capture(page, scene):
    """截一张「只有缺口、没有拼图块」的容器图，返回 (png, clip)。

    拼图块和缺口形状完全一样、还叠在同一片区域，不藏起来模型会认错。
    """
    box = scene['bg']['box']
    clip = {'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height']}
    _set_piece_visible(scene['piece'], False)
    try:
        png = page.screenshot(clip=clip)
    finally:
        # 必须恢复：拖动靠组件自己移动那块 canvas，藏着的状态下拖了也看不见。
        _set_piece_visible(scene['piece'], True)
    return png, clip


def _read_gap(page, scene):
    """截图 + 问模型，返回 (缺口 x, 说明, 原图宽, clip)。

    单独抽出来是因为重试也要走一遍：失败后组件会重新出题，缺口位置已经变了。
    """
    png, clip = _capture(page, scene)
    big, width, height = _enlarge(png)
    x, note = ask_model(big, width, height)
    return x, note, width, clip


def _nudge(x, width):
    """识别不出来时的兜底：在上次坐标附近挪几像素。"""
    return min(max(x + random.choice((-2, 0, 2)), 1), max(width - 1, 1))


def _wait_reset(page, timeout=4.0):
    """等上一次的失败提示消失，再去拖。

    提示还挂在页面上时，_wait_result 一读 body 就会把它当成这次的结果，
    明明拖对了也判失败。等不到就走（有些站点文案是常驻的，不该无限等）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.inner_text('body')
        except Exception:  # noqa: BLE001 - 跳转中读不到 body，直接放行
            return
        if not any(word in body for word in FAIL_WORDS):
            return
        page.wait_for_timeout(200)


def _glide(page, from_x, from_y, to_x, to_y, steps=26):
    """带缓动和轻微抖动的移动。直线匀速是机器特征，很多组件会看这个。"""
    for i in range(1, steps + 1):
        p = i / steps
        eased = 1 - math.pow(1 - p, 2.4)
        jitter = math.sin(i * 0.9) * 0.7 if i < steps - 3 else 0
        page.mouse.move(from_x + (to_x - from_x) * eased, from_y + jitter)
        time.sleep(random.uniform(0.008, 0.017))
    page.mouse.move(to_x, to_y)


def _piece_dx(piece, start_x):
    if not piece:
        return None
    try:
        box = piece['el'].bounding_box()
    except Exception:  # noqa: BLE001
        return None
    return (box['x'] - start_x) if box else None


def _drag(page, scene, piece_offset, target):
    """拖到目标位置。返回 (鼠标总位移, 实测比例)。

    比例不能写死：不同站点的 CSS 缩放、组件实现都会让它偏离 1:1。
    先走一小段量出「鼠标走 N，拼图块走多少」，再按真实比例算剩下的距离。
    """
    handle = scene['handle']['box']
    start_x = handle['x'] + handle['width'] / 2
    start_y = handle['y'] + handle['height'] / 2
    piece_start = (scene['piece']['box']['x'] if scene['piece'] else None)

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    time.sleep(random.uniform(0.1, 0.16))

    _glide(page, start_x, start_y, start_x + PROBE_PX, start_y, steps=8)
    moved = _piece_dx(scene['piece'], piece_start) if piece_start is not None else None
    ratio = 1.0
    if moved and moved > 4:
        ratio = moved / float(PROBE_PX)
        ratio = min(max(ratio, 0.5), 2.0)

    total = (target - piece_offset) / ratio
    total = max(total, PROBE_PX + 4)
    _glide(page, start_x + PROBE_PX, start_y, start_x + total, start_y, steps=24)
    time.sleep(random.uniform(0.08, 0.14))
    page.mouse.move(start_x + total, start_y)
    time.sleep(0.05)
    page.mouse.up()
    return total, ratio


def _alive(el):
    try:
        return bool(el.evaluate('el => el.isConnected'))
    except Exception:  # noqa: BLE001 - 上下文销毁说明页面已经跳走
        return False


def _wait_result(page, bg_el, timeout):
    """等组件给结论：消失=通过，出现失败字样=失败，到点还挂着=超时。

    不能用固定 sleep：校验是异步的，松手后要过一两秒才有结果；
    固定等 2.5 秒会把「校验中」当成失败（实测把 4/4 通过误判成 0/4）。
    """
    started = time.time()
    while time.time() - started < timeout:
        if not _alive(bg_el):
            return 'pass', round(time.time() - started, 1)
        try:
            body = page.inner_text('body')
        except Exception:  # noqa: BLE001 - 跳转中读不到 body，下一轮再读
            body = ''
        if any(word in body for word in FAIL_WORDS):
            return 'fail', round(time.time() - started, 1)
        page.wait_for_timeout(250)
    return 'timeout', round(time.time() - started, 1)


def solve(page, config=None, on_tick=None):
    """在当前页面过滑块。返回结果字典，不抛异常（除了真没找到滑块）。

    `config` 来自步骤里的 slider 字段：
      selector  手填的容器选择器，空则自动识别
      attempts  失败后最多重试几次（默认 2，即最多拖 3 次）
      timeout   单次等结果的秒数（默认 20）

    重试会重新出题、重新识别：组件复位后缺口位置变了，拿旧坐标再拖一次没有意义。
    所以 attempts 同时也决定了最多问几次模型（每次 20 秒量级）。

    `on_tick` 在每次真正拖完、等结果前调用一次，供调用方出帧——
    这个动作要 30 秒量级，中间不刷新画面用户会以为卡死。
    """
    config = config or {}
    attempts = max(0, min(int(config.get('attempts') or 2), 5))
    timeout = max(5, min(int(config.get('timeout') or 20), 90))
    selector = str(config.get('selector') or '').strip()

    started = time.perf_counter()
    scene = find_scene(page)
    if scene is None and selector:
        # 手填了选择器：用它框定范围再找一次，避免页面上有多个验证码时认错。
        for el in page.query_selector_all(selector):
            box = el.bounding_box()
            if not box:
                continue
            fake = {'bg': {'el': el, 'box': box}, 'piece': None,
                    'handle': _find_handle(page, box)}
            if fake['handle']:
                scene = fake
                break
    if scene is None:
        raise SliderError('当前页面上没有找到滑块（没找到符合尺寸的 canvas 和拖动手柄）。')

    ai_x, model_note, width, clip = _read_gap(page, scene)
    reads = [{'attempt': 1, 'x': round(ai_x, 1), 'via': 'model'}]

    last = {'verdict': 'timeout', 'wait': 0, 'ratio': 1.0, 'distance': 0, 'attempt': 0}
    for attempt in range(1, attempts + 2):
        if attempt > 1:
            # 失败后组件会重新出题：缺口位置已经变了，照旧坐标再拖一次只是
            # 把同一段轨迹交给它复判，白等一个超时。
            # 所以是「重找容器 → 重新截图 → 重新问模型」，只有识别这一步也失败了
            # 才退回上次坐标（±2px），那种情况至少还有一次机会。
            page.wait_for_timeout(1200)
            _wait_reset(page)
            fresh = find_scene(page)
            if fresh is None:
                raise SliderError(
                    '滑块没有通过，重试时组件已经从页面上消失了（可能被关掉或页面跳走）。'
                    '上一次识别的缺口坐标是 %s。' % round(ai_x))
            scene = fresh
            try:
                ai_x, model_note, width, clip = _read_gap(page, scene)
                reads.append({'attempt': attempt, 'x': round(ai_x, 1), 'via': 'model'})
            except SliderError as exc:
                ai_x = _nudge(ai_x, width)
                reads.append({'attempt': attempt, 'x': round(ai_x, 1),
                              'via': 'fallback', 'why': _short(exc, 60)})
                logger.warning('第 %d 次重试重新识别失败，沿用上次坐标：%s', attempt, exc)
        bg_box = scene['bg']['box']
        clip = {'x': bg_box['x'], 'y': bg_box['y'],
                'width': bg_box['width'], 'height': bg_box['height']}
        piece_offset = (scene['piece']['box']['x'] - bg_box['x']) if scene['piece'] else 0
        distance, ratio = _drag(page, scene, piece_offset, ai_x)
        if on_tick:
            try:
                on_tick()
            except Exception:  # noqa: BLE001 - 出帧失败不该打断求解
                pass
        verdict, wait = _wait_result(page, scene['bg']['el'], timeout)
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
        'container': '%s × %s' % (round(clip['width']), round(clip['height'])),
        # 每一次尝试用的是哪个坐标、是模型给的还是兜底挪的。
        # 回放报告和排障都靠它区分「模型这次认错了」和「拖动没被接收」。
        'reads': reads,
    })
    if not result['ok']:
        result['error'] = {
            'fail': '拖到了位置但组件判定不对（缺口坐标 %s，实测比例 %s）。'
                    % (result['ai_x'], result['ratio']),
            'timeout': '拖完 %s 秒仍没有结果，可能组件没接收这次拖动。' % timeout,
        }.get(last['verdict'], '滑块没有通过。')
    return result


def describe(result):
    """给回放明细用的一句话。"""
    if result.get('ok'):
        how = '第 %d 次' % result.get('attempt', 1)
        if result.get('attempt', 1) > 1:
            reads = result.get('reads') or []
            via = reads[-1].get('via') if reads else 'model'
            how += '·重新识别' if via == 'model' else '·沿用坐标'
        return '滑块已通过（%s，缺口 %s，拖动 %s px，%.1fs）' % (
            how, result.get('ai_x'), result.get('distance'), result.get('elapsed', 0))
    return '滑块未通过：%s' % (result.get('error') or '原因不明')
