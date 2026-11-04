<template>
  <div id="app" :class="{ 'is-panel-mode': panelMode, 'is-workspace': workspaceMode }">
    <el-container>
      <el-header v-if="!panelMode">
        <div class="header-content">
          <div class="brand" @click="$router.push('/')">
            <svg class="brand-logo" viewBox="0 0 24 24" width="24" height="24">
              <rect x="1.25" y="1.25" width="21.5" height="21.5" rx="5.5" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="1.25"/>
              <path d="M7.5 15.5v-7l4.5 7v-7" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="16.5" cy="12" r="1.6" fill="#60a5fa"/>
            </svg>
            <span class="brand-name">AutoCase</span>
            <span class="brand-divider">/</span>
            <span class="brand-sub">Test Case Generator</span>
          </div>
          <div class="head-actions">
            <!-- 深色时显示太阳（点它回到浅色），浅色时显示月亮。
                 图标比文字省位，也比「浅/深」两个字更少歧义。 -->
            <el-button
              text
              class="head-btn"
              :title="isDark ? '切换到浅色' : '切换到深色'"
              :aria-label="isDark ? '切换到浅色' : '切换到深色'"
              @click="toggle"
            >
              <el-icon :size="17">
                <Sunny v-if="isDark" />
                <Moon v-else />
              </el-icon>
            </el-button>
            <el-button @click="$router.push('/settings')" text class="head-btn" title="设置">
              <el-icon :size="18"><Setting /></el-icon>
            </el-button>
          </div>
        </div>
      </el-header>
      <el-main>
        <!-- 换页记号：只有真正跳转才扫一次，首屏进入与面板模式都不出。
             靠 :key 换新节点让 CSS 动画重播，不用计时器收尾。 -->
        <div v-if="!panelMode && navTick" :key="navTick" class="nav-line"></div>
        <router-view v-slot="{ Component }">
          <!-- 只定义了 enter（见 motion.css）：离开是瞬时的，
               所以 out-in 不会把跳转拖慢，只是保证两页不叠在一起。 -->
          <transition name="page" mode="out-in">
            <keep-alive>
              <component :is="Component" :key="$route.fullPath" />
            </keep-alive>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { computed, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Setting, Sunny, Moon } from '@element-plus/icons-vue'
import { useTheme } from './composables/useTheme'

const { isDark, toggle } = useTheme()

// 录制编排面板（?panel=1）与嵌入外壳（?embed=1）都是全屏工作区：去掉顶部导航和页面留白。
const route = useRoute()
const router = useRouter()
const panelMode = computed(() => route.query.panel === '1' || route.query.embed === '1')

// 每次真实换页 +1，绑在导航线上当 key。
// from.name 为空说明还是首次进入（START_LOCATION），那时不该扫线 ——
// 首屏有自己的阶梯入场，再来一根线就成加载条了。
// 只有 query 变化（比如面板开关）不算换页，页面本身没动。
const navTick = ref(0)
router.beforeEach((to, from) => {
  if (!from.name || to.path === from.path) return
  navTick.value += 1
})

// 录制/生成记录是「一屏看完」的工作页：收掉 body 的 8px 默认边距和多余的页面留白，避免 16px 溢出滚出滚动条。
const workspaceMode = computed(() => !panelMode.value && route.path.startsWith('/recording'))

watchEffect(() => {
  document.body.classList.toggle('immersive', panelMode.value || workspaceMode.value)
})
</script>

<style>
#app {
  font-family: var(--sans, -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

/* 顶栏：一件深色横条压在页面上方就够，不加渐变之外的装饰。
   去掉了阴影 —— 卡片上没有投影之后，顶栏下面一坨柔光反而显得脏。 */
.el-header {
  background: var(--topbar);
  border-bottom: 1px solid var(--topbar-line);
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 28px;
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-content {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  user-select: none;
}

.brand-logo {
  flex-shrink: 0;
}

.brand-name {
  font-size: 15px;
  font-weight: 600;
  color: #ffffff;
  letter-spacing: .01em;
}

.brand-divider {
  color: rgba(255, 255, 255, .22);
  font-size: 13px;
}

.brand-sub {
  font-size: 11.5px;
  color: rgba(255, 255, 255, .38);
  font-weight: 400;
  letter-spacing: .02em;
}

/* 顶栏右侧：深浅色 + 设置。两颗同样大小、同样克制，
   谁都不该比另一个更显眼 —— 这里没有主行动。 */
.head-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.head-btn {
  width: 30px;
  height: 30px;
  padding: 0 !important;
  color: rgba(255, 255, 255, 0.55) !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  transition: color .16s ease, background .16s ease;
}

.head-btn:hover {
  color: #ffffff !important;
  background: rgba(255, 255, 255, 0.1) !important;
}

.el-main {
  background: var(--bg-page);
  padding: 28px;
  min-height: calc(100vh - 56px);
  /* 导航线挂在这里的顶边，需要它当定位父级 */
  position: relative;
}

/* 编排面板窗口：无顶部导航、无留白、高度锁死一屏 */
#app.is-workspace .el-main {
  padding: 22px 32px;
}

#app.is-panel-mode .el-main {
  padding: 0;
  min-height: 100vh;
  height: 100vh;
  overflow: hidden;
}

