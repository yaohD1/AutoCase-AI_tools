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
          <el-button @click="$router.push('/settings')" text class="settings-btn">
            <el-icon :size="18"><Setting /></el-icon>
          </el-button>
        </div>
      </el-header>
      <el-main>
        <router-view v-slot="{ Component }">
          <keep-alive>
            <component :is="Component" :key="$route.fullPath" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { computed, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { Setting } from '@element-plus/icons-vue'

// 录制编排面板（?panel=1）与嵌入外壳（?embed=1）都是全屏工作区：去掉顶部导航和页面留白。
const route = useRoute()
const panelMode = computed(() => route.query.panel === '1' || route.query.embed === '1')

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
  background: linear-gradient(135deg, #191c23 0%, #171d2b 55%, #16233a 100%);
  border-bottom: 1px solid rgba(255, 255, 255, .07);
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

.settings-btn {
  color: rgba(255, 255, 255, 0.55) !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  transition: all 0.2s;
}

.settings-btn:hover {
  color: #ffffff !important;
  background: rgba(255, 255, 255, 0.1) !important;
}

.el-main {
  background: #f5f7fa;
  padding: 28px;
  min-height: calc(100vh - 56px);
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

/* 全屏工作区：锁掉 body 滚动，避免内部两列滚动时外层跟着晃 */
body.immersive { margin: 0; overflow: hidden; }

/* ========== 全局 Element Plus 组件样式统一 ========== */

/* 按钮 */
.el-button {
  border-radius: 10px;
  font-weight: 500;
  letter-spacing: -0.2px;
  transition: all 0.2s;
}
.el-button--primary {
  background: #0071e3;
  border-color: #0071e3;
  box-shadow: 0 2px 6px rgba(0, 113, 227, 0.2);
}
.el-button--primary:hover {
  background: #0077ed;
  border-color: #0077ed;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.3);
}
.el-button--primary:active {
  background: #0068d1;
  border-color: #0068d1;
}
.el-button--danger {
  background: #ff3b30;
  border-color: #ff3b30;
}
.el-button--danger:hover {
  background: #ff4d40;
  border-color: #ff4d40;
}
.el-button--success {
  background: #34c759;
  border-color: #34c759;
}
.el-button--success:hover {
  background: #40d865;
  border-color: #40d865;
}
.el-button--warning {
  border-radius: 10px;
}

/* 输入框 */
.el-input__wrapper {
  border-radius: 10px;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08) inset;
  transition: all 0.2s;
}
.el-input__wrapper:hover {
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.12) inset;
}
.el-input.is-focus .el-input__wrapper {
  box-shadow: 0 0 0 2px rgba(0, 113, 227, 0.3) inset;
}

/* 下拉框 */
.el-select .el-input__wrapper {
  border-radius: 10px;
}

/* 表格 */
.el-table {
  border-radius: 12px;
  overflow: hidden;
  font-size: 14px;
}
.el-table th.el-table__cell {
  background: #f5f5f7;
  color: #1d1d1f;
  font-weight: 600;
  font-size: 13px;
}
.el-table [class*="el-table__fixed"] th.el-table__cell,
.el-table [class*="el-table__fixed"] td.el-table__cell {
  background: #f5f5f7 !important;
}
.el-table .el-table__cell {
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}

/* 对话框 */
.el-dialog {
  border-radius: 16px;
}
.el-dialog__header {
  padding: 20px 24px 0;
  font-weight: 600;
  color: #1d1d1f;
}
.el-dialog__body {
  padding: 20px 24px;
}

/* 标签 */
.el-tag {
  border-radius: 8px;
  font-weight: 500;
}

/* 卡片 */
.el-card {
  border-radius: 16px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 24px rgba(0, 0, 0, 0.06);
}
.el-card__header {
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  padding: 18px 24px;
  font-weight: 600;
  color: #1d1d1f;
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
  color: #86868b;
}

/* 底部消息提示 */
.el-message {
  border-radius: 12px;
}
.el-message-box {
  border-radius: 16px;
}

/* 表格操作按钮对齐 */
.table-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
</style>
