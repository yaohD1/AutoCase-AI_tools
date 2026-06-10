<template>
  <div id="app">
    <el-container>
      <el-header>
        <div class="header-content">
          <div class="brand" @click="$router.push('/')">
            <svg class="brand-logo" viewBox="0 0 32 32" width="32" height="32">
              <rect x="2" y="2" width="28" height="28" rx="7" fill="none" stroke="#ffffff" stroke-width="2"/>
              <path d="M10 20V12l6 8V12" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="22" cy="16" r="2.5" fill="#60a5fa"/>
            </svg>
            <span class="brand-name">AutoCase</span>
            <span class="brand-divider">·</span>
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
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { Setting } from '@element-plus/icons-vue'
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

.el-header {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  height: 72px;
  display: flex;
  align-items: center;
  padding: 0 40px;
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: 0 2px 20px rgba(0, 0, 0, 0.2);
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
  font-size: 20px;
  font-weight: 600;
  color: #ffffff;
  letter-spacing: -0.3px;
}

.brand-divider {
  color: rgba(255, 255, 255, 0.3);
  font-size: 18px;
}

.brand-sub {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.45);
  font-weight: 400;
  letter-spacing: 0.2px;
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
  padding: 36px 32px;
  min-height: calc(100vh - 72px);
}

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