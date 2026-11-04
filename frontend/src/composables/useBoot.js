/* 品牌启动幕的节奏控制。
   幕布本体写在 index.html 里（#ac-boot），样式也是内联的 —— 首帧就得有它，
   等 Vue 挂载后再插一个全屏遮罩，中间那段正是「白屏 / 半截页面」最难看的时候。
   这里只管两件事：什么时候揭开、以及首页入场该往后推迟多少。

   三个数字就是整段节奏，改这里就够了：
     HOLD  幕布从「导航开始」起至少停多久
     PRE   首页淡入比幕布起手早多少毫秒 —— 必须早一点。CSS 动画的
           startTime 是「样式算完的第一帧」而不是写类的那一刻，实测比这里
           算的晚 100ms 出头；不提前的话，幕布已经把下半屏让开了，页面还是 0。
     LIFT  上升时长，必须和 index.html 里 boot-lift 的 .4s 一致 */

const HOLD = 1520
const PRE = 160
const LIFT = 400

export function bootPending() {
  return document.documentElement.dataset.boot === '1'
}

/* 首页入场该推迟多久：没有幕布就是 0（行为和以前完全一致）。
   有幕布时写进 --boot-off 交给 CSS，让动画在首帧就处于 delay 阶段（opacity:0）——
   比「先不加类、揭幕后再补」可靠：后者会让已经在 DOM 里的元素先以 opacity:1
   亮一帧，再被拉回 0，也就是闪一下。 */
export function bootDelay() {
  return bootPending() ? Math.max(0, holdLeft() - PRE) : 0
}

function holdLeft() {
  // performance.now() 是相对导航开始的，所以加载慢的机器不会再多等这 1520ms，
  // 只有本地秒开时才会真的看到它
  return Math.max(0, HOLD - performance.now())
}

export function liftBoot() {
  const el = document.getElementById('ac-boot')
  // el 为空只可能是被 8s 兜底摘掉了，那时 data-boot 也已清掉，一起早退
  if (!bootPending() || !el) {
    if (el) el.remove()
    return
  }

  window.setTimeout(() => {
    el.classList.add('is-lift')
    // 动画走完再把节点摘掉：留着它虽然已经移出屏幕，但会一直占一个合成层
    window.setTimeout(() => {
      el.remove()
      document.documentElement.removeAttribute('data-boot')
    }, LIFT + 240)
  }, holdLeft())
}
