import { ref, computed } from 'vue'

/* ==================== 深浅色 ====================
   三个约定：
     1. 主题挂在 <html data-theme> 上，同时保留 .dark 类 —— Element Plus 的
        dark/css-vars.css 认的是 html.dark，我们自己的 token 认 data-theme，
        两个都得设，否则会一半暗一半亮。
     2. localStorage 只存 'light' / 'dark'。没有记录 = 跟随系统（首次访问），
        手动点过一次之后就听用户的，不再跟着系统变。
     3. 首屏由 index.html 里的内联脚本先定好，这里只负责后续切换，
        避免刷新时先白一帧。

   键名 ac-theme 在 index.html 里也写了一遍，改的时候两边一起改。 */

const STORAGE_KEY = 'ac-theme'
const media = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null

function readSaved() {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'dark' || value === 'light' ? value : null
  } catch (e) {
    return null
  }
}

const saved = readSaved()
const theme = ref(saved || (media && media.matches ? 'dark' : 'light'))
const isDark = computed(() => theme.value === 'dark')

function apply(next) {
  theme.value = next
  const root = document.documentElement
  if (next === 'dark') {
    root.dataset.theme = 'dark'
    root.classList.add('dark')
  } else {
    delete root.dataset.theme
    root.classList.remove('dark')
  }
  // 移动端地址栏、表单控件（滚动条、日期选择器）都读这个
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', next === 'dark' ? '#12161d' : '#f5f7fa')
}

// 首次进入还没手动选过：跟随系统，系统变了就跟着变。
// 手动切过一次之后本监听仍然挂着（移除反而更容易漏），所以要在回调里再确认一次。
if (media && !saved) {
  const onChange = event => {
    if (readSaved()) return
    apply(event.matches ? 'dark' : 'light')
  }
  if (media.addEventListener) media.addEventListener('change', onChange)
  else if (media.addListener) media.addListener(onChange)
}

export function useTheme() {
  function toggle() {
    const next = isDark.value ? 'light' : 'dark'
    apply(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch (e) { /* 隐私模式下写不进去就算了，本次会话仍然是切过的 */ }
  }

  return { theme, isDark, toggle }
}