/* body 默认有 8px margin：不抹掉的话 #app 的 100vh 会多出 16px，
   每个普通页面右侧都挂着一条空滚动条。原来只在 immersive 下修了。 */
body { margin: 0; }

/* 底色挂在 html/body 上：页面比视口短时，下面露出来的那块不能是浏览器默认白 */
html,
body { background: var(--bg-page); }

/* 全屏工作区：锁掉 body 滚动，避免内部两列滚动时外层跟着晃 */
body.immersive { margin: 0; overflow: hidden; }

/* ========== 全局 Element Plus 组件样式统一 ========== */

/* 按钮 */
.el-button {
  border-radius: var(--radius-ctl);
  font-weight: 500;
  letter-spacing: -0.2px;
  transition: all 0.2s;
}
.el-button--primary {
  background: var(--accent);
  border-color: var(--accent);
  box-shadow: var(--shadow-primary);
}
.el-button--primary:hover {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
  box-shadow: var(--shadow-primary-hover);
}
.el-button--primary:active {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}
.el-button--danger {
  background: var(--danger);
  border-color: var(--danger);
}
.el-button--danger:hover {
  background: var(--danger-hover);
  border-color: var(--danger-hover);
}
.el-button--success {
  background: var(--ok);
  border-color: var(--ok);
}
.el-button--success:hover {
  background: var(--ok);
  border-color: var(--ok);
}
.el-button--warning {
  border-radius: 10px;
}

/* 输入框 */
.el-input__wrapper {
  border-radius: var(--radius-ctl);
  box-shadow: 0 0 0 1px var(--line-field) inset;
  transition: box-shadow .16s ease;
}
.el-input__wrapper:hover {
  box-shadow: 0 0 0 1px var(--line-field-hover) inset;
}
.el-input.is-focus .el-input__wrapper {
  box-shadow: 0 0 0 2px rgba(0, 113, 227, 0.3) inset;
}

/* 表格 */
.el-table {
  border-radius: var(--radius-card);
  overflow: hidden;
  font-size: 14px;
  --el-table-bg-color: var(--bg-card);
  --el-table-tr-bg-color: var(--bg-card);
  --el-table-expanded-cell-bg-color: var(--bg-card);
  --el-table-border-color: var(--line-faint);
  --el-table-header-bg-color: var(--bg-card);
  --el-table-row-hover-bg-color: var(--bg-sunken);
  --el-table-text-color: var(--ink-2);
  --el-table-header-text-color: var(--muted);
}
.el-table th.el-table__cell {
  background: var(--bg-card);
  color: var(--ink-2);
  font-weight: 600;
  font-size: 13px;
}
.el-table [class*="el-table__fixed"] th.el-table__cell,
.el-table [class*="el-table__fixed"] td.el-table__cell {
  background: var(--bg-card) !important;
}
.el-table .el-table__cell {
  border-bottom: 1px solid var(--line-faint);
}

/* 对话框 */
.el-dialog {
  border-radius: var(--radius-card);
  background: var(--bg-card);
}
.el-dialog__header {
  padding: 20px 24px 0;
  font-weight: 600;
  color: var(--ink);
}
.el-dialog__body {
  padding: 20px 24px;
}
.el-overlay {
  background-color: var(--scrim);
}

/* 标签 */
.el-tag {
  border-radius: 6px;
  font-weight: 500;
}

/* 卡片 */
.el-card {
  border-radius: var(--radius-card);
  border: 1px solid var(--line-soft);
  background: var(--bg-card);
  box-shadow: none;
}
.el-card__header {
  border-bottom: 1px solid var(--line-soft);
  padding: 18px 24px;
  font-weight: 600;
  color: var(--ink);
}
.el-card__body {
  padding: 24px;
}

/* 分页 */
.el-pagination .btn-prev,
.el-pagination .btn-next,
.el-pager li {
  border-radius: 8px;
}

/* 表格内工具提示行 */
.el-table__empty-text {
  color: var(--muted);
}

/* 底部消息提示：底色跟着卡片走，只让图标带语义色。
   否则 Element 会拿 --el-color-*-light-9 当底，暗色下是一片半透明的绿/黄，
   飘在内容上既糊又读不清。 */
.el-message {
  border-radius: 10px;
  background: var(--bg-raised);
  border: 1px solid var(--line-soft);
  box-shadow: none;
}
.el-message .el-message__content { color: var(--ink-2); }
.el-message-box {
  border-radius: var(--radius-card);
  background: var(--bg-raised);
  border: 1px solid var(--line-soft);
}

/* 表格操作按钮对齐 */
.table-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
</style>
