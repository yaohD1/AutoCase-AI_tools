"""生成到目标项目里的滑块工具源码（TypeScript）。

和 Python 侧 `slider.py` 是同一套流程，但不是逐行翻译：
- 沙箱里没有 PIL，没法放大图片，所以直接发原图（Python 侧放大 2 倍是为了保险，
  实测原图也能读准，差别见 AGENTS.md 的说明）。
- 模型配置走环境变量，不把 key 写进代码。生成时 .env 已经填好，
  换环境只改 .env。
"""

SLIDER_TS = r"""/**
 * 滑块验证码求解（拼图缺口型）。
 *
 * 由 AutoCase 生成。只处理「拖对了就放行」的前端校验组件；
 * 带服务端风控的验证码（极验、腾讯验证码）不保证能过。
 *
 * 模型配置来自环境变量，不要硬编码：
 *   SLIDER_MODEL_BASE_URL   OpenAI 兼容端点
 *   SLIDER_MODEL_NAME       模型名
 *   SLIDER_MODEL_API_KEY    密钥
 *
 * 这个文件不要手改：平台重新生成时会覆盖。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import type { Locator, Page } from '@playwright/test';

export interface SliderConfig {
  /** 容器 CSS 选择器；留空则按 canvas 尺寸自动识别 */
  selector?: string;
  /** 失败后最多重试几次（默认 2，即最多拖 3 次） */
  attempts?: number;
  /** 单次等结果的秒数（默认 20） */
  timeout?: number;
}

const HANDLE_SELECTORS = [
  '.range-btn', '[class*="range-btn"]', '[class*="slider-btn"]',
  '[class*="drag-btn"]', '[class*="verify-btn"]',
];
const FAIL_WORDS = ['验证失败', '验证不通过'];
const PROBE_PX = 24;

let dotenvCache: Record<string, string> | null = null;

/**
 * Playwright 不会自动加载 .env，这里补上：从这个文件往上找 .env。
 * 不引入 dotenv 依赖——目标项目不一定装了，而这段逻辑只有二十行。
 */
function readDotenv(): Record<string, string> {
  if (dotenvCache) return dotenvCache;
  const out: Record<string, string> = {};
  let dir = typeof __dirname !== 'undefined' ? __dirname : process.cwd();
  for (let depth = 0; depth < 4; depth += 1) {
    const file = join(dir, '.env');
    if (existsSync(file)) {
      for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
        const line = raw.trim();
        if (!line || line.startsWith('#')) continue;
        const eq = line.indexOf('=');
        if (eq <= 0) continue;
        const key = line.slice(0, eq).trim();
        let value = line.slice(eq + 1).trim();
        if ((value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        if (!(key in out)) out[key] = value;
      }
      break;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  dotenvCache = out;
  return out;
}

function env(name: string): string {
  const value = process.env[name] || readDotenv()[name];
  if (!value) {
    throw new Error(
      `缺少环境变量 ${name}。滑块验证需要视觉模型，请在项目根 .env 里补齐三项：` +
      'SLIDER_MODEL_BASE_URL / SLIDER_MODEL_NAME / SLIDER_MODEL_API_KEY',
    );
  }
  return value;
}

async function askModel(png: Buffer, width: number, height: number): Promise<number> {
  const base = env('SLIDER_MODEL_BASE_URL').replace(/\/+$/, '');
  const model = env('SLIDER_MODEL_NAME');
  const prompt =
    `这是滑块拼图验证码的区域截图（${width}x${height}）。` +
    '图里只有一个白色缺口，拼图块已被隐藏。请给出缺口左边缘的 x 坐标，' +
    `坐标系按这张 ${width}x${height} 的图算。只输出 JSON：{"x": 整数}`;

  const resp = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env('SLIDER_MODEL_API_KEY')}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      // 这个模型会先输出 reasoning，给小了 content 会是空的。
      max_tokens: 1200,
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: prompt },
          { type: 'image_url', image_url: { url: `data:image/png;base64,${png.toString('base64')}` } },
        ],
      }],
    }),
  });
  if (!resp.ok) {
    throw new Error(`滑块模型返回 ${resp.status}: ${(await resp.text()).slice(0, 160)}`);
  }
  const body: any = await resp.json();
  const message = body?.choices?.[0]?.message || {};
  for (const field of ['content', 'reasoning_content']) {
    const text = String(message[field] || '');
    const match = text.match(/\{[^{}]*"x"\s*:\s*(-?\d+)[^{}]*\}/) || text.match(/"x"\s*:\s*(-?\d+)/);
    if (match) {
      const x = parseInt(match[1], 10);
      if (x >= 0 && x <= width) return x;
      throw new Error(`模型给的坐标 ${x} 超出图片宽度 ${width}`);
    }
  }
  throw new Error(`模型没有返回可解析的坐标：${String(message.content || '').slice(0, 120)}`);
}

type CanvasBox = { locator: Locator; x: number; y: number; width: number; height: number };

/** 取候选画布。root 可以是页面，也可以是用户拾取到的容器 / 画布。 */
async function collectCanvases(root: Locator | Page, isElement: boolean): Promise<CanvasBox[]> {
  const items: CanvasBox[] = [];
  const push = async (loc: Locator) => {
    const box = await loc.boundingBox().catch(() => null);
    if (box) items.push({ locator: loc, x: box.x, y: box.y, width: box.width, height: box.height });
  };
  // 拾取到的可能直接就是画布本身：它不在自己的 querySelectorAll('canvas') 里。
  if (isElement) {
    const tag = await (root as Locator).evaluate((el) => el.tagName.toLowerCase()).catch(() => '');
    if (tag === 'canvas') await push(root as Locator);
  }
  const canvases = root.locator('canvas');
  const total = await canvases.count();
  for (let i = 0; i < total; i += 1) await push(canvases.nth(i));
  return items;
}

async function findHandle(page: Page, bg: CanvasBox) {
  const bottom = bg.y + bg.height;
  for (const sel of HANDLE_SELECTORS) {
    for (const el of await page.locator(sel).all()) {
      const box = await el.boundingBox();
      if (!box) continue;
      if (box.width < 30 || box.width > 90 || box.height < 30 || box.height > 90) continue;
      // 手柄贴着画布底部外侧（实测在下方 20px），不是贴着画布顶部的。
      if (Math.abs(box.x - bg.x) < 60 && box.y - bottom >= 0 && box.y - bottom < 90) {
        return { locator: el, box };
      }
    }
  }
  return null;
}

async function findScene(page: Page, selector?: string) {
  // 候选一律用 locator 而不是坐标：拼图块拖动后会移动，靠坐标反查
  // 会在第二次重试时找不到（实测 ratio 从 0.9654 掉成 1）。
  const roots: Array<{ root: Locator | Page; isElement: boolean }> = [];
  const picked = (selector || '').trim();
  if (picked) {
    // 用户指定了容器就只在这个范围里找：页面上有多个验证码时，
    // 「自动挑最大的」经常不是他想要的那个。
    const found = page.locator(picked);
    const total = await found.count();
    for (let i = 0; i < total; i += 1) roots.push({ root: found.nth(i), isElement: true });
  }
  roots.push({ root: page, isElement: false });

  for (const { root, isElement } of roots) {
    const items = await collectCanvases(root, isElement);
    const big = items.filter((c) => c.width >= 200 && c.width <= 640 &&
      c.height >= 110 && c.height <= 300 && c.width > c.height);
    // 容器本身也可能落在尺寸区间里，但它配不上手柄（手柄在它内部）。
    // 按面积从大到小逐个试，配不上就落到内部真正的画布上。
    for (const bg of big.sort((a, b) => b.width * b.height - a.width * a.height)) {
      const handle = await findHandle(page, bg);
      if (!handle) continue;
      const piece = items.find((c) => c.width < 100 && c.height >= 60 &&
        Math.abs(c.x - bg.x) < 40 && Math.abs(c.y - bg.y) < 24) || null;
      return { bg, piece, handle, selector };
    }
  }
  return null;
}

async function setPieceVisible(page: Page, piece: any, visible: boolean) {
  if (!piece) return;
  try {
    await piece.locator.evaluate((el: HTMLElement, on: boolean) => {
      el.style.visibility = on ? '' : 'hidden';
    }, visible);
  } catch {
    // 元素已经不在（组件被销毁），后面自然会失败，这里不额外抛。
  }
}

/** 截一张「只有缺口、没有拼图块」的容器图。拼图块和缺口同形，不藏起来会认错。 */
async function capture(page: Page, scene: any) {
  const bg = scene.bg;
  const clip = { x: bg.x, y: bg.y, width: bg.width, height: bg.height };
  await setPieceVisible(page, scene.piece, false);
  let png: Buffer;
  try {
    png = await page.screenshot({ clip });
  } finally {
    await setPieceVisible(page, scene.piece, true);
  }
  return { png, width: Math.round(bg.width), height: Math.round(bg.height) };
}

function nudge(x: number, width: number): number {
  return Math.min(Math.max(x + [-2, 0, 2][Math.floor(Math.random() * 3)], 1),
    Math.max(width - 1, 1));
}

/** 等上一次的失败提示消失。提示还挂着时，waitResult 会把它当成这次的结果。 */
async function waitReset(page: Page, timeout = 4000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    let text = '';
    try {
      text = await page.evaluate(() => document.body?.innerText || '');
    } catch {
      return; // 页面正在跳走，读不到 body，直接放行
    }
    if (!FAIL_WORDS.some((w) => text.includes(w))) return;
    await page.waitForTimeout(200);
  }
}

async function glide(page: Page, fromX: number, fromY: number, toX: number, toY: number, steps = 26) {
  for (let i = 1; i <= steps; i += 1) {
    const p = i / steps;
    const eased = 1 - Math.pow(1 - p, 2.4);
    const jitter = i < steps - 3 ? Math.sin(i * 0.9) * 0.7 : 0;
    await page.mouse.move(fromX + (toX - fromX) * eased, fromY + jitter);
    await page.waitForTimeout(8 + Math.random() * 9);
  }
  await page.mouse.move(toX, toY);
}

async function waitResult(page: Page, timeoutSec: number): Promise<'pass' | 'fail' | 'timeout'> {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const alive = await page.evaluate(() =>
      !!document.querySelector('canvas') &&
      Array.from(document.querySelectorAll('canvas')).some((c) => {
        const r = c.getBoundingClientRect();
        return r.width >= 200 && r.height >= 110;
      }));
    if (!alive) return 'pass';
    const text = await page.evaluate(() => document.body?.innerText || '');
    if (FAIL_WORDS.some((w) => text.includes(w))) return 'fail';
    await page.waitForTimeout(250);
  }
  return 'timeout';
}

/**
 * 在当前页面上过一次滑块。失败抛错，不返回 false——
 * 让它静默失败的话，后面的断言会在错误页面上跑，报错点离真正的原因很远。
 */
export async function solveSlider(page: Page, config: SliderConfig = {}): Promise<void> {
  const attempts = Math.min(Math.max(config.attempts ?? 2, 0), 5);
  const timeout = Math.min(Math.max(config.timeout ?? 20, 5), 90);

  let scene;
  try {
    scene = await findScene(page, config.selector);
  } catch (err) {
    throw new Error(`容器选择器无效：${String(err).slice(0, 120)}`);
  }
  if (!scene) {
    throw new Error('当前页面上没有找到滑块（没找到符合尺寸的 canvas 和拖动手柄）');
  }

  const first = await capture(page, scene);
  let target = await askModel(first.png, first.width, first.height);
  let last: any = {};

  for (let attempt = 1; attempt <= attempts + 1; attempt += 1) {
    if (attempt > 1) {
      // 失败后组件会重新出题：缺口位置已经变了，照旧坐标再拖一次只是把同一段
      // 轨迹交给它复判。所以重找容器 → 重新截图 → 重新问模型；
      // 只有识别这一步也失败，才退回上次坐标挪几像素。
      await page.waitForTimeout(1200);
      await waitReset(page);
      let fresh = await findScene(page, config.selector);
      // 重绘有个一两百毫秒的空窗：失败提示已经消失，但新画布还没插回来。
      for (let i = 0; i < 4 && !fresh; i += 1) {
        await page.waitForTimeout(300);
        fresh = await findScene(page, config.selector);
      }
      if (!fresh) {
        throw new Error('滑块没有通过，重试时组件已经从页面上消失了（可能被关掉或页面跳走）。' +
          `上一次识别的缺口坐标是 ${target}。`);
      }
      scene = fresh;
      try {
        const shot = await capture(page, scene);
        target = await askModel(shot.png, shot.width, shot.height);
      } catch {
        target = nudge(target, scene.bg.width);
      }
    }
    const pieceOffset = scene.piece ? scene.piece.x - scene.bg.x : 0;
    const startX = scene.handle.box.x + scene.handle.box.width / 2;
    const startY = scene.handle.box.y + scene.handle.box.height / 2;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.waitForTimeout(100 + Math.random() * 60);
    await glide(page, startX, startY, startX + PROBE_PX, startY, 8);

    // 比例不能写死：鼠标走 120px，拼图块可能只走 116px。先量 24px 再算剩下要走多少。
    let ratio = 1;
    if (scene.piece) {
      const moved = await scene.piece.locator.boundingBox().catch(() => null);
      if (moved && moved.x - scene.piece.x > 4) {
        ratio = Math.min(Math.max((moved.x - scene.piece.x) / PROBE_PX, 0.5), 2);
      }
    }
    const total = Math.max((target - pieceOffset) / ratio, PROBE_PX + 4);
    await glide(page, startX + PROBE_PX, startY, startX + total, startY, 24);
    await page.waitForTimeout(80 + Math.random() * 60);
    await page.mouse.move(startX + total, startY);
    await page.waitForTimeout(50);
    await page.mouse.up();

    const verdict = await waitResult(page, timeout);
    last = { verdict, attempt };
    if (verdict === 'pass') return;
  }

  throw new Error(
    `滑块没有通过（重试 ${last.attempt || 1} 次，缺口坐标 ${target}）：` +
    (last.verdict === 'fail' ? '拖到了位置但组件判定不对' : '拖完没有结果，组件可能没接收这次拖动'),
  );
}

/** 页面现在有没有滑块，用来提前跳过而不是等它超时。 */
export async function hasSlider(page: Page): Promise<boolean> {
  return (await findScene(page)) !== null;
}
"""
