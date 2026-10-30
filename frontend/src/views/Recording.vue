<template>
  <div :class="['recording-page', 'ui-buttons', { 'is-panel': panelMode, 'is-embed': embedMode, 'is-collapsed': panelCollapsed, 'is-lean': stepsCollapsed, 'is-resizing': resizing }]">
    <!-- 嵌入模式：左 iframe 是被测系统（占用剩余全部宽度），右侧是可收起的编排面板 -->
    <iframe v-if="embedMode" class="embed-frame" :src="targetUrl" title="被测系统"></iframe>

    <button
      v-if="embedMode && panelCollapsed"
      type="button"
      class="embed-rail"
      title="展开编排面板"
      @click="panelCollapsed = false"
    >
      <span class="rail-caret">‹</span>
      <span class="rail-count">{{ steps.length }}</span>
      <span :class="['rail-dot', { live: isRecording }]"></span>
    </button>

    <div v-if="embedMode && !panelCollapsed && !stepsCollapsed" class="embed-resizer" title="拖动调整面板宽度" @pointerdown="startResize"></div>

    <!-- 定位待确认（录制当场弹）：不唯一的定位过后再选，页面早不是当时的样子了。
         固定在右上角，不压住左边被测页面里那些带编号的框；放在 page-col 外面，
         整块面板被收起时也不会跟着隐藏。 -->
    <div v-if="locConfirm" class="loc-confirm" role="dialog" aria-live="polite" aria-label="定位待确认">
      <div class="loc-head">
        <span class="loc-tag">定位待确认</span>
        <span class="loc-step" :title="locConfirm.label">第 {{ locConfirm.no }} 步 · {{ locConfirm.label }}</span>
        <el-button class="loc-close" text size="small" :icon="Close" title="稍后再选：步骤保留「不唯一」标记" @click="deferLocConfirm" />
      </div>
      <p class="loc-lead">
        <template v-if="locConfirm.loading">正在把匹配到的元素在页面上框出来…</template>
        <template v-else-if="locConfirm.count > 1">
          这一步在页面上匹配到 {{ locConfirm.count }} 个元素，已经按同样的顺序编号框出。
          <b>现在选，页面还是录制时的样子</b>；过后再选就得靠你自己核对了。
        </template>
        <template v-else>当前页面上已经找不到这一步的元素了（点完页面跳走了）。</template>
      </p>
      <!-- 页面跑掉时唯一的补救：用录制时记下的地址把那一页重新打开。
           浏览器后退在这里靠不住——实测会被登录跳转的历史条目带偏。 -->
      <p v-if="locConfirm.gone && !locConfirm.returnUrl" class="loc-gone">
        可以回到那一步的页面重新框选，也可以直接按下面的快照选。
      </p>
      <div v-if="locConfirm.items.length" class="loc-list">
        <button
          v-for="item in locConfirm.items"
          :key="item.index"
          type="button"
          :class="['loc-item', { 'is-picked': locConfirm.picked === item.index }]"
          @click="pickLocConfirm(item.index)"
        >
          <span class="loc-no">{{ item.index + 1 }}</span>
          <span class="loc-text">{{ item.row || item.text || ('<' + item.tag + '>') }}</span>
          <span v-if="locConfirm.active === item.index" class="loc-origin">录制时点的</span>
          <span v-if="!item.visible" class="loc-hidden">不可见</span>
        </button>
      </div>
      <p v-if="locConfirm.note" class="loc-note">{{ locConfirm.note }}</p>
      <div class="loc-foot">
        <span v-if="locQueue.length" class="loc-queue">还有 {{ locQueue.length }} 步待确认</span>
        <div class="loc-actions">
          <el-button
            v-if="locConfirm.returnUrl"
            size="small"
            :loading="locConfirm.returning"
            @click="returnFromStepPage"
          >
            回到刚才的页面
          </el-button>
          <el-button
            v-else-if="locConfirm.gone"
            size="small"
            :loading="locConfirm.reopening"
            @click="reopenStepPage"
          >
            回到这一步的页面
          </el-button>
          <el-button text size="small" @click="deferLocConfirm">稍后再选</el-button>
          <el-button
            v-if="locConfirm.items.length"
            type="primary"
            size="small"
            :disabled="locConfirm.picked == null"
            :loading="locPicking"
            @click="acceptLocConfirm"
          >
            就这个，继续
          </el-button>
        </div>
      </div>
    </div>

    <div
      v-show="!embedMode || !panelCollapsed"
      class="page-col"
      :class="{ 'is-panel': embedMode }"
      :style="embedMode ? { width: panelWidth + 'px' } : null"
    >
      <!-- 面板窗口（录制时自动弹出的右侧编排栏）：紧凑标题条，把空间都留给步骤 -->
      <div v-if="compactMode" class="panel-head">
        <span :class="['panel-dot', session && session.status === 'recording' ? (paused ? 'is-paused' : 'is-live') : '']"></span>
        <span class="panel-title">{{ panelTitle }}</span>
        <span v-if="steps.length" class="panel-count">{{ steps.length }} 步</span>
        <span v-if="blockers.length" class="panel-warn">{{ blockers.length }} 处定位待确认</span>
        <div class="panel-actions">
          <el-button v-if="session?.panel" text size="small" :icon="Connection" @click="relayoutPanel">并排</el-button>
          <el-button text size="small" @click="configExpanded = !configExpanded">{{ configExpanded ? '收起配置' : '配置' }}</el-button>
          <button v-if="embedMode" type="button" class="panel-collapse" title="收起面板，把宽度还给页面" @click="panelCollapsed = true">
            <span class="collapse-arrow">›</span>
          </button>
        </div>
      </div>

      <div v-if="!compactMode" class="page-header">
      <div class="page-intro">
        <div class="eyebrow">录制 · 代码生成</div>
        <h1>自动化工具</h1>
        <p>
          在真实浏览器里操作，记录步骤与唯一定位；录制时会自动打开编排面板，插入断言后按工作区框架生成脚本。
          <span class="hint-dot" title="写入后可以预览，再提交或撤销；校验失败会自动还原，不留半成品。">?</span>
        </p>
      </div>
      <div class="page-actions">
        <el-button :icon="Tickets" @click="$router.push('/recording/records')">生成记录</el-button>
        <el-button @click="$router.push('/')">返回首页</el-button>
      </div>
    </div>

    <!-- 左配置 / 右步骤+生成：一屏内看完，生成记录单独成页 -->
    <div class="recording-body">
    <!-- 目标与配置：默认是一张「规格摘要」，点某一行才就地展开编辑。
         六个常驻输入框会把 400px 的侧栏塞成注册表单，读不出轻重。 -->
    <el-card v-show="!compactMode || configExpanded" class="panel-card config-card">
      <template #header>
        <div class="section-header">
          <span class="card-title">录制配置</span>
          <span class="cfg-saved">已记住上次设置</span>
        </div>
      </template>

      <div class="cfg-form">
        <!-- 唯一必填项：字号比其余值大一档，视线先落在这里 -->
        <div :class="['cfg-field', 'cfg-field--hero', { 'is-editing': cfgEditing === 'baseUrl' }]">
          <div class="cfg-label">录制起始地址</div>
          <div v-if="cfgEditing !== 'baseUrl'" class="cfg-view" @click="beginEdit('baseUrl')">
            <span :class="['cfg-text', 'cfg-mono', { 'is-empty': !config.baseUrl }]">{{ config.baseUrl || '填写被测系统地址' }}</span>
            <span class="cfg-hint">编辑</span>
          </div>
          <el-input
            v-else
            :ref="el => setEditRef('baseUrl', el)"
            v-model="config.baseUrl"
            class="cfg-input"
            placeholder="http://127.0.0.1:8080/login"
            clearable
            @blur="endEdit"
            @keyup.enter="endEdit"
            @keyup.esc="endEdit"
          />
        </div>

        <!-- 工作区只能从根目录里挑：手填的路径写错，要到生成时才炸 -->
        <div class="cfg-field">
          <div class="cfg-label" :title="'必须是 ' + (workspaceRoot || 'AUTOMATION_WORKSPACE_ROOT') + ' 内的目录'">工作区</div>
          <div class="cfg-view is-static">
            <span :class="['cfg-text', 'cfg-mono', { 'is-empty': !config.workspace }]">{{ config.workspace || '还没有选择工作区' }}</span>
            <span class="cfg-acts">
              <button type="button" class="cfg-act" @click="openWorkspacePicker">更换</button>
              <button type="button" class="cfg-act" :disabled="!config.workspace || detecting" @click="detectWorkspace">
                {{ detecting ? '检测中' : '检测' }}
              </button>
            </span>
          </div>
          <div v-if="detect" class="cfg-detect">
            <span :class="['cfg-chip', detect.framework ? 'is-ok' : '']">{{ frameworkName(detect.framework) }}</span>
            <span :class="['cfg-chip', detect.python ? 'is-ok' : 'is-warn']" :title="detect.python || ''">
              Python {{ detect.python ? tailPath(detect.python) : '未找到' }}
            </span>
            <span :class="['cfg-chip', detect.seleniumlibrary_version ? 'is-ok' : 'is-warn']">
              SeleniumLibrary {{ detect.seleniumlibrary_version || '未检测到' }}
            </span>
            <details v-if="(detect.samples && detect.samples.length) || (detect.evidence && detect.evidence.length)" class="cfg-details">
              <summary>依据与参考文件</summary>
              <div v-for="sample in detect.samples || []" :key="sample.path" class="detect-sample">{{ sample.path }}</div>
              <div v-for="(item, idx) in detect.evidence || []" :key="idx" class="evidence-line">
                <span class="evidence-kind">{{ item.kind }}</span>
                <span>{{ item.value === null ? '—' : item.value }}</span>
                <span class="evidence-source">{{ item.source }}</span>
              </div>
            </details>
          </div>
        </div>

        <!-- 只有两个取值，分段比下拉少一次点击，也让「当前用哪套」永远可见 -->
        <div class="cfg-field">
          <div class="cfg-label">生成框架</div>
          <div class="cfg-seg" role="radiogroup" aria-label="生成框架">
            <button
              v-for="item in FRAMEWORKS"
              :key="item.value"
              type="button"
              role="radio"
              :aria-checked="config.framework === item.value"
              :class="['cfg-seg-item', { 'is-on': config.framework === item.value }]"
              @click="config.framework = item.value"
            >{{ item.short }}</button>
          </div>
        </div>

        <div :class="['cfg-field', { 'is-editing': cfgEditing === 'model' }]">
          <div class="cfg-label">模型</div>
          <div v-if="cfgEditing !== 'model'" class="cfg-view" @click="beginEdit('model')">
            <span :class="['cfg-text', 'cfg-mono', { 'is-empty': !config.model }]">{{ config.model || '还没选择模型' }}</span>
            <span class="cfg-hint">{{ config.model ? '更换' : '选择' }}</span>
          </div>
          <el-select
            v-else
            :ref="el => setEditRef('model', el)"
            v-model="config.model"
            class="cfg-select"
            filterable
            placeholder="选择模型"
            @change="endEdit"
            @visible-change="open => { if (!open) endEdit() }"
          >
            <el-option v-for="m in modelOptions" :key="m.value" :label="m.value" :value="m.value" />
          </el-select>
        </div>

        <div :class="['cfg-field', { 'is-editing': cfgEditing === 'targetPath' }]">
          <div class="cfg-label" title="留空则由任务书按项目约定决定位置">目标文件</div>
          <div v-if="cfgEditing !== 'targetPath'" class="cfg-view" @click="beginEdit('targetPath')">
            <span :class="['cfg-text', 'cfg-mono', { 'is-empty': !config.targetPath }]">{{ config.targetPath || '留空，由任务书决定' }}</span>
            <span class="cfg-acts">
              <button type="button" class="cfg-act" :disabled="!config.workspace.trim()" @click.stop="openTargetPicker">浏览</button>
            </span>
          </div>
          <el-input
            v-else
            :ref="el => setEditRef('targetPath', el)"
            v-model="config.targetPath"
            class="cfg-input"
            :placeholder="targetPlaceholder"
            clearable
            @blur="endEdit"
            @keyup.enter="endEdit"
            @keyup.esc="endEdit"
          />
        </div>

        <div :class="['cfg-field', { 'is-editing': cfgEditing === 'title' }]">
          <div class="cfg-label">用例名称</div>
          <div v-if="cfgEditing !== 'title'" class="cfg-view" @click="beginEdit('title')">
            <span :class="['cfg-text', { 'is-empty': !config.title }]">{{ config.title || '例如：登录成功' }}</span>
            <span class="cfg-hint">编辑</span>
          </div>
          <el-input
            v-else
            :ref="el => setEditRef('title', el)"
            v-model="config.title"
            class="cfg-input"
            placeholder="例如：登录成功"
            maxlength="200"
            clearable
            @blur="endEdit"
            @keyup.enter="endEdit"
            @keyup.esc="endEdit"
          />
        </div>

        <!-- 高级项折成一行：值直接写在右侧，不用展开也知道当前用的是哪个解释器 -->
        <button type="button" class="cfg-adv" @click="showAdvanced = !showAdvanced">
          <span :class="['cfg-caret', { 'is-open': showAdvanced }]">
            <el-icon :size="10"><ArrowDown /></el-icon>
          </span>
          <span>Python 解释器</span>
          <span class="cfg-adv-value">{{ config.python ? tailPath(config.python) : '自动查找 .venv' }}</span>
        </button>
        <div v-if="showAdvanced" :class="['cfg-field', 'cfg-field--sub', { 'is-editing': cfgEditing === 'python' }]">
          <div class="cfg-label">解释器路径（留空则自动查找项目里的 .venv）</div>
          <div v-if="cfgEditing !== 'python'" class="cfg-view" @click="beginEdit('python')">
            <span :class="['cfg-text', 'cfg-mono', { 'is-empty': !config.python }]">{{ config.python || '自动' }}</span>
            <span class="cfg-hint">编辑</span>
          </div>
          <el-input
            v-else
            :ref="el => setEditRef('python', el)"
            v-model="config.python"
            class="cfg-input"
            placeholder="例如 D:/proj/.venv/Scripts/python.exe"
            clearable
            @blur="endEdit"
            @keyup.enter="endEdit"
            @keyup.esc="endEdit"
          />
        </div>
      </div>

      <!-- 首屏的主行动放在配置末尾：它用的是上面这些设置，不该长在另一张卡里 -->
      <div v-if="!isRecording && !steps.length && !compactMode" class="cfg-foot">
        <el-button type="primary" class="cfg-start" :loading="starting" @click="startRecording">开始录制</el-button>
      </div>
    </el-card>

    <div class="main-layout">
      <!-- 录制步骤 -->
      <el-card class="panel-card steps-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">录制步骤</span>
            <div class="steps-head-right">
              <span :class="['state-pill', 'state-' + (session ? session.status : 'idle')]">{{ stateLabel }}</span>
              <el-button
                v-if="compactMode"
                text
                class="steps-collapse-btn"
                :icon="stepsCollapsed ? Expand : Fold"
                :title="stepsCollapsed ? '展开：恢复上移 / 下移（删除一直在），面板回到原来的宽度' : '收起：精简步骤行，只留拖拽排序、铅笔编辑与删除'"
                @click="toggleStepsCollapsed"
              >
                {{ stepsCollapsed ? '展开' : '收起' }}
              </el-button>
            </div>
          </div>
        </template>

        <!-- 工具栏分两组：左边「现在对浏览器做什么」，右边「往步骤里插什么」。
             组本身是一块浅色底，按钮常态无边框，悬停才浮起——少一档样式，视觉就不会碎。 -->
        <!-- 首屏还没有会话时，这一排全是禁用按钮：不显示，注意力留给左边的「开始录制」。
             面板/嵌入模式靠工具栏开始，所以那里始终保留。 -->
        <div v-if="compactMode || session || steps.length" :class="['toolbar', { 'is-lean': stepsCollapsed }]">
          <div class="toolbar-group">
            <!-- 录制 / 拾取 / 关闭浏览器：这一组都不改步骤，改的是浏览器状态 -->
            <!-- 首屏（还没有步骤）把主行动放在配置卡末尾，这里就不再重复一个「开始」；
                 面板/嵌入模式没有完整配置卡，仍由工具栏承担。 -->
            <el-button v-if="!isRecording && (steps.length || compactMode)" class="lean-wide" type="primary" :icon="VideoCamera" :loading="starting" @click="startRecording">开始</el-button>
            <!-- 停止用实心红 + 白色方块，这是录制的通用记号；暂停/继续用浅色描边，避免两个重色按钮打架。
                 条件必须写全：上面那个「开始」在首屏是隐藏的，用 v-else 会让「停止」接在隐藏分支上。 -->
            <el-button v-else-if="isRecording" type="danger" :loading="stopping" title="停止录制，步骤保留；浏览器不会关" @click="stopRecording">
              <span class="rec-stop" aria-hidden="true"></span>停止
            </el-button>
            <el-button
              v-if="isRecording"
              :type="paused ? 'success' : 'default'"
              :plain="paused"
              :icon="paused ? VideoPlay : VideoPause"
              :loading="pausing"
              :title="paused ? '继续把操作记录为步骤' : '浏览器照常可用，但操作不再记为步骤'"
              @click="togglePause"
            >
              {{ paused ? '继续' : '暂停' }}
            </el-button>
            <!-- 停止后浏览器不关，所以拾取与验证仍然可用；关掉窗口才真正结束 -->
            <el-button
              :type="picking ? 'warning' : 'default'"
              :plain="picking"
              :icon="Aim"
              :disabled="!canInspect"
              :title="canInspect ? '在浏览器里点元素，只登记为可引用的元素，不记为步骤（页面里按 Esc 也能退出）' : '浏览器已关闭，无法再拾取元素'"
              @click="togglePick(!picking)"
            >
              {{ picking ? '退出拾取' : '拾取元素' }}
            </el-button>
            <el-button v-if="canCloseBrowser" :icon="Close" :loading="closingBrowser" title="关闭被测浏览器，结束这次录制" @click="closeBrowser">关闭浏览器</el-button>
          </div>

          <span class="toolbar-sep"></span>

          <div class="toolbar-group">
            <!-- 刷新只由这个按钮添加：不自动检测（SPA 一次刷新会触发多个导航事件，分不清真假），
                 点了会真的刷新，后续步骤就落在刷新后的页面上 -->
            <el-button
              :icon="Refresh"
              :disabled="!canEdit"
              :loading="addingReload"
              :title="isRecording ? '插入一步「刷新页面」，并真的刷新浏览器（在浏览器里按 F5 不会被录制）' : '插入一步「刷新页面」（浏览器已停止录制，不会真的刷新）'"
              @click="insertReload()"
            >
              刷新页面
            </el-button>
            <el-button :icon="CircleCheck" :disabled="!canEdit" title="插入一步：判断页面上的某个状态是否成立（需要等它慢慢成立时，在表单里展开「等到成立」）" @click="openDraft('assert')">添加断言</el-button>
            <el-button :icon="Clock" :disabled="!canEdit" title="插入一步：不管条件，先干等指定秒数" @click="openDraft('sleep')">固定等待</el-button>
          </div>
        </div>

        <!-- 断言解析器：录制一开始就在后台起进程并预热。状态放这一行，
             不抢工具栏的宽度（那里一行刚好排满），只有异常时才需要用户注意。 -->
        <div v-if="askState" class="ask-hint" :class="askState.status" :title="askState.detail || ''">
          <span class="ask-dot" aria-hidden="true"></span>{{ askHint }}
        </div>

        <div v-if="paused" class="pause-hint">
          已暂停：可以在浏览器里自由操作（关闭弹层、滚动、试填一遍都不算），这些动作不会被记录。点「继续」接着录。
        </div>

        <div v-if="isStopped && browserOpen" class="keep-open-hint">
          录制已停止，浏览器仍然开着：可以继续拾取元素、验证断言；确认完成后点「关闭浏览器」。
        </div>

        <div v-if="picking" class="pick-hint">
          拾取模式已开启：在浏览器里点击元素，它会被记为可引用的元素，不会记为操作步骤。
          鼠标划过时页面会框出「这一下会拾到谁」。
          要拾取菜单、下拉、折叠面板里的元素，<b>按住 Alt 再点</b>——这一下会真的展开，不会被拦掉。
          按 <b>Esc</b> 或点上面的「退出拾取」结束。
        </div>
        <el-alert v-if="session && session.error" :title="session.error" type="error" :closable="false" class="session-error" />

        <div v-if="!steps.length && !draft" :class="['steps-empty', { 'is-roomy': !compactMode }]">
          <span class="steps-empty-dot"></span>
          <span v-if="compactMode">还没有步骤 · 点「开始」后，在弹出的浏览器里操作</span>
          <template v-else>
            <div class="steps-empty-title">还没有步骤</div>
            <div class="steps-empty-sub">点左侧「开始录制」，会在独立窗口打开被测页面。你在页面上点过、输入过的内容，会一条条出现在这里。</div>
          </template>
        </div>

        <ol v-if="steps.length" ref="timelineEl" class="timeline">
          <li
            v-for="step in steps"
            :key="step.no"
            :data-step-no="step.no"
            :class="['step', { 'is-blocked': ['ambiguous', 'suspect'].includes(effectiveStatus(step)), 'is-flash': flashingNo === step.no }]"
          >
            <div class="step-no" :title="canEdit ? '按住拖动可排序' : ''">{{ step.no }}</div>
            <div class="step-body">
              <!-- 就地编辑：只改值，不动定位 -->
              <div v-if="editing && editing.no === step.no" class="step-editor">
                <el-input
                  v-if="step.type === 'fill'"
                  v-model="editing.value"
                  size="small"
                  placeholder="输入值"
                  @keyup.enter="saveEdit"
                  @keyup.esc="cancelEdit"
                />
                <template v-else-if="step.type === 'select'">
                  <el-input v-model="editing.value" size="small" placeholder="选项值（value）" @keyup.enter="saveEdit" />
                  <el-input v-model="editing.label" size="small" placeholder="选项文本（可选）" @keyup.enter="saveEdit" />
                </template>
                <el-input
                  v-else-if="step.type === 'goto'"
                  v-model="editing.url"
                  size="small"
                  placeholder="http(s)://..."
                  @keyup.enter="saveEdit"
                  @keyup.esc="cancelEdit"
                />
                <el-input
                  v-else-if="step.type === 'press'"
                  v-model="editing.value"
                  size="small"
                  placeholder="键名，如 Enter / Control+Enter / ArrowDown"
                  @keyup.enter="saveEdit"
                  @keyup.esc="cancelEdit"
                />
                <el-select
                  v-else-if="step.type === 'dialog'"
                  v-model="editing.value"
                  size="small"
                  placeholder="弹窗怎么处理"
                >
                  <el-option label="点「确定」(accept)" value="accept" />
                  <el-option label="点「取消」(dismiss)" value="dismiss" />
                </el-select>
                <el-input-number
                  v-else-if="step.type === 'sleep'"
                  v-model="editing.seconds"
                  :min="0.1"
                  :max="600"
                  :step="0.5"
                  :precision="1"
                  size="small"
                  controls-position="right"
                />
                <!-- 断言 / 等待就地改节奏：不勾「允许等待」就是立即断言 -->
                <div v-else-if="step.type === 'assert' || step.type === 'wait'" class="step-editor-poll">
                  <el-checkbox v-model="editing.allowWait" size="small">允许等待</el-checkbox>
                  <template v-if="editing.allowWait">
                    <span class="poll-label">最多</span>
                    <el-input-number
                      v-model="editing.timeout"
                      :min="1"
                      :max="MAX_WAIT_SECONDS"
                      :step="5"
                      size="small"
                      controls-position="right"
                    />
                    <span class="poll-label">秒</span>
                    <el-checkbox v-model="editing.reload" size="small">期间刷新</el-checkbox>
                    <template v-if="editing.reload">
                      <span class="poll-label">每</span>
                      <el-input-number
                        v-model="editing.interval"
                        :min="1"
                        :max="MAX_POLL_INTERVAL"
                        size="small"
                        controls-position="right"
                      />
                      <span class="poll-label">秒</span>
                    </template>
                  </template>
                </div>
                <div class="step-editor-actions">
                  <el-button size="small" type="primary" :loading="editing.saving" @click="saveEdit">保存</el-button>
                  <el-button size="small" @click="cancelEdit">取消</el-button>
                </div>
              </div>
              <template v-else>
                <div class="step-main">
                  <span :class="['kind-tag', 'kind-' + stepTone(step.type)]">
                    <!-- 带等待的断言和立即断言在列表里必须一眼分得开，加个小钟做标识 -->
                    <el-icon v-if="step.type === 'wait'" class="kind-icon"><Timer /></el-icon>{{ stepKindLabel(step.type) }}
                  </span>
                  <span class="step-text">{{ stepDescription(step) }}</span>
                </div>
                <div v-if="step.text" class="step-note">{{ step.text }}</div>
                <div v-if="step.target" class="step-locator">
                  <span class="step-alias">{{ step.target.alias || '未命名元素' }}</span>
                  <code class="locator-code">{{ locatorText(step.target) }}</code>
                  <el-tag size="small" :type="statusTone(effectiveStatus(step))" effect="light" :title="statusHint(step)">
                    {{ statusLabel(effectiveStatus(step)) }}
                  </el-tag>
                  <span v-if="step.target.chosen && step.target.chosen.index_invalid" class="chosen-warn"
                        title="你选的序号，在停止录制时的页面上已经不存在了：生成出来会点不到，需要重新选">序号已失效</span>
                  <!-- 图标按钮这类控件：你点的是里层的图形，但脚本点的是元素的中心，
                       中心被挡住时 Playwright 会拒绝点击。录制时已自动改用能点到的父容器。 -->
                  <span v-if="step.target.snap && step.target.snap.lifted" class="lifted-hint"
                        :title="`这块区域的中心被别的元素压住了，脚本点它会报 intercepts pointer events；已把定位上提到能点到的父容器（原来是 <${step.target.snap.lifted}>）。生成写进去的就是上提后的这一条。`"
                  >已上提</span>
                  <!-- 人眼确认：把这条定位匹配到的元素全部框出来。比任何文字都可靠，
                       尤其是「两个都叫确定」这种文本和行文本都一样的场景。 -->
                  <el-button
                    class="peek-btn"
                    text
                    :icon="Aim"
                    :loading="peek.no === step.no && !!peek.loading"
                    :disabled="!canInspect"
                    :title="canInspect ? '在浏览器里框出这条定位匹配到的元素，确认是不是这一个' : '浏览器已关闭，无法框出元素'"
                    @click.stop="peekTarget(step)"
                  />
                </div>
                <div v-if="peek.no === step.no" class="peek-panel">
                  <div class="peek-head">
                    <template v-if="peek.count">
                      <span class="peek-title">匹配到 {{ peek.count }} 个，当前用的是第 {{ peek.active + 1 }} 个</span>
                      <code class="peek-desc">{{ peek.desc }}</code>
                    </template>
                    <span v-else class="peek-title">{{ peek.note }}</span>
                    <el-button class="peek-close" text :icon="Close" title="关闭" @click.stop="closePeek" />
                  </div>
                  <div v-if="peek.items.length" class="peek-items">
                    <button
                      v-for="item in peek.items"
                      :key="item.index"
                      type="button"
                      :class="['peek-item', { 'is-active': item.index === peek.active }]"
                      @click.stop="choosePeek(item.index)"
                    >
                      <span class="peek-no">{{ item.index + 1 }}</span>
                      <span class="peek-text">{{ item.row || item.text || ('<' + item.tag + '>') }}</span>
                      <span v-if="!item.visible" class="peek-hidden">不可见</span>
                    </button>
                  </div>
                  <div v-if="peek.count > peek.items.length" class="peek-more">
                    共 {{ peek.count }} 个，这里只列出前 {{ peek.items.length }} 个
                  </div>
                  <div v-if="peek.note && peek.count" class="peek-more">{{ peek.note }}</div>
                </div>
              </template>
            </div>
            <div class="step-actions">
              <!-- 「在这一步之后插入」：hover 才出现，点开选断言 / 固定等待 / 刷新。
                   拖拽只能挪已录的步骤，中途补一步才是高频动作，所以入口放在行尾。 -->
              <el-dropdown
                v-if="canEdit"
                class="step-insert-btn"
                trigger="click"
                placement="bottom-end"
                @command="kind => onInsertCommand(kind, step.no)"
              >
                <el-button text :icon="Plus" title="在这一步之后插入断言 / 固定等待 / 刷新" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="assert">插入断言</el-dropdown-item>
                    <el-dropdown-item command="sleep">插入固定等待</el-dropdown-item>
                    <el-dropdown-item command="reload" divided>插入刷新页面</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button
                v-if="editableFields(step).length"
                text
                :icon="EditPen"
                :disabled="!canEdit"
                title="编辑这一步的值"
                @click="startEdit(step)"
              />
              <!-- 收起后只留铅笔与删除：上移 / 下移 不 v-if 硬切，交给 .is-off 做宽度过渡，
                   否则整行文字会瞬移一下，看着很生硬 -->
              <div :class="['step-actions-extra', { 'is-off': stepsCollapsed }]">
                <el-button text :icon="ArrowUp" :disabled="!canEdit" title="上移" @click="moveStep(step.no, 'up')" />
                <el-button text :icon="ArrowDown" :disabled="!canEdit" title="下移" @click="moveStep(step.no, 'down')" />
              </div>
              <!-- 删除常驻（含收起态）：收起才是日常形态，删错一步不该还要先点展开 -->
              <el-button
                text
                class="step-delete-btn"
                :icon="Delete"
                :disabled="!canEdit"
                title="删除这一步"
                @click="removeStep(step.no)"
              />
            </div>
          </li>
        </ol>

        <!-- 断言 / 等待 / 固定等待 模板 -->
        <div v-if="draft" class="draft-card">
          <div class="draft-head">
            <span class="draft-title">{{ draftTitle }}</span>
            <span v-if="draft.afterNo" class="draft-anchor">插在第 {{ draft.afterNo }} 步之后</span>
            <el-button text :icon="Close" @click="closeDraft" />
          </div>

          <!-- 固定等待没有「内容」可解析：不摆文本框和「解析」按钮，
               也去掉只有一个选项的类型下拉，一行秒数就够。 -->
          <div v-if="draft.kind === 'sleep'" class="sleep-row">
            <span class="sleep-label">等待</span>
            <el-input-number
              v-model="draft.seconds"
              :min="0.1"
              :max="MAX_SLEEP_SECONDS"
              :step="0.5"
              controls-position="right"
            />
            <span class="sleep-unit">秒</span>
            <span class="sleep-hint">不看页面状态，直接等这么久</span>
          </div>

          <div v-if="draft.kind !== 'sleep'" class="draft-nl">
            <el-input
              v-model="draft.text"
              type="textarea"
              :rows="2"
              maxlength="300"
              placeholder="用自然语言描述，例如：提交成功的提示出现 / 等到加载遮罩消失 / 页面包含「登录成功」"
            />
            <el-button :loading="draft.parsing" :disabled="!config.model || !draft.text.trim()" @click="parseDraft">
              解析
            </el-button>
          </div>
          <div v-if="draft.kind !== 'sleep'" class="field-hint">
            {{ config.model ? '解析结果会填进下面的表单，改完再插入；插入的永远是表单里的内容。' : '未选择模型：直接在下面的表单里填写。' }}
          </div>

          <div v-if="draft.parsed" class="parsed-preview">
            <div class="parsed-line">
              <span class="parsed-label">解析为</span>
              <span class="parsed-text">{{ intentDescription(draft.parsed) }}</span>
              <span :class="['parsed-tag', { dirty: formDirty }]">{{ formDirty ? '表单已修改，以表单为准' : '已填入表单' }}</span>
            </div>
          </div>

          <el-divider v-if="draft.kind !== 'sleep'" content-position="left">断言内容（可修改）</el-divider>
          <div v-if="draft.kind !== 'sleep'" class="form-grid">
            <el-select v-model="draft.intentType" placeholder="意图类型">
              <el-option
                v-for="type in draftTypes"
                :key="type"
                :label="intentLabel(type)"
                :value="type"
              />
            </el-select>

            <!-- 不需要目标元素的断言（地址 / 整页文字）：与其摆一个灰掉的下拉，不如直接不显示 -->
            <el-select
              v-if="needsTarget(draft.intentType)"
              v-model="draft.target"
              clearable
              placeholder="目标元素"
            >
              <el-option
                v-for="item in aliasItems"
                :key="item.alias"
                :label="item.alias"
                :value="item.alias"
              >
                <span class="alias-option">
                  <span class="alias-option-name">{{ item.alias }}</span>
                  <span class="alias-option-hint" :title="item.desc || ''">{{ item.hint }}</span>
                </span>
              </el-option>
            </el-select>
            <el-button
              v-if="needsTarget(draft.intentType)"
              :type="draft.picking ? 'warning' : 'default'"
              :disabled="!canInspect"
              :title="canInspect ? '' : '浏览器已关闭，无法再拾取元素'"
              @click="pickForDraft"
            >
              {{ draft.picking ? '请在浏览器中点击元素…' : '拾取元素' }}
            </el-button>
            <el-input
              v-if="needsValue(draft.intentType)"
              v-model="draft.value"
              placeholder="文字或值"
              maxlength="500"
            />
          </div>

          <!-- 高级：把「等待」收进断言。断言与等待本来就只差「容不容忍此刻还没成立」，
               所以不再单开一个按钮，默认不勾就是立即判定，勾上才变成轮询。 -->
          <div
            v-if="draft.intentType !== 'sleep'"
            :class="['advanced-box', { 'is-open': draft.pollOpen, 'is-on': draft.allowWait }]"
          >
            <div class="advanced-head" @click="draft.pollOpen = !draft.pollOpen">
              <span :class="['advanced-caret', { 'is-open': draft.pollOpen }]"></span>
              <span class="advanced-title">等到成立</span>
              <span class="advanced-summary">{{ pollSummary }}</span>
              <el-tooltip placement="top-end" effect="dark" :show-after="200">
                <template #content>
                  <div class="advanced-tip">
                    勾上后，条件暂时不成立不算失败，会一直等到「最多」的秒数用尽才报错。
                    <br>被测页面自己会更新时（前端轮询接口）不要勾刷新，越刷越等不到；
                    设备重启、后台任务这类「页面不会自己变」的场景才需要刷。
                  </div>
                </template>
                <span class="advanced-help" @click.stop>?</span>
              </el-tooltip>
            </div>
            <div v-if="draft.pollOpen" class="advanced-body">
              <div class="poll-row">
                <el-checkbox v-model="draft.allowWait">允许等待</el-checkbox>
                <template v-if="draft.allowWait">
                  <span class="poll-label">最多</span>
                  <el-input-number
                    v-model="draft.timeout"
                    :min="1"
                    :max="MAX_WAIT_SECONDS"
                    :step="5"
                    controls-position="right"
                    size="small"
                  />
                  <span class="poll-unit">秒</span>
                </template>
              </div>
              <div v-if="draft.allowWait" class="poll-row">
                <el-checkbox v-model="draft.reload">期间刷新页面</el-checkbox>
                <template v-if="draft.reload">
                  <span class="poll-label">每</span>
                  <el-input-number
                    v-model="draft.interval"
                    :min="1"
                    :max="MAX_POLL_INTERVAL"
                    :step="1"
                    controls-position="right"
                    size="small"
                  />
                  <span class="poll-unit">秒刷一次</span>
                </template>
              </div>
            </div>
          </div>

          <!-- 插入前验证：只回答「此刻成不成立」，口径必须写清楚，否则会被当成回放结论 -->
          <div v-if="draft.verify" :class="['verify-box', verifyTone]">
            <div class="verify-head">
              <span class="verify-dot"></span>
              <span class="verify-title">{{ verifyTitle }}</span>
              <code v-if="draft.verify.locator" class="verify-loc" :title="draft.verify.locator">{{ draft.verify.locator }}</code>
              <button type="button" class="verify-close" title="关掉这条结果" @click="draft.verify = null">×</button>
            </div>
            <div class="verify-row">
              <span class="verify-key">期望</span>
              <span class="verify-val">{{ draft.verify.expected ?? '—' }}</span>
            </div>
            <div class="verify-row">
              <span class="verify-key">实际</span>
              <span class="verify-val">{{ draft.verify.actual ?? '—' }}</span>
            </div>
            <div v-if="draft.verify.detail" class="verify-detail">{{ draft.verify.detail }}</div>
            <div class="verify-note">
              {{ draft.verify.poll
                ? '轮询等待在插入时通常还没到那一步，现在不成立是正常的；它会在回放时按「最长等待」自己等到满足或超时。'
                : '只反映点「验证」这一刻的页面，不是回放结论；断言写在操作之后时，现在不成立是正常的。' }}
            </div>
          </div>

          <div class="draft-actions">
            <el-button
              v-if="draft.intentType !== 'sleep'"
              :loading="draft.verifying"
              :disabled="!canInspect"
              :title="canInspect ? '在浏览器当前页面上判断这条断言现在成不成立' : '浏览器已关闭，无法再看当前页面'"
              @click="verifyDraft"
            >
              验证当前页面
            </el-button>
            <el-button type="primary" :loading="draft.inserting" @click="insertFromForm">{{ insertLabel }}</el-button>
          </div>
        </div>
      </el-card>

      <!-- 生成：面板模式下录制中就藏起来，停止后再出现（面板空间留给步骤） -->
      <el-card v-if="session && (!compactMode || isStopped)" class="panel-card generate-card">
        <template #header>
          <div class="section-header">
            <span class="card-title">生成</span>
          </div>
        </template>

        <div v-if="isStopped" class="checklist">
          <div :class="['check', { ok: steps.length > 0 }]">
            <span class="check-dot"></span>已录制 {{ steps.length }} 个步骤
          </div>
          <div :class="['check', { ok: isStopped }]">
            <span class="check-dot"></span>录制已停止
          </div>
          <div :class="['check', { ok: !blockers.length, warn: blockers.length > 0 }]">
            <span class="check-dot"></span>
            <span class="check-text">
              {{ blockers.length
                ? `第 ${blockers.join('、')} 步定位待确认，将按「最窄候选 + 录制时的序号」生成`
                : (pageGoneCount ? '当前页面上的步骤都有唯一定位' : '所有步骤都有唯一定位') }}
            </span>
            <!-- 页面跑掉的步骤当场框不出来，停在这里还能一条条回去看 -->
            <el-button v-if="reviewCount" text size="small" class="check-action" @click="startLocReview">
              {{ reviewCount }} 步待确认 · 逐条看
            </el-button>
          </div>
          <div v-if="pageGoneCount" class="check warn">
            <span class="check-dot"></span>{{ pageGoneCount }} 个步骤的目标页面已翻走，按录制时定位生成，请回放确认
          </div>
          <div v-if="fragileCount" class="check warn">
            <span class="check-dot"></span>{{ fragileCount }} 个步骤使用结构定位，已标黄，生成后请留意
          </div>
          <div v-if="suspectCount" class="check warn">
            <span class="check-dot"></span>{{ suspectCount }} 个步骤的「唯一」定位其实指向了别的元素，生成不会采用，请先重新框选
          </div>
          <div :class="['check', { ok: !!config.workspace && !!config.model }]">
            <span class="check-dot"></span>
            工作区、模型均已填写<span v-if="!config.targetPath.trim()">（目标文件留空，交给任务书定）</span>
          </div>
        </div>
        <!-- 没停录制时把 4 行检查压成一行，别占掉半屏 -->
        <div v-else class="gen-compact">
          {{ paused ? '暂停中' : '录制中' }} · 已录 <b>{{ steps.length }}</b> 步 · 停止后可生成
          <span v-if="blockers.length" class="gen-compact-bad">· {{ blockers.length }} 处定位待确认</span>
        </div>

        <!-- 回放校验：跑的是内存里的录制 IR，不是生成的脚本，所以暂停或停止后就能跑，
             不需要先经过「生成」。按钮放在这里，不要再塞回生成结果卡片里。 -->
        <div v-if="canReplay && steps.length" class="replay-block">
          <div class="replay-row">
            <el-button size="small" :loading="replaying" @click="runReplay">回放校验</el-button>
            <span class="field-hint">
              {{ paused
                ? '暂停中也能跑：另开一个无头浏览器从第 1 步重演，不动你当前这个窗口'
                : '在浏览器里把录制重跑一遍，只验定位还命中不命中，不需要先生成' }}
            </span>
          </div>
          <div v-if="replayStale" class="replay-stale">
            {{ replayExtra ? `回放之后又录了 ${replayExtra} 步` : '步骤有改动' }}，这次结论只覆盖回放时的 {{ replayResult.total }} 步
          </div>
          <div v-if="replayResult" class="gen-replay">
            <button type="button" class="gen-replay-head" @click="replayOpen = !replayOpen">
              <span :class="['gen-replay-dot', replayResult.ok ? 'ok' : 'bad']"></span>
              <span :class="['gen-replay-title', { bad: !replayResult.ok }]">{{ replayTitle }}</span>
              <span v-if="replayResult.steps.length" class="gen-replay-toggle">
                {{ replayOpen ? '收起明细' : '展开明细' }}
              </span>
            </button>
            <div v-if="!replayResult.ok && replayResult.error" class="gen-replay-error">{{ replayResult.error }}</div>
            <div v-if="config.framework === 'robot-selenium'" class="gen-replay-note">
              Robot 生成的脚本需要 Selenium 环境运行，这里只校验定位是否仍然有效。
            </div>
            <ul v-if="replayOpen" class="gen-replay-steps">
              <li v-for="item in replayResult.steps" :key="item.no" :class="{ bad: !item.ok }">
                <span class="gen-replay-no">{{ item.no }}</span>
                <span class="gen-replay-type">{{ stepKindLabel(item.type) }}</span>
                <span class="gen-replay-alias" :title="item.alias || item.label || item.url || ''">
                  {{ item.alias || item.label || item.url || '—' }}
                </span>
                <code v-if="item.used" class="gen-replay-loc" :title="item.used.syntax">
                  {{ item.used.syntax }}
                </code>
                <span v-else class="gen-replay-loc is-empty">—</span>
                <span v-if="item.used && item.used.nth" class="gen-replay-nth">
                  匹配 {{ item.used.matches }} 取第 {{ item.used.nth }}
                </span>
                <span v-if="item.poll" class="gen-replay-poll">
                  轮询 {{ item.poll.elapsed }}s / 刷新 {{ item.poll.attempts - 1 }} 次
                </span>
                <span v-if="!item.ok && item.error" class="gen-replay-why">{{ item.error }}</span>
              </li>
            </ul>
          </div>
        </div>

        <!-- 生成中：进度条取代按钮。状态来自服务端，主界面和录制面板看到的是同一份，
             不会出现「面板点完、这里还能再点一次」。 -->
        <div v-if="generating" class="gen-progress">
          <div class="gen-progress-head">
            <span class="gen-progress-label">{{ emitLabel }}</span>
            <span class="gen-progress-pct">{{ emitPercent }}%</span>
          </div>
          <el-progress :percentage="emitPercent" :show-text="false" :stroke-width="6" color="#0071e3" />
          <div class="gen-progress-foot">
            <span class="gen-progress-detail">{{ emitDetail }}</span>
            <span class="gen-progress-time">已用 {{ elapsedText }}</span>
          </div>
        </div>
        <!-- 写入的唯一入口在任务书里（「按此执行」）。这里只负责出任务书，
             避免又出现一条绕过规划、直接从零写的路。 -->
        <el-button
          v-else-if="!planState && !shownPlanError"
          class="generate-btn"
          type="primary"
          :disabled="!canPlan"
          @click="runPlan(false)"
        >
          生成任务书
        </el-button>
        <!-- 任务书只有这一个入口：卡片是 flex:none，永远贴在面板底部，不用滚。
             正文在浮层里读（见下方 Teleport），这里只交代「写到哪、几步」。 -->
        <div v-else class="plan-ready">
          <span :class="['check-dot', shownPlanError ? '' : 'ok']"></span>
          <div class="plan-ready-main">
            <div class="plan-ready-title">
              <!-- 重新规划失败时旧版还在：说「没生成出来」会误导，得说清用的是哪一版 -->
              <template v-if="shownPlanError && planData">重新规划失败，仍是上一版</template>
              <template v-else-if="shownPlanError">任务书没生成出来</template>
              <template v-else>
                任务书已就绪 · {{ planSteps.length }} 步<template v-if="planReusable.length"> · 复用 {{ planReusable.length }} 项</template>
              </template>
            </div>
            <div class="plan-ready-sub">
              <template v-if="shownPlanError">{{ shownPlanError.message }}</template>
              <template v-else-if="planTarget">写入 <code>{{ planTarget }}</code></template>
              <template v-else>写入位置还没定，打开任务书确认</template>
            </div>
          </div>
          <el-button size="small" :type="shownPlanError && !planData ? 'default' : 'primary'" @click="openPlanSheet">
            {{ shownPlanError && !planData ? '看错误' : '打开任务书' }}
          </el-button>
        </div>
        <div v-if="!generating && !planState && !canPlan && generateBlockReason" class="gen-block-reason">
          <span class="gen-block-dot"></span>{{ generateBlockReason }}
        </div>
        <div v-else-if="!generating && !planState" class="field-hint">
          任务书只读项目、不写文件；你确认之后才会写入目标文件。
        </div>
        <div v-else-if="!generating" class="field-hint">
          写入只在任务书里触发；改了录制步骤需要重新生成任务书。
        </div>

        <div v-if="shownError" class="gen-error">
          <div class="gen-error-title">{{ shownError.message }}</div>
          <ul v-if="shownError.details && shownError.details.length" class="gen-error-list">
            <li v-for="(item, idx) in shownError.details" :key="idx">{{ detailText(item) }}</li>
          </ul>
        </div>

        <div v-if="shownResult" class="gen-result">
          <div class="gen-result-title">已写入 {{ shownResult.path }}</div>
          <div class="field-hint">
            {{ shownResult.validated ? '校验通过' : '未校验' }} · 框架 {{ frameworkName(shownResult.framework) }}
          </div>
          <div v-if="shownResult.degraded && shownResult.degraded.length" class="gen-degraded">
            <div class="gen-degraded-title">{{ shownResult.degraded.length }} 步使用了降级定位，请回放确认</div>
            <ul>
              <li v-for="item in shownResult.degraded" :key="item.no">
                第 {{ item.no }} 步「{{ item.alias }}」：匹配 {{ item.matches }} 个，取第 {{ (item.nth ?? 0) + 1 }} 个
              </li>
            </ul>
          </div>
          <div v-if="shownResult.outside && shownResult.outside.length" class="gen-degraded">
            <div class="gen-degraded-title">
              目标文件以外还有 {{ shownResult.outside.length }} 个文件被改动，已保留在工作区（未自动还原）
            </div>
            <ul>
              <li v-for="item in shownResult.outside" :key="item">{{ item }}</li>
            </ul>
            <div class="field-hint">确认不是你要的改动，可用 git checkout -- &lt;路径&gt; 恢复。</div>
          </div>
          <div class="gen-result-actions">
            <el-button size="small" type="primary" plain @click="$router.push({ path: '/recording/records', query: { id: shownResult.generation_id } })">
              查看生成记录
            </el-button>
            <span class="field-hint">预览文件内容、提交到 Git 或撤销这次写入</span>
          </div>
        </div>
      </el-card>

      <!-- 任务书正文：长文档（表格 + 风险 + 意见）塞不进 420–600 宽的编排面板，
           入口在生成卡里，这里只负责铺开读。浮层打开时后端会把面板窗口铺宽——
           panel=1 时本页就是那个窄窗口，窗口内的 96vw 再大也大不过窗口。 -->
      <Teleport to="body">
        <div v-if="planSheetOpen" class="plan-sheet-mask" @mousedown.self="closePlanSheet">
          <section class="plan-sheet" role="dialog" aria-modal="true" aria-labelledby="plan-sheet-title">
            <header class="plan-sheet-head">
              <span id="plan-sheet-title" class="card-title">任务书</span>
              <span v-if="planData" class="plan-head-meta">
                {{ planSteps.length }} 步<template v-if="planReusable.length"> · 复用 {{ planReusable.length }} 项</template>
              </span>
              <span class="plan-sheet-tip">选中正文任意一段，可以只针对它提意见</span>
              <button type="button" class="plan-sheet-close" title="关闭（Esc）" @click="closePlanSheet">✕</button>
            </header>

            <!-- 错误和正文不互斥：重新规划失败时上一版任务书还在，
                 这时候把旧版藏起来等于让人两手空空地重新点「生成」。 -->
            <div v-if="shownPlanError" :class="['plan-sheet-error', { 'is-solo': !planData }]">
              <div class="gen-error">
                <div class="gen-error-title">{{ shownPlanError.message }}</div>
                <ul v-if="shownPlanError.details && shownPlanError.details.length" class="gen-error-list">
                  <li v-for="(item, idx) in shownPlanError.details" :key="idx">{{ detailText(item) }}</li>
                </ul>
              </div>
              <div class="plan-actions">
                <el-button size="small" type="primary" :loading="planRunning" @click="runPlan(true)">重试规划</el-button>
                <el-button v-if="!planData" size="small" text @click="closePlanSheet">关掉</el-button>
              </div>
            </div>

            <div v-if="planData" class="plan-sheet-body">
              <div ref="planBody" class="plan-sheet-main" @mouseup="onPlanSelect" @mousedown="clearSelTip">
                <div v-if="planStale" class="plan-stale">
                  录制步骤已经变了，这份任务书对不上。请重新规划后再执行。
                </div>

                <div class="plan-block">
                  <div class="plan-block-title">项目风格</div>
                  <div class="plan-kv"><span>runner</span><b>{{ planStyle.runner || '—' }}</b></div>
                  <div class="plan-kv"><span>断言</span><b>{{ planStyle.assertion || '—' }}</b></div>
                  <div v-if="planStyle.import_sample" class="plan-kv">
                    <span>import</span><code>{{ planStyle.import_sample }}</code>
                  </div>
                  <div v-if="planStyle.notes" class="plan-style-note">{{ planStyle.notes }}</div>
                </div>

                <div class="plan-block">
                  <div class="plan-block-title">可复用的封装</div>
                  <div v-if="!planReusable.length" class="field-hint">
                    项目里没有找到能覆盖这些步骤的现成封装，这次全部手写。
                  </div>
                  <ul v-else class="plan-reuse">
                    <li v-for="(item, idx) in planReusable" :key="idx">
                      <code>{{ item.symbol || item.file || item }}</code>
                      <span v-if="item.file && item.symbol" class="plan-reuse-file">{{ item.file }}</span>
                      <span v-if="item.usage" class="plan-reuse-use">{{ item.usage }}</span>
                    </li>
                  </ul>
                </div>

                <div class="plan-block">
                  <div class="plan-block-title">逐步做法（{{ planSteps.length }} 步）</div>
                  <table class="plan-steps">
                    <tr v-for="step in planSteps" :key="step.no">
                      <td class="plan-step-no">{{ step.no }}</td>
                      <td :class="['plan-step-action', { reuse: step.action === 'reuse' }]">
                        {{ step.action === 'reuse' ? '复用' : '手写' }}
                      </td>
                      <td class="plan-step-note">{{ step.note || step.target || '—' }}</td>
                    </tr>
                  </table>
                </div>

                <div v-if="planRisks.length" class="plan-block">
                  <div class="plan-block-title">风险</div>
                  <ul class="plan-risks">
                    <li v-for="(item, idx) in planRisks" :key="idx">{{ item }}</li>
                  </ul>
                </div>

                <div v-if="planNeeds.length" class="plan-block">
                  <div class="plan-block-title">还想改这些文件（这次没有权限，只记录）</div>
                  <ul class="plan-risks">
                    <li v-for="(item, idx) in planNeeds" :key="idx">
                      {{ item.path }}<span v-if="item.reason"> — {{ item.reason }}</span>
                    </li>
                  </ul>
                </div>
              </div>

              <aside class="plan-sheet-side">
                <div class="plan-side-scroll">
                  <div class="plan-block">
                    <div class="plan-block-title">
                      写入位置
                      <span v-if="planTargetChosen" class="plan-path-badge">AI 按项目约定选的</span>
                    </div>
                    <div v-if="!planTargetEditing" class="plan-target">
                      <code>{{ planTarget || '—' }}</code>
                      <el-button
                        class="plan-target-edit-btn"
                        text
                        size="small"
                        :icon="EditPen"
                        title="改写入位置"
                        @click="startEditPlanTarget"
                      />
                    </div>
                    <div v-else class="plan-target-edit">
                      <el-input
                        v-model="planTargetDraft"
                        size="small"
                        placeholder="工作区内相对路径，例如 tests/login.spec.ts"
                        @keyup.enter="savePlanTarget"
                      />
                      <div class="plan-target-edit-btns">
                        <el-button size="small" type="primary" @click="savePlanTarget">确定</el-button>
                        <el-button size="small" text @click="cancelEditPlanTarget">取消</el-button>
                      </div>
                    </div>
                    <div v-if="planTargetError" class="plan-target-error">{{ planTargetError }}</div>
                    <div v-if="planResult.reason" class="plan-suggest-reason">{{ planResult.reason }}</div>
                    <div v-if="planTargetChanged" class="plan-target-changed">
                      位置是你改的。任务书里的相对 import 是按规划给的位置算的，可能已经不对；
                      不确定就点「重新规划」，让 AI 按新位置重算一遍。
                    </div>
                    <div v-if="planSuggestion && !planTargetChosen" class="plan-suggest">
                      <div class="plan-suggest-title">
                        规划建议改到 <code>{{ planSuggestion }}</code>
                      </div>
                      <el-button size="small" type="warning" plain @click="adoptSuggestion">采用这个路径</el-button>
                    </div>
                    <div class="field-hint">
                      这是本次唯一允许写入的文件。点「按此执行」才算授权，不点什么都不写。
                    </div>
                  </div>

                  <div class="plan-notes">
                    <div class="plan-block-title">
                      你的意见
                      <span v-if="planNotes.length" class="plan-notes-count">{{ planNotes.length }} 条</span>
                    </div>
                    <div v-if="!planNotes.length" class="field-hint">
                      没有意见就直接执行；有意见会在写入时一并交给 AI。
                    </div>
                    <div v-for="note in planNotes" :key="note.id" class="plan-note">
                      <div v-if="note.quote" class="plan-quote">「{{ note.quote }}」</div>
                      <div class="plan-note-body">{{ note.comment }}</div>
                      <button type="button" class="plan-note-del" @click="removeNote(note.id)">删除</button>
                    </div>
                    <el-button size="small" text @click="openNote('')">补充整体意见</el-button>
                  </div>
                </div>

                <div class="plan-side-foot">
                  <div class="plan-actions">
                    <el-button size="small" :loading="planRunning" :disabled="generating" @click="runPlan(true)">
                      重新规划
                    </el-button>
                    <el-button
                      size="small"
                      type="primary"
                      :loading="writing"
                      :disabled="!canExecute"
                      @click="executePlan(false)"
                    >
                      按此执行
                    </el-button>
                  </div>
                  <div v-if="!canExecute && executeBlockReason" class="gen-block-reason">
                    <span class="gen-block-dot"></span>{{ executeBlockReason }}
                  </div>
                  <div v-else-if="!writing" class="field-hint">
                    执行只写目标文件；校验失败会保留文件并把错误告诉你。
                  </div>
                </div>
              </aside>
            </div>
          </section>
        </div>
      </Teleport>
    </div>
    </div>

      <Teleport to="body">
        <button
          v-if="selTip.visible"
          type="button"
          class="plan-sel-tip"
          :style="{ left: selTip.x + 'px', top: selTip.y + 'px' }"
          @mousedown.prevent
          @click="openNote(selTip.text)"
        >
          就这段写意见
        </button>
      </Teleport>

      <el-dialog v-model="noteDialog.visible" title="对任务书的意见" width="520px" append-to-body>
        <div v-if="noteDialog.quote" class="plan-quote">「{{ noteDialog.quote }}」</div>
        <el-input
          v-model="noteDialog.comment"
          type="textarea"
          :rows="4"
          maxlength="800"
          show-word-limit
          placeholder="例如：这一步必须用 getByRole，不要用 getByText"
        />
        <template #footer>
          <el-button @click="noteDialog.visible = false">取消</el-button>
          <el-button type="primary" @click="saveNote">添加意见</el-button>
        </template>
      </el-dialog>

      <PathPickerDialog
        v-model="picker.visible"
        :mode="picker.mode"
        :workspace="config.workspace.trim()"
        :framework="config.framework"
        :start-path="picker.start"
        :default-name="suggestedFileName"
        @loaded="onRootLoaded"
        @select="onPathSelected"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Aim, ArrowUp, ArrowDown, CircleCheck, Clock, Close, Connection, Delete, Document, EditPen, Expand, Fold, FolderOpened, Plus, Refresh, Tickets, Timer, VideoCamera, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import Sortable from 'sortablejs'
import api from '../api'
import PathPickerDialog from '../components/PathPickerDialog.vue'

// 与后端保持一致的常量（backend/app/services/recording/intent.py、locators.py）
// short 用于配置区的分段控制器：两个取值要在一行里放下，全称太长会把分段挤成两行。
const FRAMEWORKS = [
  { value: 'robot-selenium', label: 'Robot Framework（SeleniumLibrary）', short: 'Robot Framework' },
  { value: 'playwright-ts', label: 'Playwright TypeScript', short: 'Playwright TS' }
]
const ASSERT_TYPES = ['visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled', 'url_contains', 'page_text_contains']
// 合并前的老类型 wait_visible / wait_hidden 只会从历史数据里读到，不再出现在下拉里，
// 落到表单时由 LEGACY_WAIT 拆成 visible / hidden + 「允许等待」。
const SLEEP_TYPES = ['sleep']
const NEEDS_TARGET = new Set(['visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled', 'wait_visible', 'wait_hidden'])
const NEEDS_VALUE = new Set(['text_contains', 'text_equals', 'value_equals', 'url_contains', 'page_text_contains'])
// 「是不是等待」不再看类型，而是看有没有给最长等待：给了就等到成立，没给就是立即判定。
const MAX_WAIT_SECONDS = 3600
const MAX_POLL_INTERVAL = 300
const MAX_SLEEP_SECONDS = 60
const INTENT_LABELS = {
  visible: '元素可见', hidden: '元素隐藏', text_contains: '文字包含', text_equals: '文字等于',
  value_equals: '值等于', enabled: '元素可用', url_contains: '地址包含',
  page_text_contains: '页面包含文字', wait_visible: '等待出现', wait_hidden: '等待消失', sleep: '固定等待'
}
const STEP_LABELS = {
  goto: '打开页面', reload: '刷新', click: '点击', fill: '输入', select: '选择', press: '按键',
  dialog: '弹窗', new_page: '新标签页',
  assert: '断言', wait: '等待', sleep: '固定等待'
}
const STEP_TONES = { goto: 'info', reload: 'warning', click: 'primary', fill: 'primary', select: 'primary', press: 'primary', dialog: 'warning', new_page: 'info', assert: 'success', wait: 'warning', sleep: 'danger' }
const STATE_LABELS = {
  idle: '未开始', starting: '启动中', recording: '录制中', stopping: '停止中', stopped: '已停止',
  cancelled: '已取消', failed: '失败', idle_timeout: '超时已停止', closed: '浏览器已关闭'
}
const STORAGE_KEY = 'autocase.recording.config'
// 与后端 locators.py 保持一致：Playwright 与 RF 两组的优先级，以及结构定位（需标黄）。
const PW_ORDER = ['testid', 'role', 'row_scoped', 'label', 'placeholder', 'role_bare', 'text',
  'class_part', 'css']
// rf_row_scoped 排在 rf_xpath 之后：它本身是用文本 xpath 组出来的，
// 只有「文本在页面上不唯一」时才比裸文本强，不能压过一条本来就唯一的语义定位。
// rf_id_loose 是「带数字、分不清是业务键还是自增序号」的 id，让位给 testid / 干净 id /
// name / aria / label，但排在文本 xpath 之前——id 是元素身份，比文本匹配更接近元素本身。
// rf_class_part 是 class 里剥掉构建哈希的名字（_itemMenu_）：比文本弱，但比 css 结构路径强。
const RF_ORDER = ['rf_testid', 'rf_id', 'rf_name', 'rf_aria', 'rf_label', 'rf_id_loose',
  'rf_xpath', 'rf_row_scoped', 'rf_class_part', 'rf_css']
const FRAGILE_KINDS = ['css', 'rf_css']

const config = reactive(loadConfig())

// ---- 配置行：默认只读，点开才就地展开 ----
// 六个输入框常驻会把 400px 的侧栏塞成注册表单；默认只显示「值」，
// 点某一行才换成输入框。只记一个字段名，同一时刻只允许一行处于编辑态。
const cfgEditing = ref(null)
const cfgEditRefs = {}
function setEditRef(name, el) {
  if (el) cfgEditRefs[name] = el
}
async function beginEdit(name) {
  cfgEditing.value = name
  // el-input 的 focus() 要等它真的挂上去；同一 tick 里调用会打在旧 DOM 上。
  await nextTick()
  cfgEditRefs[name]?.focus?.()
}
function endEdit() {
  cfgEditing.value = null
}
// 两种「围绕录制」的布局：
// - panel=1：split 模式下的独立面板窗口，后端用 CDP 和被测页面左右并排；
// - embed=1：嵌入模式，本页就是唯一窗口，左边 iframe 加载 target，右边是编排面板。
const route = useRoute()
const panelMode = computed(() => route.query.panel === '1')
const embedMode = computed(() => route.query.embed === '1')
const compactMode = computed(() => panelMode.value || embedMode.value)
const targetUrl = computed(() => (typeof route.query.target === 'string' ? route.query.target : ''))
const configExpanded = ref(false)
const panelTitle = computed(() => {
  if (!compactMode.value) return ''
  const base = paused.value && isRecording.value
    ? '已暂停'
    : (STATE_LABELS[session.value?.status || 'idle'] || '录制')
  return targetUrl.value ? `${base} · ${shortHost(targetUrl.value)}` : base
})
const modelOptions = ref([])
const detect = ref(null)
const detecting = ref(false)
// 高级设置（Python 解释器）默认收起：绝大多数工作区能自动找到 .venv。
const showAdvanced = ref(false)

// 面板宽度可拖拽调整；收起后 left iframe 吃掉几乎整个窗口。
const PANEL_WIDTH_KEY = 'autocase.recording.panelWidth'
const panelCollapsed = ref(false)
const panelWidth = ref(loadPanelWidth())
const resizing = ref(false)

// 「收起」：精简步骤行（只留拖拽排序、铅笔编辑与删除）并把面板一键收窄，替代手动拖动分栏。
// 默认就是收起——实测窄面板 + 精简行最好用；需要上移 / 下移再点「展开」。
// 独立页面（/recording）没有这个开关，保持展开。
const LEAN_PANEL_WIDTH = 420
const stepsCollapsed = ref(compactMode.value)
let widthBeforeLean = 0

function maxPanelWidth() {
  return Math.max(340, Math.min(900, window.innerWidth - 900))
}

// 默认收起时面板也按收起的宽度打开：只收行不收宽度，和按钮上说的「面板变窄」不一致。
if (stepsCollapsed.value && embedMode.value) {
  widthBeforeLean = panelWidth.value
  panelWidth.value = Math.min(panelWidth.value, LEAN_PANEL_WIDTH, maxPanelWidth())
}

function toggleStepsCollapsed() {
  stepsCollapsed.value = !stepsCollapsed.value
  if (!embedMode.value) return
  if (stepsCollapsed.value) {
    widthBeforeLean = panelWidth.value
    // 只收不放：本来就比 420 窄的话保持原宽，不能因为「收起」反而变宽。
    panelWidth.value = Math.min(panelWidth.value, LEAN_PANEL_WIDTH, maxPanelWidth())
  } else {
    panelWidth.value = Math.min(widthBeforeLean || 600, maxPanelWidth())
  }
}

function loadPanelWidth() {
  try {
    const saved = Number(localStorage.getItem(PANEL_WIDTH_KEY))
    if (saved >= 340 && saved <= 900) return saved
  } catch {
    // 隐私模式下读不到，用默认值。
  }
  return 600
}

function startResize(event) {
  // 收起时不允许拖拽：宽度由「收起 / 展开」控制，避免拖到一半元素被挤变形。
  if (stepsCollapsed.value) return
  event.preventDefault()
  const handle = event.currentTarget
  // 指针一旦移到 iframe 上方，事件就进了 iframe 的文档，父窗口收不到。
  // 所以用 pointer capture 把 pointermove 固定在手柄上，并在拖动期间禁用 iframe 的命中测试。
  try {
    handle.setPointerCapture(event.pointerId)
  } catch {
    // 拿不到 capture 时靠下面的 pointer-events 兜底
  }
  resizing.value = true
  const move = (e) => {
    // 面板最宽 900，同时保证被测页面至少还剩 900（页面优先，面板让位）。
    const room = Math.max(340, Math.min(900, window.innerWidth - 900))
    panelWidth.value = Math.min(Math.max(window.innerWidth - e.clientX, 340), room)
  }
  const stop = () => {
    resizing.value = false
    handle.removeEventListener('pointermove', move)
    handle.removeEventListener('pointerup', stop)
    handle.removeEventListener('pointercancel', stop)
    try {
      localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidth.value))
    } catch {
      // 忽略持久化失败
    }
  }
  handle.addEventListener('pointermove', move)
  handle.addEventListener('pointerup', stop)
  handle.addEventListener('pointercancel', stop)
}

function shortHost(url) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function applyPanelQuery() {
  if (!compactMode.value) return
  const query = route.query
  // 外壳/面板可能是新的浏览器 profile，localStorage 为空，只能靠后端带过来的参数预填。
  if (typeof query.model === 'string' && query.model) config.model = query.model
  if (typeof query.workspace === 'string' && query.workspace) config.workspace = query.workspace
  if (typeof query.framework === 'string' && FRAMEWORKS.some(item => item.value === query.framework)) {
    config.framework = query.framework
  }
  // 目标文件/标题/解释器也必须在开始录制时带过来：录制窗口是独立 profile，
  // 用户在主窗口填的这三项不会出现在这里；目标文件为空时生成按钮会一直不可用。
  if (typeof query.target_path === 'string' && query.target_path) config.targetPath = query.target_path
  if (typeof query.title === 'string' && query.title) config.title = query.title
  if (typeof query.python === 'string' && query.python) config.python = query.python
  const startUrl = typeof query.base_url === 'string' ? query.base_url : ''
  const target = typeof query.target === 'string' ? query.target : ''
  const filled = startUrl || target
  if (filled) config.baseUrl = filled
}
applyPanelQuery()

const session = ref(null)
const starting = ref(false)
const stopping = ref(false)
const closingBrowser = ref(false)
// 插入「刷新页面」用的是命令通道（后端要真的刷一次浏览器），和断言草稿分开计数。
const addingReload = ref(false)
const draft = ref(null)
// 刚插入的那一步短暂高亮，用来「指认」插入结果
const flashingNo = ref(0)
let flashTimer = 0
// 生成状态以服务端为准：主界面和录制面板是两个独立窗口，本地 loading 互相看不见，
// 会出现「一边点完，另一边还能点」。localPending 只补位「点击 → 下一次轮询」这一秒。
const localPending = ref(false)
const genError = ref(null)
const genResult = ref(null)
// 任务书：规划是独立的一次请求，结果留在页面上。写脚本必须从这里点「按此执行」，
// 界面上不再提供「不规划直接写」的入口。
const planState = ref(null)      // { result, sig, at }
const planPending = ref(false)   // 规划请求在飞（服务端进度起来之前补位）
const planError = ref(null)
const planNotes = ref([])        // 用户选中任务书片段写的意见
// 任务书是长文档（表格 + 风险 + 意见），面板只有 420–600 宽、正文还被限高到 200px，
// 塞在里面只能来回滚。所以正文搬到浮层：面板里留一行状态，宽屏里读、右侧拍板。
const planSheetOpen = ref(false)
const planBody = ref(null)
const noteDialog = reactive({ visible: false, quote: '', comment: '' })
const selTip = reactive({ visible: false, x: 0, y: 0, text: '' })
let noteSeq = 0
// 回放校验：跑的是内存里的录制 IR，和有没有生成产物无关。
// 停止后随时可跑；暂停时步骤集也不会再变，一样可以跑。
const replaying = ref(false)
const replayResult = ref(null)
const replayOpen = ref(false)

let pollTimer = null
let pollMs = 0
// 页面被 keep-alive 缓存时不该继续打接口；回到页面再恢复。
let pollEnabled = true
let tickTimer = null

// ---- 配置持久化（仅浏览器本地便捷设置，不含敏感信息）----
// targetPath 故意不持久化：它跟具体项目绑定，留着上一次的值等于替用户把路径锁死，
// 下次换个项目还是那个路径，又回到「用户盲填、模型不敢改」的老问题。每次默认留空。
function loadConfig() {
  const defaults = { baseUrl: '', workspace: '', framework: 'robot-selenium', targetPath: '', model: '', title: '', python: '' }
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    delete saved.targetPath
    return { ...defaults, ...saved }
  } catch {
    return defaults
  }
}

watch(config, (value) => {
  try {
    const rest = { ...value }
    delete rest.targetPath
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rest))
  } catch {
    // 隐私模式或存储被禁用时忽略，页面照常工作。
  }
}, { deep: true })

const targetPlaceholder = computed(() => (config.framework === 'robot-selenium'
  ? '留空由任务书决定，也可以填 tests/login.robot'
  : '留空由任务书决定，也可以填 tests/login.spec.ts'))

// ---- 路径选择（浏览工作区目录 / 目标文件）----
// 浏览器拿不到本地绝对路径，所以由后端在 AUTOMATION_WORKSPACE_ROOT 内列目录，前端只填相对路径。
const picker = reactive({ visible: false, mode: 'dir', start: '' })
const workspaceRoot = ref('')
const EXT_BY_FRAMEWORK = { 'robot-selenium': '.robot', 'playwright-ts': '.spec.ts' }
const KNOWN_EXTS = ['.spec.ts', '.test.ts', '.robot', '.resource', '.ts']
const suggestedFileName = computed(() => fileSlug(config.title) || 'recorded_flow')

function fileSlug(text) {
  return String(text || '').trim().replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 50)
}

function dirnameOf(path) {
  const parts = String(path || '').trim().split('/')
  parts.pop()
  return parts.join('/')
}

function openWorkspacePicker() {
  // 从当前工作区的上级开始浏览，方便对比同级目录。
  picker.mode = 'dir'
  picker.start = dirnameOf(config.workspace.trim())
  picker.visible = true
}

function openTargetPicker() {
  if (!config.workspace.trim()) {
    ElMessage.warning('请先选择工作区，再选择目标文件')
    return
  }
  picker.mode = 'file'
  picker.start = dirnameOf(config.targetPath.trim())
  picker.visible = true
}

function onRootLoaded({ root }) {
  workspaceRoot.value = root || ''
}

function onPathSelected({ path }) {
  if (picker.mode === 'dir') {
    const changed = path !== config.workspace.trim()
    config.workspace = path
    if (changed) detect.value = null
    // 目标文件不再按目录名自动猜：留空就是「让任务书按项目约定定」，
    // 偷偷填一个 tests/xxx 反而会把它锁死成用户指定，又回到盲填路径的老路。
    detectWorkspace()
    return
  }
  config.targetPath = path
}

// 切换框架时同步目标文件扩展名，避免出现「Robot 框架 + .spec.ts」这种组合。
watch(() => config.framework, (next) => {
  const current = (config.targetPath || '').trim()
  if (!current) return
  const matched = KNOWN_EXTS.filter(ext => current.endsWith(ext)).sort((a, b) => b.length - a.length)[0]
  const expected = EXT_BY_FRAMEWORK[next]
  if (!matched || !expected || current.endsWith(expected)) return
  config.targetPath = current.slice(0, -matched.length) + expected
})

// ---- 会话状态 ----
const isRecording = computed(() => session.value?.status === 'recording')
const isStopped = computed(() => ['stopped', 'idle_timeout', 'closed'].includes(session.value?.status))
const canEdit = computed(() => isRecording.value || isStopped.value)
// 浏览器只在停止后才关闭（或用户手动关），所以「拾取 / 验证」的可用性跟浏览器走，不跟录制状态走。
const browserOpen = computed(() => !!session.value?.browser_open)
const canInspect = computed(() => isRecording.value || (browserOpen.value && isStopped.value))
// 停止后浏览器还开着，但用户可能已经不想要那个窗口了，需要一个显式出口。
const canCloseBrowser = computed(() => browserOpen.value && isStopped.value)
const steps = computed(() => session.value?.steps || [])
const aliases = computed(() => session.value?.aliases || [])
// 下拉里要能分清「删除」和「删除#2」：别名 + 它来自第几步。
const aliasItems = computed(() => session.value?.alias_items || aliases.value.map((alias) => ({ alias, hint: '', desc: '' })))
const picking = computed(() => !!session.value?.pick)
const paused = computed(() => !!session.value?.paused)
const pausing = ref(false)
const stateLabel = computed(() => {
  if (paused.value && isRecording.value) return '已暂停'
  return STATE_LABELS[session.value?.status || 'idle'] || '未知'
})

const blockers = computed(() => steps.value
  .filter(s => s.target && ['ambiguous', 'invalid'].includes(effectiveStatus(s)))
  .map(s => s.no))
const fragileCount = computed(() => steps.value.filter(s => s.target && effectiveStatus(s) === 'fragile').length)
// 看着唯一、其实指向了别的元素：这类步骤生成时拿不到定位，必须让用户先看见再处理。
const suspectCount = computed(() => steps.value.filter(s => s.target && effectiveStatus(s) === 'suspect').length)
// 录制时在登录页、停止时已经跳到聊天页的步骤：没法在当前页面复核，但不该报成不唯一。
const pageGoneCount = computed(() => steps.value.filter(s => s.target && s.target.page_gone).length)

// 录完之后还能逐条过一遍的步骤：当场没来得及选的、页面跑掉没框出来的。
// 判据和录制中那套一致，只是额外把 page_gone / unverified 也算进来——
// 这两种恰恰是「页面跑掉了」，正是要能回去看的。
function reviewable(step) {
  const target = step.target
  if (!target) return false
  // 亲手确认过的不用再看
  if (target.chosen && target.chosen.desc) return false
  // 点完自己就没了的（下拉项、菜单项）：那一页回去也没有这个元素
  if (target.vanished_after) return false
  return needsLocConfirm(step) || !!target.page_gone || !!target.unverified
}
const reviewCount = computed(() => steps.value.filter(reviewable).length)

// ---- 生成进度（服务端状态，两个窗口共用）----
const emit = computed(() => session.value?.emit || {})
const emitRunning = computed(() => emit.value.status === 'running')
const planRunning = computed(() => planPending.value || (emitRunning.value && emit.value.mode === 'plan'))
const writing = computed(() => localPending.value || (emitRunning.value && emit.value.mode !== 'plan'))
const generating = computed(() => planRunning.value || writing.value)

// 阶段百分比只给基准值，模型写脚本的耗时装不出来：按 stage_at 指数逼近 cap，
// 前 20 秒爬得快、越接近上限越慢，既不卡死也不会提前满格。
const nowTick = ref(Date.now() / 1000)
const emitPercent = computed(() => {
  const e = emit.value
  if (!emitRunning.value && generating.value) return 4
  if (!e || e.status !== 'running') return Math.round(e?.percent || 0)
  const base = e.percent || 0
  const cap = e.cap || base
  const t = Math.max(0, nowTick.value - (e.stage_at || nowTick.value))
  return Math.min(cap, Math.round(base + (cap - base) * (1 - Math.exp(-t / 25))))
})
const emitLabel = computed(() => {
  if (!emitRunning.value && generating.value) return planRunning.value ? '正在准备规划' : '正在准备写入'
  return emit.value.label || '正在准备'
})
const emitDetail = computed(() => {
  if (!emitRunning.value && generating.value) {
    return planRunning.value ? '正在提交规划请求' : '正在提交写入请求'
  }
  if (emit.value.mode === 'plan') return emit.value.detail || '只读项目，通常需要 1~2 分钟'
  return emit.value.detail || '写入的只有目标文件；校验失败会保留文件'
})
const elapsedText = computed(() => {
  const start = emit.value.started_at
  if (!start) return '0s'
  const end = emit.value.finished_at || nowTick.value
  const seconds = Math.max(0, Math.round(end - start))
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, '0')}s`
})

// 另一个窗口跑完 / 跑挂时，这里也要能看到同一条结果，而不是只剩一个「还能点」的按钮。
const shownError = computed(() => {
  if (genError.value) return genError.value
  // 规划失败要显示在任务书卡片里，别和写入失败混在一起。
  return emit.value.status === 'failed' && emit.value.mode !== 'plan' ? emit.value.error : null
})
const shownPlanError = computed(() => {
  if (planError.value) return planError.value
  return emit.value.status === 'failed' && emit.value.mode === 'plan' ? emit.value.error : null
})
const shownResult = computed(() => {
  const local = genResult.value
  if (!local) {
    const remote = emit.value.status === 'done' ? emit.value.result : null
    // 规划结束也是 done，但它没有 path：不能拿它冒充一次写入结果。
    return remote && remote.path ? remote : null
  }
  // 服务端更近一次生成的结果优先，避免这个窗口还挂着上一次的路径。
  if (emit.value.status === 'done' && emit.value.result
      && emit.value.result.generation_id !== local.generation_id) return emit.value.result
  return local
})

// ---- 任务书（plan）----
const planResult = computed(() => planState.value?.result || null)
const planData = computed(() => planResult.value?.plan || null)
const planStyle = computed(() => planData.value?.project_style || {})
const planSteps = computed(() => planData.value?.steps || [])
const planReusable = computed(() => planData.value?.reusable || [])
const planRisks = computed(() => planData.value?.risks || [])
const planNeeds = computed(() => planData.value?.needs || [])
// 最终写哪个文件：任务书里的 target_path 才是唯一事实，配置只是「要不要锁死它」。
// 用户在卡片上改路径时改的是这里，不是全局配置——改配置会影响下一次录制。
const planTargetOverride = ref('')
// 只有「用户自己敲的路径」才提示 import 可能失效：采用规划建议时，
// 那套 import 本来就是按建议位置算的，再吓唬他一次没有意义。
const planTargetManual = ref(false)
const planTarget = computed(() =>
  planTargetOverride.value || String(planData.value?.target_path || ''))
const planTargetChanged = computed(() => planTargetManual.value && !!planTargetOverride.value
  && planTargetOverride.value !== String(planData.value?.target_path || ''))
const planSuggestion = computed(() => {
  const suggestion = planResult.value?.suggestion || ''
  // 已经采纳过（写入位置 == 建议路径）就不再提示，否则黄框会一直挂着。
  return suggestion && suggestion !== planTarget.value ? suggestion : ''
})
// AI 自己定的位置：需要在卡片上明确「这是它选的，你确认后才写」。
const planTargetChosen = computed(() => !!planResult.value?.chosen)
const planTargetEditing = ref(false)
const planTargetDraft = ref('')
const planTargetError = ref('')
// 录制步骤变了（增删、改写），旧任务书就不再可信：后端也会用 plan_stale 拦一次，
// 这里提前把「按此执行」置灰，别让用户白等一次请求。
const stepsSignature = computed(() => steps.value
  .map(s => `${s.no}:${s.type}:${s.target?.alias || ''}`).join('|'))
const planStale = computed(() => !!planState.value
  && (planState.value.stale || planState.value.sig !== stepsSignature.value))

// 另一个窗口一开跑，这里就把上一次的错误和结果收掉，不然会和进度条同时出现。
watch(() => emit.value.status, (status) => {
  if (status === 'running') {
    genError.value = null
    genResult.value = null
  }
})

const canPlan = computed(() =>
  isStopped.value && steps.value.length > 0 &&
  !!config.workspace.trim() && !!config.model && !generating.value
)
const canExecute = computed(() =>
  !!planData.value && !!planTarget.value && !planStale.value && !generating.value
)
// 按钮置灰时，告诉用户第一个不满足的条件是什么（定位不唯一不再是阻断项，见 executePlan 的确认框）
const generateBlockReason = computed(() => {
  if (!isStopped.value) return '' // 未停止时「gen-compact」一行已经说明了原因，不重复
  if (!steps.value.length) return '还没有录制任何步骤'
  if (!config.workspace.trim()) return '还没有填写工作区（Git 目录）'
  if (!config.model) return '还没有选择模型'
  return ''
})
const executeBlockReason = computed(() => {
  if (!planData.value) return '还没有任务书'
  if (!planTarget.value) return '任务书没有给出写入路径，请重新规划或手动指定'
  if (planStale.value) return '录制步骤已变化，请重新规划'
  if (generating.value) return ''
  return ''
})

// 与后端 best_effort 对齐：目标框架下「命中最少」的那条候选 + 录制时算出的序号。
// 只用于生成前的确认文案，真正的定位由后端算。
function degradePlan(step) {
  const target = step.target || {}
  // 用户选过的就是实际会生成的那条，确认框不能按自动选择去描述。
  const picked = target.chosen
  if (picked && picked.desc) {
    const hit = (target.candidates || []).find(c => c.desc === picked.desc)
    if (hit && hit.count > 1) return { locator: picked.desc, count: hit.count, index: picked.index }
  }
  const pool = (target.candidates || []).filter(c => c.count > 0)
  if (!pool.length) return null
  const order = config.framework === 'robot-selenium' ? RF_ORDER : PW_ORDER
  const best = pool.slice().sort((a, b) => a.count - b.count || order.indexOf(a.kind) - order.indexOf(b.kind))[0]
  return { locator: best.desc, count: best.count, index: best.index }
}

// 断言解析器（录制期常驻的 opencode 进程）状态。后端每次轮询都会带回 ask 字段。
const askState = computed(() => {
  const ask = session.value && session.value.ask
  return ask && ask.status !== 'off' ? ask : null
})

const askHint = computed(() => {
  const ask = askState.value
  if (!ask) return ''
  if (ask.status === 'ready') {
    return ask.detail ? '断言解析器已就绪（预热未完成，第一次解析可能偏慢）' : '断言解析器已就绪，解析约 2 秒'
  }
  if (ask.status === 'failed') return '断言解析器不可用，解析将退回慢路径（约 7 秒）'
  if (ask.status === 'stopped') return '断言解析器已关闭'
  return `断言解析器${ask.label}`
})

function applySession(data) {
  // 换了一个录制会话：事件游标必须从头开始，否则新会话的 seq 都小于旧游标，
  // 增量接口永远返回空，弹窗就再也不弹了。
  const sid = data && data.id
  if (sid !== eventSessionId) {
    eventSessionId = sid || null
    eventCursor = -1
  }
  // 新步骤落地的那一轮就检查定位：录得快时，等回到列表再点准星，页面早不是当时的样子了。
  const fresh = freshSteps(data.steps)
  session.value = data
  // 拾取模式下，新出现的拾取元素会被草稿自动选中，然后关闭拾取。
  if (draft.value && draft.value.picking && data.picks && data.picks.length > draft.value.pickBaseline) {
    const latest = data.picks[data.picks.length - 1]
    draft.value.target = latest.target.alias
    draft.value.picking = false
    if (data.pick) togglePick(false, true)
    // 停止后浏览器还开着，拾取靠轮询拿结果；拿到就收，别一直在后台拉。
    if (data.status !== 'recording' && data.status !== 'starting') stopPolling()
  }
  trackLocConfirm(fresh, data)
  seenStepNos = new Set((data.steps || []).map(s => s.no))
  // 列表结构变了，拖拽实例要跟着（重）建。
  nextTick(initSortable)
}

// ---- 定位预览：在浏览器里把匹配元素框出来，人眼确认「生成时会点到谁」 ----
// peek.no 是当前展开的步骤号；只有它等于某一步时才在该步下面显示候选条。
const peek = ref({})

async function peekTarget(step) {
  if (peek.value.no === step.no) {
    await closePeek()
    return
  }
  // 页面已经翻走的步骤在当前页面上一个匹配都数不到，后端只能回 no_candidate；
  // 直接说清楚原因，别让用户以为是定位本身没生成出来。
  if (step.target?.page_gone) {
    peek.value = {
      no: step.no,
      loading: false,
      count: 0,
      items: [],
      active: 0,
      note: '这一步录制时在别的页面，停止时页面已经翻走，当前页面框不出来；生成会使用录制时的定位'
    }
    return
  }
  await runPeek(step.no, null)
}

async function runPeek(no, index) {
  peek.value = { no, loading: true, count: 0, items: [], active: 0, note: '' }
  try {
    const { data } = await api.highlightRecordingStep(no, index, config.framework)
    if (!data.ok) {
      peek.value = { no, loading: false, count: 0, items: [], active: 0, note: peekErrorText(data) }
      return
    }
    peek.value = {
      no,
      loading: false,
      desc: data.desc,
      count: data.count,
      active: data.active,
      items: data.items || [],
      note: ''
    }
  } catch (error) {
    peek.value = { no, loading: false, count: 0, items: [], active: 0, note: errorText(error) }
  }
}

function peekErrorText(data) {
  if (data.reason === 'not_found') {
    return '当前页面上已经数不到这个元素了：这一步可能发生在别的页面，或页面已经跳转'
  }
  if (data.reason === 'frame_lost') return '页面已关闭或正在跳转，暂时框不出来'
  if (data.reason === 'no_frame') return '浏览器已关闭，无法框出元素'
  if (data.reason === 'no_candidate') return '这一步没有可用的定位'
  return data.detail || '框不出来'
}

async function choosePeek(index) {
  const { no, desc } = peek.value
  if (!no || !desc) return
  try {
    // 必须先落库再重画：重画用的是「当前选中」，顺序反了会框回原来那个。
    const { data } = await api.chooseRecordingTarget(no, desc, index)
    applySession(data)
    await runPeek(no, index)
    peek.value.note = `已记下：生成时取第 ${index + 1} 个`
  } catch (error) {
    ElMessage.warning(errorText(error))
  }
}

async function closePeek() {
  const had = !!peek.value.no
  peek.value = {}
  if (!had) return
  try {
    await api.clearRecordingHighlight()
  } catch {
    // 浏览器可能已经关了，清不掉也无所谓：浮层随页面一起消失。
  }
}

// ---- 定位待确认：不唯一就当场选，别留到「回列表再点准星」 ----
// 实测痛点：录得快时来不及选，等事后回到步骤列表再点准星，被测页面已经走远，
// 框出来的根本不是当时那几个元素，选了也是瞎选。所以新步骤一落地就弹这张卡，
// 趁着页面还是录制时的状态把「第几个」定下来；事后想改再走行尾的准星。
// 规则：当前框架下没有唯一候选（真·不唯一），或者页面上有多个同名元素、
// 只是靠结构路径碰巧唯一的（结构定位），两者都要当场选——后者正是「事后回列表
// 已经框错了」的主要来源。页面已翻走 / 元素已消失的步骤不弹，那时选了也是瞎选。
const locConfirm = ref(null)     // { no, label, desc, count, items, picked, note, loading, gone, returnUrl }
const locQueue = ref([])         // 等待确认的步骤号，录得快时会排队
const locPicking = ref(false)
const GOTO_STEP_REASONS = {
  no_url: '这一步没有记下页面地址，翻不回去',
  no_frame: '找不到可操作的页面，浏览器可能已经关了',
  goto_failed: '那一页打不开了'
}
// 上一轮已知的步骤号：null 表示还没拿到首帧快照（刚打开面板时不能把历史步骤都当成新的）
let seenStepNos = null

function freshSteps(list) {
  if (seenStepNos === null) return []
  return (list || []).filter(s => !seenStepNos.has(s.no))
}

function needsLocConfirm(step) {
  const target = step.target
  // 页面已翻走 / 录制时元素就没了：当前页面框不出东西，弹了也只能瞎选，留给步骤列表里的「待确认」。
  if (!target || target.page_gone || target.unverified) return false
  // 点完元素已经被这一次点击带走（下拉收起、列表删行）：计数取自按下的那一刻，
  // 但现在页面上只剩别的同名元素，再框就是张冠李戴，不如不弹。
  if (target.vanished_after) return false
  // 已经亲手确认过的步骤不再打扰。
  if (target.chosen && target.chosen.desc) return false
  // 真·不唯一：当前框架下没有任何唯一候选，生成只能按录制时的序号取。
  if (effectiveStatus(step) === 'ambiguous') return true
  // 看着唯一、其实指向了别的元素：写进脚本必点错，比不唯一更需要当场重选。
  if (effectiveStatus(step) === 'suspect') return true
  // 页面上有多个同名元素、只是靠结构路径（css）碰巧唯一的，也弹：
  // 生成出来是一长串 div > div:nth-of-type()，而人其实想选的是「列表里的这一行」。
  // 这正是事后回列表看不清、页面又已经变了的场景。
  return (target.matches?.count || 0) > 1
}

// 快照里的候选（target.matches.items）没带序号，只有框出来的那份才带：
// 统一按数组下标补一个，序号才和页面上的编号对得上。
function withIndex(items) {
  return (items || []).map((item, i) => ({ ...item, index: typeof item.index === 'number' ? item.index : i }))
}

function trackLocConfirm(fresh, data) {
  // 停录/暂停期间不再弹：那已经不是「当场」了，硬弹只会打断收尾。
  if (data.status !== 'recording' || data.paused) return
  const hits = fresh.filter(needsLocConfirm).map(s => s.no)
  if (!hits.length) return
  locQueue.value = [...locQueue.value, ...hits.filter(no => no !== locConfirm.value?.no)]
  if (!locConfirm.value) openNextLocConfirm()
}

// 停止后逐条过一遍：录制中漏掉的（页面跑掉了、当场点了「稍后再选」）在这里补齐。
// 每条都能「回到那一步的页面」重新框——这正是录制中来不及做的动作。
function startLocReview() {
  const hits = steps.value.filter(reviewable).map(s => s.no)
  if (!hits.length) {
    ElMessage.info('没有需要确认的步骤')
    return
  }
  locQueue.value = hits.filter(no => no !== locConfirm.value?.no)
  if (!locConfirm.value) openNextLocConfirm()
}

async function openNextLocConfirm() {
  if (locConfirm.value || !locQueue.value.length) return
  const [no, ...rest] = locQueue.value
  locQueue.value = rest
  const step = steps.value.find(s => s.no === no)
  if (!step) {
    openNextLocConfirm()
    return
  }
  const snap = step.target?.matches
  locConfirm.value = {
    no,
    label: stepDescription(step),
    loading: true,
    // 先用录制时存的快照垫底：浏览器那边框不出来时，列表至少还是当时那几个。
    desc: snap?.desc || '',
    count: snap?.count || 0,
    items: withIndex(snap?.items),
    active: null,
    picked: null,
    note: '',
    // 页面跑掉不是死路：这一步录制时的地址记着，能把那一页重新打开再选。
    gone: false,
    returnUrl: '',
    reopening: false,
    returning: false
  }
  // 行内准星和这张卡共用页面上的同一层框，先收掉，免得框和列表说的是两回事。
  if (peek.value.no) await closePeek()
  await refreshLocConfirm(no)
}

/** 让浏览器重新框一次这一步的目标。页面跑了就标 gone，界面据此给出「回去看」。 */
async function refreshLocConfirm(no) {
  try {
    const { data } = await api.highlightRecordingStep(no, null, config.framework)
    if (!locConfirm.value || locConfirm.value.no !== no) return
    if (data.ok) {
      locConfirm.value = {
        ...locConfirm.value,
        loading: false,
        desc: data.desc,
        count: data.count,
        items: withIndex(data.items),
        active: data.active,
        gone: false
      }
    } else {
      locConfirm.value = {
        ...locConfirm.value,
        loading: false,
        // not_found / frame_lost 才是「页面跑掉了」，重开那一页能救；
        // no_candidate 这类是定位本身没候选，回去也白回。
        gone: data.reason === 'not_found' || data.reason === 'frame_lost',
        note: `${peekErrorText(data)}；只能按录制时的快照选`
      }
    }
  } catch (error) {
    if (!locConfirm.value || locConfirm.value.no !== no) return
    locConfirm.value = { ...locConfirm.value, loading: false, note: errorText(error) }
  }
}

// 用录制时记下的地址把那一页重新打开（不走浏览器后退，那个在 SPA 上会退飞）。
async function reopenStepPage() {
  const current = locConfirm.value
  if (!current || current.reopening) return
  current.reopening = true
  try {
    const { data } = await api.gotoRecordingStepDoc(current.no)
    if (!locConfirm.value || locConfirm.value.no !== current.no) return
    if (!data.ok) {
      ElMessage.warning(GOTO_STEP_REASONS[data.reason] || '翻不回那一步的页面')
      return
    }
    locConfirm.value = { ...locConfirm.value, returnUrl: data.return_url || '', note: '' }
    // 回来了再框一次，这次元素应该在页面上。
    await refreshLocConfirm(current.no)
    if (!locConfirm.value?.count) {
      locConfirm.value = {
        ...locConfirm.value,
        note: '页面回来了，但这一步的元素已经不在了（数据变了或列表刷新过）'
      }
    }
  } catch (error) {
    ElMessage.warning(errorText(error))
  } finally {
    if (locConfirm.value) locConfirm.value.reopening = false
  }
}

async function returnFromStepPage() {
  const current = locConfirm.value
  if (!current || current.returning) return
  current.returning = true
  try {
    const { data } = await api.gotoRecordingBack()
    if (!locConfirm.value || locConfirm.value.no !== current.no) return
    locConfirm.value = {
      ...locConfirm.value,
      returnUrl: '',
      note: data.ok ? '已回到刚才的页面' : '回不去了，那一页可能已经关掉'
    }
  } catch (error) {
    ElMessage.warning(errorText(error))
  } finally {
    if (locConfirm.value) locConfirm.value.returning = false
  }
}

async function pickLocConfirm(index) {
  const current = locConfirm.value
  if (!current || current.loading || locPicking.value || !current.desc) return
  locPicking.value = true
  try {
    // 先落库再重画，和行内准星同一个顺序，否则会框回原来那个。
    const { data } = await api.chooseRecordingTarget(current.no, current.desc, index)
    applySession(data)
    if (!locConfirm.value || locConfirm.value.no !== current.no) return
    locConfirm.value = {
      ...locConfirm.value,
      picked: index,
      note: `已记下：生成时取第 ${index + 1} 个`
    }
    // 页面上把红框挪到选中的那个，「就是它」这件事用眼睛再确认一次。
    await api.highlightRecordingStep(current.no, index, config.framework)
  } catch (error) {
    ElMessage.warning(errorText(error))
  } finally {
    locPicking.value = false
  }
}

function acceptLocConfirm() {
  if (!locConfirm.value || locConfirm.value.picked == null) return
  closeLocConfirm()
}

function deferLocConfirm() {
  closeLocConfirm()
}

async function closeLocConfirm() {
  const current = locConfirm.value
  locConfirm.value = null
  // 关卡片时人还站在「回去看」的那一页：先把浏览器送回离开的地方再开下一条，
  // 否则下一条会在跳转途中去框选，一个都框不到。
  if (current && current.returnUrl) {
    try {
      await api.gotoRecordingBack()
    } catch {
      // 回不去不影响确认，别把这条卡住
    }
  }
  if (locQueue.value.length) {
    // 上一条的「就这个」可能刚点完，留一拍再切下一条，避免卡片内容闪一下。
    window.setTimeout(openNextLocConfirm, 220)
    return
  }
  // 没有排队时把页面上的框清掉；行内准星还开着的话，框归它管。
  if (!peek.value.no) api.clearRecordingHighlight().catch(() => {})
}

// 步骤被删掉时卡片要跟着消失，否则会对着不存在的步骤选序号。
watch(steps, (list) => {
  const current = locConfirm.value
  if (!current) return
  if (!list.some(step => step.no === current.no)) closeLocConfirm()
})

async function loadCurrent() {
  try {
    const { data } = await api.getRecordingCurrent()
    applySession(data)
  } catch (error) {
    if (error.response && error.response.status === 404) session.value = null
  }
  // 会话状态决定轮询频率：停录后仍要看生成进度（两个窗口共用这份状态），
  // 所以不能像以前那样一停就彻底不轮了，只是把频率降下来。
  syncPolling()
}

function startPolling(ms = 1000) {
  if (pollTimer && pollMs === ms) return
  if (pollTimer) clearInterval(pollTimer)
  pollMs = ms
  pollTimer = setInterval(loadCurrent, ms)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  pollMs = 0
}

function syncPolling() {
  if (!pollEnabled) return
  const status = session.value?.status
  if (!status) {
    stopPolling()
    stopEventStream()
    return
  }
  // starting 也要轮询：并排面板窗口是在录制启动过程中打开的，抓不到主页面那次状态跳变。
  const live = status === 'recording' || status === 'starting'
  // 录制中换成事件流：只拉几十字节的增量游标，有新事件才去取完整快照。
  // 原来是 1 秒拉一次完整快照，弹窗要等最多 1 秒才出现——那时页面早跳走了。
  // 完整轮询留着兜底（状态跳变、另一个窗口的操作），但不指望它赶时间。
  if (live) startEventStream()
  // 停止后浏览器还开着：拾取、暂停照样能在页面里就地切换（拾取模式下按 Esc 退出）。
  // 事件流降速到 1 秒继续跟着，不然面板要等 2 秒的完整轮询才发现状态变了。
  else if (isStopped.value && browserOpen.value) startEventStream(1000)
  else stopEventStream()
  // 生成中回到 1 秒：阶段要跟得上进度条的推进，2 秒一跳会显得卡顿。
  startPolling(live || emitRunning.value ? 1000 : 2000)
}

// ---- 事件流：让「新步骤」尽快到达界面 ----
// 后端一直有这个游标接口（/recording/events），只是前端从来没用过。
let eventCursor = -1
let eventSessionId = null
let eventTimer = null
let eventMs = 0
let eventBusy = false

function startEventStream(ms = 200) {
  if (eventTimer && eventMs === ms) return
  stopEventStream()
  eventMs = ms
  eventTimer = setInterval(pollRecordingEvents, ms)
}

function stopEventStream() {
  if (eventTimer) clearInterval(eventTimer)
  eventTimer = null
  eventMs = 0
}

async function pollRecordingEvents() {
  // 上一跳还没回来就跳过这一拍，别在慢请求上堆队列。
  if (!pollEnabled || eventBusy) return
  eventBusy = true
  try {
    const { data } = await api.getRecordingEvents(eventCursor)
    const cursor = typeof data.cursor === 'number' ? data.cursor : eventCursor
    let reload = false
    if (cursor !== eventCursor) {
      eventCursor = cursor
      reload = true
    }
    // 拾取 / 暂停可以在页面里就地切换（拾取模式下按 Esc 退出），游标不会动。
    // 只比「有没有新步骤」的话，界面会一直停在旧状态：按钮还写着「退出拾取」，人却发现没反应。
    if (typeof data.pick === 'boolean' && data.pick !== session.value?.pick) reload = true
    if (typeof data.paused === 'boolean' && data.paused !== session.value?.paused) reload = true
    if (reload) await loadCurrent()
  } catch {
    // 会话没了或接口挂了：退回普通轮询，别每秒五次打 404。
    stopEventStream()
  } finally {
    eventBusy = false
  }
}

watch(() => session.value?.status, () => syncPolling())

async function startRecording() {
  const url = (config.baseUrl || '').trim()
  if (!/^https?:\/\//.test(url)) {
    ElMessage.warning('请先填写以 http:// 或 https:// 开头的录制起始地址')
    return
  }
  starting.value = true
  genResult.value = null
  genError.value = null
  localPending.value = false
  // 新录制 = 新步骤：上一份任务书不能再用了，留着只会让人误点执行。
  planState.value = null
  planNotes.value = []
  planError.value = null
  planPending.value = false
  closePlanSheet()
  try {
    const { data } = await api.startRecording(url, {
      model: config.model,
      workspace: config.workspace.trim(),
      framework: config.framework,
      // 录制窗口是新的浏览器 profile，模型/工作区/框架这三项不随请求带过去就会丢。
      // target_path 可以留空：停止录制后由任务书按项目约定定位置。
      target_path: config.targetPath.trim(),
      title: config.title.trim(),
      python: (config.python || '').trim()
    })
    draft.value = null
    applySession(data)
    // 被测系统拒绝被 iframe 嵌入时后端会自动换成独立窗口，不说明白用户会以为弹错了窗。
    if (data.notice) {
      ElMessage({ message: data.notice, type: 'warning', duration: 10000, showClose: true })
    }
  } catch (error) {
    ElMessage.error(errorText(error))
    await loadCurrent()
  } finally {
    starting.value = false
  }
}

async function stopRecording() {
  stopping.value = true
  try {
    const { data } = await api.stopRecording()
    if (draft.value && draft.value.picking) draft.value.picking = false
    applySession(data)
    if (data.browser_open) {
      ElMessage.success('已停止录制；浏览器保持打开，可以继续拾取元素、验证断言')
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    stopping.value = false
  }
}

async function togglePick(on, quiet = false) {
  try {
    const { data } = await api.setRecordingPick(on)
    if (session.value) session.value = { ...session.value, pick: data.pick }
    // 停止后的轮询只为接拾取结果，退出拾取就停。
    if (!on && !isRecording.value) stopPolling()
  } catch (error) {
    if (!quiet) ElMessage.error(errorText(error))
  }
}

async function togglePause() {
  if (pausing.value) return
  pausing.value = true
  try {
    const { data } = await api.setRecordingPause(!paused.value)
    if (session.value) session.value = { ...session.value, paused: data.paused }
    ElMessage.success(data.paused ? '已暂停：在浏览器里的操作不会被记录' : '已继续录制')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    pausing.value = false
  }
}

async function removeStep(no) {
  try {
    const { data } = await api.deleteRecordingStep(no)
    applySession(data)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function moveStep(no, direction) {
  try {
    const { data } = await api.moveRecordingStep(no, direction)
    applySession(data)
  } catch (error) {
    ElMessage.warning(errorText(error))
  }
}

// ---- 就地编辑（只改值，不动定位）----
const editing = ref(null)
const EDITABLE = {
  fill: ['value'], select: ['value', 'label'], goto: ['url'], sleep: ['seconds'],
  press: ['value'], dialog: ['value'],
  // 断言和等待只差一个「最长等待」，所以两者都能就地调，不用删了重插。
  assert: ['timeout', 'interval', 'reload'], wait: ['timeout', 'interval', 'reload']
}

function editableFields(step) {
  if (!canEdit.value) return []
  return EDITABLE[step.type] || []
}

function startEdit(step) {
  const intent = step.intent || {}
  editing.value = {
    no: step.no,
    type: step.type,
    saving: false,
    value: step.value ?? '',
    label: step.label ?? '',
    url: step.url ?? '',
    seconds: intent.seconds != null ? Number(intent.seconds) : 1,
    // 断言 / 等待的等待参数：不勾「允许等待」就是立即断言。
    allowWait: !!intent.timeout,
    timeout: intent.timeout ? Number(intent.timeout) : 30,
    interval: intent.interval ? Number(intent.interval) : 5,
    reload: !!intent.reload
  }
}

function cancelEdit() {
  editing.value = null
}

async function saveEdit() {
  const current = editing.value
  if (!current || current.saving) return
  const payload = {}
  if (current.type === 'fill') payload.value = current.value
  else if (current.type === 'select') {
    payload.value = current.value
    payload.label = current.label
  } else if (current.type === 'goto') payload.url = (current.url || '').trim()
  else if (current.type === 'sleep') payload.seconds = current.seconds
  else if (current.type === 'assert' || current.type === 'wait') {
    // timeout 传 null 表示改回「立即断言」，后端会把 step type 从 wait 换回 assert。
    payload.timeout = current.allowWait ? Number(current.timeout) : null
    payload.reload = current.allowWait ? !!current.reload : false
    payload.interval = current.allowWait && current.reload ? Number(current.interval) : null
    if (current.allowWait && current.reload && payload.interval > payload.timeout) {
      ElMessage.warning('刷新间隔不能大于最长等待')
      return
    }
  } else if (current.type === 'press' || current.type === 'dialog') payload.value = current.value
  else {
    editing.value = null
    return
  }
  current.saving = true
  try {
    const { data } = await api.updateRecordingStep(current.no, payload)
    editing.value = null
    applySession(data)
  } catch (error) {
    current.saving = false
    ElMessage.error(errorText(error))
  }
}

// ---- 拖拽排序（sortablejs）：拖动步骤编号 ----
const timelineEl = ref(null)
let sortable = null

function initSortable() {
  if (!timelineEl.value) return
  // `.timeline` 是 v-if 渲染的，元素变化时重建实例。
  if (sortable && sortable.el === timelineEl.value) {
    sortable.option('disabled', !canEdit.value)
    return
  }
  if (sortable) {
    sortable.destroy()
    sortable = null
  }
  sortable = Sortable.create(timelineEl.value, {
    animation: 150,
    handle: '.step-no',
    ghostClass: 'step-ghost',
    chosenClass: 'step-chosen',
    disabled: !canEdit.value,
    onEnd: async (evt) => {
      const { oldIndex, newIndex, from, item } = evt
      if (oldIndex === newIndex || oldIndex == null || newIndex == null) return
      // 先把 DOM 还原：顺序以后端返回的数据为准，避免 Vue 的 keyed diff 和 sortable 打架。
      // 注意 item 已经被 sortable 移动过，要先把它排除再取 oldIndex 位置，否则会错一位。
      const siblings = Array.from(from.children).filter((el) => el !== item)
      from.insertBefore(item, siblings[oldIndex] || null)
      // sortable 加的临时类不会自动清掉，残留会让某一行一直带阴影。
      from.querySelectorAll('.step-chosen, .step-ghost, .step-drag')
        .forEach((el) => el.classList.remove('step-chosen', 'step-ghost', 'step-drag'))
      const moved = steps.value[oldIndex]
      if (!moved) return
      try {
        const { data } = await api.reorderRecordingStep(moved.no, newIndex)
        applySession(data)
      } catch (error) {
        ElMessage.warning(errorText(error))
      }
    }
  })
}

// ---- 断言 / 等待 模板 ----
// 断言与等待共用一个入口：draft.kind 只剩 assert / sleep，等待变成 assert 上的一个开关。
function draftKindTypes(kind) {
  if (kind === 'sleep') return SLEEP_TYPES
  return ASSERT_TYPES
}

const draftTypes = computed(() => draft.value ? draftKindTypes(draft.value.kind) : [])
const draftTitle = computed(() => ({ assert: '添加断言', sleep: '固定等待' })[draft.value?.kind] || '添加断言')
const insertLabel = computed(() => ({ sleep: '插入固定等待' })[draft.value?.kind] || '插入断言')

// 收起时也要一眼看出这条会不会等，否则得展开才知道。
const pollSummary = computed(() => {
  const current = draft.value
  if (!current || !current.allowWait) return '立即判定'
  const tail = `最多 ${current.timeout} 秒`
  return current.reload ? `${tail} · 每 ${current.interval} 秒刷新` : tail
})

// 表单当前签名：解析结果会填进表单，之后用户改没改过，靠这个签名对比。
const formSig = computed(() => {
  const current = draft.value
  if (!current) return ''
  return [current.intentType, current.target || '', current.value || '', current.seconds ?? '',
    current.allowWait ? 1 : 0, current.timeout ?? '', current.interval ?? '', current.reload ? 1 : 0].join('\u0001')
})
const formDirty = computed(() => !!draft.value?.parsed && formSig.value !== (draft.value?.parsedSig || ''))

// 轮询等待的「此刻」几乎必然不成立，用红色会给错误信号，单独一档中性色。
const verifyTone = computed(() => {
  const result = draft.value?.verify
  if (!result) return ''
  if (result.poll) return 'neutral'
  return result.ok ? 'ok' : 'bad'
})
const verifyTitle = computed(() => {
  const result = draft.value?.verify
  if (!result) return ''
  if (result.poll) {
    return result.ok ? '此刻已满足：这一步会立刻通过，不会真的等' : '此刻未满足：插入后由它自己等到满足或超时'
  }
  return `当前页面状态：${result.ok ? '成立' : '不成立'}`
})

// 表单一动，上一次「验证当前页面」的结论就过期了，留着会比没有更误导。
watch(formSig, () => {
  if (draft.value && draft.value.verify) draft.value.verify = null
})

function openDraft(kind, afterNo = null) {
  const first = draftKindTypes(kind)[0]
  draft.value = {
    kind,
    // 从某一步的「+」进来时记住锚点；工具栏那三个按钮不传，就是追加到末尾。
    afterNo: afterNo || null,
    text: '',
    intentType: first,
    target: null,
    value: '',
    seconds: 1,
    // 高级默认收起，不勾就是立即断言。默认 30 秒比框架默认的 5 秒实用；
    // 刷新默认不勾——刷新会重置表单和登录态，只会让条件更难成立。
    allowWait: false,
    pollOpen: false,
    timeout: 30,
    interval: 5,
    reload: false,
    parsed: null,
    parsedSig: '',
    verify: null,
    verifying: false,
    parsing: false,
    inserting: false,
    picking: false,
    pickBaseline: 0
  }
  // 面板窄栏里草稿在列表下方，打开后滚到底，避免点了「添加断言」却看不到表单。
  nextTick(() => {
    const body = document.querySelector('.steps-card .el-card__body')
    if (body && body.scrollHeight > body.clientHeight) {
      body.scrollTop = body.scrollHeight
      return
    }
    const card = document.querySelector('.draft-card')
    if (card && card.scrollIntoView) card.scrollIntoView({ block: 'nearest' })
  })
}

function closeDraft() {
  if (draft.value && draft.value.picking) togglePick(false, true)
  draft.value = null
  // 停止后才开的轮询（为了接拾取结果）用完就收。
  if (!isRecording.value) stopPolling()
}

// 新插入的步骤：滚进视野 + 高亮 1.2 秒，否则插在中间那一下没有任何反馈
function revealStep(no) {
  flashingNo.value = no
  nextTick(() => {
    const el = document.querySelector(`.timeline .step[data-step-no="${no}"]`)
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashingNo.value = 0 }, 1200)
}

function needsTarget(type) { return NEEDS_TARGET.has(type) }
function needsValue(type) { return NEEDS_VALUE.has(type) }

async function parseDraft() {
  const current = draft.value
  if (!current || current.parsing) return
  current.parsing = true
  current.parsed = null
  current.verify = null
  try {
    const { data } = await api.parseRecordingIntent(current.text.trim(), config.model)
    // 解析要等模型几秒，期间用户可能已经关掉草稿或换了一个。
    if (draft.value === current) applyParsed(current, data.intent)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    if (draft.value === current) current.parsing = false
  }
}

// 老类型归一：模型或历史数据里可能还是 wait_visible / wait_hidden，
// 它们等价于 visible / hidden 加一个「允许等待」，落到表单上就拆成这两个字段。
const LEGACY_WAIT = { wait_visible: 'visible', wait_hidden: 'hidden' }

function kindForIntent(type) {
  return type === 'sleep' ? 'sleep' : 'assert'
}

// 解析结果直接落到表单字段上：用户能改，插入只认表单。
// 之前预览和表单是两套数据，点了「确认插入」提交的是预览，改表单完全没用。
function applyParsed(current, intent) {
  const legacy = LEGACY_WAIT[intent.type]
  // 模型可能给出别的语义（在「添加断言」里写了「等到遮罩消失」），卡片种类跟着结果走。
  current.kind = kindForIntent(intent.type)
  current.intentType = legacy || intent.type
  current.target = intent.target || null
  current.value = intent.value || ''
  if (intent.seconds != null) current.seconds = Number(intent.seconds)
  current.allowWait = !!legacy || intent.timeout != null
  if (intent.timeout != null) current.timeout = Number(intent.timeout)
  if (intent.interval != null) current.interval = Number(intent.interval)
  if (intent.reload != null) current.reload = !!intent.reload
  // 解析出等待就展开高级区，否则用户看不到「这条会等 360 秒」。
  if (current.allowWait) current.pollOpen = true
  current.parsed = intent
  // 等 DOM 更新后再记签名，否则 computed 立即求值会被当成「用户改过了」。
  nextTick(() => {
    if (draft.value === current) current.parsedSig = formSig.value
  })
}

async function verifyDraft() {
  const current = draft.value
  if (!current || current.verifying) return
  let intent
  try {
    intent = buildFormIntent(current)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  current.verifying = true
  try {
    const { data } = await api.verifyRecordingIntent(intent)
    if (draft.value === current) {
      current.verify = data
      // 窄栏里结果块会出现在可视区之外，验证完要把它带进视野，否则以为没反应。
      // 等一帧再滚：块刚插进 DOM 时高度还没算出来，nearest 会滚不到位。
      setTimeout(() => {
        const box = document.querySelector('.draft-card .verify-box')
        if (box && box.scrollIntoView) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }, 80)
    }
  } catch (error) {
    if (draft.value === current) current.verify = null
    ElMessage.error(errorText(error))
  } finally {
    if (draft.value === current) current.verifying = false
  }
}

async function closeBrowser() {
  try {
    await ElMessageBox.confirm(
      '关闭后就无法再在当前页面拾取元素或验证断言了。已录步骤、生成与回放校验都不受影响。',
      '关闭浏览器',
      { confirmButtonText: '关闭', cancelButtonText: '先留着', type: 'warning' }
    )
  } catch {
    return
  }
  closingBrowser.value = true
  try {
    await api.closeRecordingBrowser()
    ElMessage.success('已关闭浏览器')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    closingBrowser.value = false
    // 面板窗口本身可能就活在这个浏览器里，请求回来时页面已经没了；能回来的情况刷新一下状态。
    loadCurrent()
  }
}

// 行内「+」的四个入口：刷新走独立通道，其余三种打开草稿卡。
function onInsertCommand(kind, afterNo) {
  if (kind === 'reload') return insertReload(afterNo)
  openDraft(kind, afterNo)
}

async function insertReload(afterNo = null) {
  if (addingReload.value) return
  addingReload.value = true
  try {
    const { data } = await api.addRecordingReload(afterNo)
    applySession(data)
    revealInserted(afterNo)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    addingReload.value = false
  }
}

async function insertIntent(intent, text) {
  draft.value.inserting = true
  try {
    const afterNo = draft.value.afterNo
    const payload = { intent, text: (text || '').trim() || null }
    if (afterNo) payload.after_no = afterNo
    const { data } = await api.addRecordingStep(payload)
    draft.value = null
    applySession(data)
    // 插在中间时草稿卡在列表底部，点了「确认插入」屏幕上看不出变化，
    // 所以把新步骤滚进视野并闪一下，明确「加在哪了」。
    revealInserted(afterNo)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    if (draft.value) draft.value.inserting = false
  }
}

// 插入位置推算：after_no 为空就是末尾；否则落在锚点后面，
// 但后端不会插进「点击 → 新标签页」这一对里，遇到就再往后挪一位（与 session.add_intent_step 一致）。
function revealInserted(afterNo) {
  const list = steps.value
  if (!list.length) return
  let pos = list.length - 1
  if (afterNo) {
    const idx = list.findIndex(s => s.no === afterNo)
    if (idx >= 0) pos = list[idx + 1] && list[idx + 1].type === 'new_page' ? idx + 2 : idx + 1
  }
  const step = list[pos] || list[list.length - 1]
  if (step) revealStep(step.no)
}

function buildFormIntent(current) {
  const type = current.intentType
  const intent = { type, target: null, value: null, seconds: null,
    timeout: null, interval: null, reload: false }
  if (needsTarget(type)) {
    if (!current.target) throw new Error('请选择目标元素，或点击「拾取元素」')
    intent.target = current.target
  }
  if (needsValue(type)) {
    const value = String(current.value || '').trim()
    if (!value) throw new Error('请填写文字或值')
    intent.value = value
  }
  if (type === 'sleep') {
    const seconds = Number(current.seconds)
    if (!(seconds > 0 && seconds <= MAX_SLEEP_SECONDS)) {
      throw new Error(`固定等待需在 0 到 ${MAX_SLEEP_SECONDS} 秒之间`)
    }
    intent.seconds = seconds
  }
  // 勾了「允许等待」才带 timeout：不勾就是立即断言，回放时 5 秒内不成立即失败。
  if (type !== 'sleep' && current.allowWait) {
    const timeout = Number(current.timeout)
    if (!(timeout > 0 && timeout <= MAX_WAIT_SECONDS)) {
      throw new Error(`最长等待需在 1 到 ${MAX_WAIT_SECONDS} 秒之间`)
    }
    intent.timeout = timeout
    intent.reload = !!current.reload
    if (current.reload) {
      const interval = Number(current.interval)
      if (!(interval >= 1 && interval <= MAX_POLL_INTERVAL)) {
        throw new Error(`刷新间隔需在 1 到 ${MAX_POLL_INTERVAL} 秒之间`)
      }
      if (interval > timeout) throw new Error('刷新间隔不能大于最长等待')
      intent.interval = interval
    }
  }
  return intent
}

function insertFromForm() {
  let intent
  try {
    intent = buildFormIntent(draft.value)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  insertIntent(intent, draft.value.text)
}

async function pickForDraft() {
  draft.value.pickBaseline = (session.value?.picks || []).length
  draft.value.picking = true
  // 停止后浏览器仍然开着，拾取结果只能靠轮询拿回来（录制中本来就在轮询）。
  startPolling()
  await togglePick(true)
}

// ---- 步骤展示 ----
function frameworkName(value) {
  return (FRAMEWORKS.find(f => f.value === value) || {}).label || (value ? value : '未识别')
}

// 检测结果里的绝对路径太长（Python 解释器常常三四层），chips 里只留末尾两段，完整路径放 title。
function tailPath(path, depth = 2) {
  if (!path) return ''
  const parts = String(path).split(/[\\/]+/).filter(Boolean)
  return parts.slice(-depth).join('/')
}

function intentLabel(type) { return INTENT_LABELS[type] || type }

function intentDescription(intent, target) {
  if (!intent) return ''
  const parts = [intentLabel(intent.type)]
  const alias = intent.target || (target && target.alias)
  if (alias) parts.push(`「${alias}」`)
  if (intent.value !== null && intent.value !== undefined && intent.value !== '') parts.push(`：${intent.value}`)
  if (intent.type === 'sleep') parts.push(`${intent.seconds} 秒`)
  // 带最长等待就把「等多久、靠不靠刷新推进」写清楚，否则列表里和立即断言长得一样。
  // 接在最后一段后面而不是 push 新段：parts 用空格 join，新段会在逗号前多出一个空格。
  if (intent.timeout) {
    let tail = `，最多 ${intent.timeout} 秒`
    if (intent.reload) tail += `，每 ${intent.interval || 5} 秒刷新一次`
    if (parts.length) parts[parts.length - 1] += tail
    else parts.push(tail)
  }
  return parts.join(' ')
}

function stepKindLabel(type) { return STEP_LABELS[type] || type }
function stepTone(type) { return STEP_TONES[type] || 'info' }

function stepDescription(step) {
  switch (step.type) {
    case 'goto': return `打开 ${step.url || ''}`
    case 'reload': return '刷新当前页面'
    case 'click': return `点击「${step.target ? step.target.alias : '元素'}」`
    // 空值不是「没录到」，而是用户把输入框删空了，回放时要真的清空它。
    case 'fill': return `在「${step.target ? step.target.alias : '输入框'}」中${
      step.value === '' || step.value == null ? '清空内容' : `输入 ${step.value}`}`
    case 'select': return `在「${step.target ? step.target.alias : '下拉框'}」中选择 ${step.label || step.value || ''}`
    case 'press': return `在「${step.target ? step.target.alias : '元素'}」上按 ${step.value || ''}`
    case 'dialog': {
      const action = step.value === 'dismiss' ? '取消' : '确定'
      const message = (step.dialog && step.dialog.message) || ''
      return message ? `弹窗「${message}」→ 点${action}` : `弹窗 → 点${action}`
    }
    case 'new_page': return '切换到新打开的标签页'
    case 'sleep': return `固定等待 ${step.intent ? step.intent.seconds : ''} 秒`
    case 'assert':
    case 'wait': return intentDescription(step.intent, step.target)
    default: return step.type
  }
}

// 与后端 locators.hits_target 一致：relation 说明这条候选命中的是不是用户点的那个元素。
// 没有 relation 的是改造前录的数据，保持放行——新校验不能把原本能用的步骤判死。
function hitsTarget(candidate) {
  const relation = candidate.relation
  if (!relation) return true
  if (relation === 'other' || relation === 'gone') return false
  return candidate.index == null || candidate.index >= 0
}

// 与后端 locators.pick_for 一致：按当前框架挑出第一个唯一、且确实命中目标的候选。
function pickFor(framework, candidates = []) {
  const order = framework === 'robot-selenium' ? RF_ORDER : PW_ORDER
  for (const kind of order) {
    const hit = candidates.find(c => c.kind === kind && c.count === 1 && hitsTarget(c))
    if (hit) return hit
  }
  return null
}

function effectiveStatus(step) {
  if (!step.target) return null
  // 录制时在登录页，停止时页面已经翻到聊天页：旧元素随旧 document 销毁了，
  // 不能拿新页面重算，也就不能说它「不唯一」。生成仍用录制时的定位。
  // 但「录制时本来就不唯一 / 没核实 / 已选定」不能被这个标记盖掉，否则会隐藏真正的降级信息。
  if (step.target.page_gone) {
    if (step.target.unverified) return 'unverified'
    if (step.target.chosen && step.target.chosen.desc) {
      return step.target.chosen.index_invalid ? 'invalid' : 'chosen'
    }
    return pickFor(config.framework, step.target.candidates || []) ? 'page_gone' : 'ambiguous'
  }
  // 录制那一刻页面就被这一步带走了（回车提交、点掉链接）：元素不在新 document 上，
  // 定位是按快照推出来的、没核实过。单独标出来让用户回放确认，而不是伪装成唯一或不唯一。
  if (step.target.unverified) return 'unverified'
  // 用户看过页面上的框、亲手选定的：这就是他确认过的定位，不该再按自动选择标成结构定位。
  if (step.target.chosen && step.target.chosen.desc) {
    return step.target.chosen.index_invalid ? 'invalid' : 'chosen'
  }
  const chosen = pickFor(config.framework, step.target.candidates || [])
  if (!chosen) {
    // 有唯一候选、但每条都指向了别的元素：后端会退回其中一条并标 suspect。
    // 这里跟着标，否则界面说「不唯一」而生成侧其实取不到任何定位，两边对不上。
    const order = config.framework === 'robot-selenium' ? RF_ORDER : PW_ORDER
    const unique = (step.target.candidates || []).some(c => order.includes(c.kind) && c.count === 1)
    return unique ? 'suspect' : 'ambiguous'
  }
  if (FRAGILE_KINDS.includes(chosen.kind)) return 'fragile'
  if (step.target.status === 'stale') return 'stale'
  return 'unique'
}

function withNth(desc, index, count) {
  // index 的语义是「多个匹配里取第几个」。count == 1 时它没有意义，
  // 硬拼会渲染成 .nth(0)——而 row_scoped 这类本身就唯一的定位更没有序号可言。
  if (index == null || (count != null && count <= 1)) return desc
  // RF 的定位本身就是 xpath 字符串，再拼 .nth() 是错的，用中文明说取第几个。
  if (config.framework === 'robot-selenium') return `${desc}  →  第 ${index + 1} 个`
  return `${desc}.nth(${index})`
}

function locatorText(target) {
  // 用户亲手选过的，显示他确认的那条：生成时也优先走它（见 emit._locator_info）。
  const picked = target.chosen
  if (picked && picked.desc) return withNth(picked.desc, picked.index, picked.count)
  const chosen = pickFor(config.framework, target.candidates || [])
  if (chosen) return withNth(chosen.desc, chosen.index, chosen.count)
  // 没核实过的步骤按快照里语义最强的那条显示，和生成时用的是同一条。
  const first = (target.candidates || [])[0]
  if (target.unverified && first) return first.desc
  return '无唯一定位'
}

function statusLabel(status) {
  return ({
    unique: '唯一', fragile: '结构定位', ambiguous: '不唯一', unverified: '未核实',
    page_gone: '页面已翻页', stale: '页面已变化', chosen: '已选定', invalid: '序号失效',
    suspect: '定位存疑'
  })[status] || status
}

// 徽标只写四个字，把「凭什么算唯一」放进 title：role_bare 和普通唯一的前提不一样，
// 不说明的话用户会以为它和「有名字的语义定位」一样稳。
function statusHintBase(step) {
  const status = effectiveStatus(step)
  const chosen = pickFor(config.framework, step.target?.candidates || [])
  if (status === 'unique' && chosen?.kind === 'role_bare') {
    return '按 role 定位：页面上只有这一个同类元素。新增同类控件后会退化为「匹配 N 个取第 k 个」'
  }
  if (status === 'unique' && ['row_scoped', 'rf_row_scoped'].includes(chosen?.kind)) {
    return '按行锚定：先用这一行的业务字段锁定是哪一行，再在行内找目标。'
      + '列表增删、换个排序都不会指错行——比「第 N 行的按钮」稳，也是人工维护这类页面时的写法'
  }
  if (status === 'unique' && chosen?.kind === 'rf_id_loose') {
    return '按 id 定位：这个 id 里带数字。它可能是业务单号（一直有效），也可能是页面渲染时自增的序号'
      + '（重新加载就变了）。回放前请确认一次；如果这条元素还能按 name / aria / 文本定位，优先用那些'
  }
  if (status === 'page_gone') {
    return '录制时这条定位是有效的；停止录制时页面已经跳到别的地址，没法在当前页面复核。生成会用录制时的定位，建议回放确认。'
  }
  if (status === 'unique' && ['class_part', 'rf_class_part'].includes(chosen?.kind)) {
    return '按 class 名定位：取的是 CSS Modules / BEM 类名里去掉构建哈希的那半截，'
      + '改版重新打包也还在，且不依赖元素在第几个位置。它不是语义属性，页面改了类名仍会失效'
  }
  if (status === 'unique') return '语义定位，页面上唯一命中'
  if (status === 'fragile') return '结构定位：依赖页面层级，改版后可能失效'
  if (status === 'ambiguous') {
    return step.target?.page_gone
      ? '录制时匹配到多个元素；停止时页面已经翻走，没法复核。生成会按录制时的序号取，请回放确认'
      : '匹配到多个元素，生成时按录制时点的序号取'
  }
  if (status === 'unverified') {
    return step.target?.page_gone
      ? '录制时就没数到这个元素（页面当时已经翻走），停止时也回不到那页；定位按录制快照取，生成不受影响，回放时确认'
      : '这一步操作后元素就从页面上消失了（下拉/浮层收起、局部重渲染或跳转都算），没来得及数一次；定位按录制快照取，生成不受影响，回放时确认'
  }
  if (status === 'stale') return '重新校验时还找得到，但已经不是你录制时点的那个元素'
  if (status === 'suspect') {
    return '这几条「唯一」的定位，命中的元素都不是你点的那个（是页面上另一处同名/同结构的元素）。'
      + '把它写进脚本会点错地方，所以生成时不会采用——请点右侧准星重新框选，或手工改成正确的那条'
  }
  if (status === 'chosen') {
    const picked = step.target?.chosen || {}
    const hit = (step.target?.candidates || []).find(c => c.desc === picked.desc)
    const total = hit?.count
    const caveat = step.target?.page_gone ? '；停止时页面已经翻走，未能复核' : ''
    return `你确认过：页面上匹配到 ${total ?? '?'} 个，取第 ${(picked.index ?? 0) + 1} 个${caveat}`
  }
  if (status === 'invalid') {
    return '你选的序号，在停止录制时的页面上已经不存在了：生成出来会点不到，需要重新选'
  }
  return ''
}

// 计数取自按下鼠标那一刻的步骤：点完元素已经没了，徽标仍按当时的结果显示，
// 但要在提示里说清楚，否则用户会奇怪「为什么页面上框不出它」。
function statusHint(step) {
  const base = statusHintBase(step)
  return step.target?.vanished_after
    ? base + '（这个元素点完就从页面上消失了，计数取自按下鼠标那一刻）'
    : base
}

function statusTone(status) {
  // ambiguous 现在会降级生成（不再阻断），所以用警告色而不是红色；
  // suspect 是「看着唯一、其实指错了人」，写进脚本必错，用红色。
  return ({
    unique: 'success', fragile: 'warning', ambiguous: 'warning', unverified: 'warning',
    page_gone: 'warning', stale: 'info', chosen: 'success', invalid: 'danger', suspect: 'danger'
  })[status] || 'info'
}

// ---- 工作区检测 ----
async function detectWorkspace() {
  const workspace = (config.workspace || '').trim()
  if (!workspace) return
  detecting.value = true
  try {
    const { data } = await api.detectRecordingWorkspace(workspace, (config.python || '').trim())
    detect.value = data
    if (data.framework) config.framework = data.framework
  } catch (error) {
    detect.value = null
    ElMessage.warning(errorText(error))
  } finally {
    detecting.value = false
  }
}

// ---- 任务书浮层 ----
// 遮罩只是其中一半：panel=1 时本页就是那个 600 多宽的独立窗口，窗口内的 96vw
// 再大也大不过窗口，所以同时让后端把面板窗口铺宽，关掉再摆回并排。
// 窗口调不动（嵌入模式 / 系统拒绝 / 浏览器已关）不影响浮层本身，那是纯增强。
let sheetBodyOverflow = ''

function openPlanSheet() {
  if (planSheetOpen.value) return
  if (!planData.value && !shownPlanError.value) return
  planSheetOpen.value = true
  sheetBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  focusSheetWindow(true)
}

function closePlanSheet() {
  if (!planSheetOpen.value) return
  planSheetOpen.value = false
  document.body.style.overflow = sheetBodyOverflow || ''
  sheetBodyOverflow = ''
  clearSelTip()
  focusSheetWindow(false)
}

function focusSheetWindow(open) {
  // 失败不提示：用户此刻在看任务书，窗口能不能挪宽不是他要操心的事。
  api.focusRecordingSheet(open).catch(() => {})
}

function onSheetKeydown(event) {
  if (event.key !== 'Escape' || !planSheetOpen.value) return
  // 意见弹窗开着就先让它关：Esc 逐层退回，别一下把整份任务书也关掉。
  if (noteDialog.visible) return
  event.stopPropagation()
  closePlanSheet()
}

watch(planSheetOpen, (open) => {
  if (open) window.addEventListener('keydown', onSheetKeydown, true)
  else window.removeEventListener('keydown', onSheetKeydown, true)
})

// 录制重新开始 = 这一版任务书作废，浮层不能留在屏幕上让人对着旧步骤确认。
watch(isStopped, (stopped) => {
  if (!stopped) closePlanSheet()
})

// ---- 任务书：选中片段写意见 ----
// 不用自定义富文本：任务书本身是普通 DOM，浏览器选区就够用。
// 选中后浮出一个小按钮，点它把这一段原文带进意见框，用户只写「要改成什么」。
function clearSelTip() {
  selTip.visible = false
}

function onPlanSelect() {
  const selection = window.getSelection()
  const root = planBody.value
  if (!root || !selection || selection.isCollapsed) {
    clearSelTip()
    return
  }
  const text = selection.toString().trim()
  if (text.length < 2) {
    clearSelTip()
    return
  }
  // 选区必须完全落在任务书正文里，别把「意见列表」或页面别处的文字带进来。
  if (!root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) {
    clearSelTip()
    return
  }
  const rect = selection.getRangeAt(0).getBoundingClientRect()
  selTip.text = text.slice(0, 400)
  selTip.x = Math.min(Math.max(12, rect.left + rect.width / 2 - 62), window.innerWidth - 150)
  selTip.y = Math.max(12, rect.top - 38)
  selTip.visible = true
}

function openNote(quote) {
  noteDialog.quote = quote || ''
  noteDialog.comment = ''
  noteDialog.visible = true
  selTip.visible = false
}

function saveNote() {
  const comment = noteDialog.comment.trim()
  if (!comment) {
    ElMessage.warning('写一句你的要求，再添加')
    return
  }
  planNotes.value.push({ id: ++noteSeq, quote: noteDialog.quote, comment })
  noteDialog.visible = false
}

function removeNote(id) {
  planNotes.value = planNotes.value.filter(note => note.id !== id)
}

function adoptSuggestion() {
  if (!planSuggestion.value) return
  planTargetOverride.value = planSuggestion.value
  planTargetManual.value = false
  ElMessage.success(`这次会写到 ${planSuggestion.value}`)
}

function startEditPlanTarget() {
  planTargetDraft.value = planTarget.value
  planTargetError.value = ''
  planTargetEditing.value = true
}

function cancelEditPlanTarget() {
  planTargetEditing.value = false
  planTargetError.value = ''
}

function savePlanTarget() {
  const next = planTargetDraft.value.trim().replace(/\\/g, '/')
  if (!next) {
    planTargetError.value = '路径不能为空'
    return
  }
  if (next.startsWith('/') || /^[a-zA-Z]:/.test(next) || next.split('/').includes('..')) {
    planTargetError.value = '要填工作区内的相对路径，不能是绝对路径或带 ..'
    return
  }
  if (/[*?[\]]/.test(next)) {
    planTargetError.value = '路径里不能有 * ? [ ]'
    return
  }
  planTargetOverride.value = next === String(planData.value?.target_path || '') ? '' : next
  planTargetManual.value = true
  planTargetEditing.value = false
  planTargetError.value = ''
}

function notePayload() {
  return planNotes.value.map(({ quote, comment }) => ({ quote, comment }))
}

// ---- 规划：只出任务书，不写文件 ----
async function runPlan(replan) {
  if (generating.value) return
  planPending.value = true
  planError.value = null
  genError.value = null
  if (!replan) {
    planState.value = null
    planNotes.value = []
  }
  startPolling(1000)
  try {
    const { data } = await api.planRecording({
      model: config.model,
      workspace_path: config.workspace.trim(),
      framework: config.framework,
      // 留空 = 让规划按项目约定选；用户手改过就用他改的那条，重新规划也不丢。
      target_path: (planTargetOverride.value || config.targetPath.trim()),
      title: config.title.trim() || 'recorded flow',
      python: (config.python || '').trim() || undefined,
      // 重新规划时把上一版和用户意见一起带上，规划 agent 才知道要改哪里。
      previous_plan: replan ? planData.value : undefined,
      feedback: replan ? notePayload() : []
    })
    planState.value = { result: data, sig: stepsSignature.value, at: Date.now() }
    // 新一版任务书自带路径，上一轮的手改作废，否则会拿旧 override 覆盖新结果。
    planTargetOverride.value = ''
    planTargetManual.value = false
    planTargetEditing.value = false
    planTargetError.value = ''
    if (replan) {
      const count = planNotes.value.length
      planNotes.value = []
      ElMessage.success(count ? `已按你的 ${count} 条意见重新生成任务书` : '任务书已重新生成')
    } else {
      ElMessage.success('任务书已生成，确认后再执行')
    }
    await nextTick()
    // 生成完直接把任务书翻开：这里没有「先看摘要再点开」的必要，
    // 用户点的是「生成任务书」，产物就该立刻在眼前。
    openPlanSheet()
  } catch (error) {
    const status = error.response && error.response.status
    const details = (error.response && error.response.data && error.response.data.details) || []
    if (status === 409) {
      ElMessage.info('已有任务在进行，这里会同步显示进度')
      loadCurrent()
      return
    }
    planError.value = { message: errorText(error), details }
  } finally {
    planPending.value = false
    loadCurrent()
  }
}

// ---- 写入：只从任务书的「按此执行」进来 ----
async function executePlan(overwrite) {
  // 没有确认过的写入位置就不写：任务书的「按此执行」本身就是授权动作。
  if (!planData.value || !planTarget.value || planStale.value || generating.value) return
  if (!overwrite && blockers.value.length) {
    // 不唯一不再阻断生成，但要让用户知道哪几步会降级、降到什么程度。
    const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
    const lines = blockers.value.map((no) => {
      const step = steps.value.find(s => s.no === no)
      const plan = degradePlan(step)
      const alias = esc(step?.target?.alias || `第 ${no} 步`)
      if (step?.target?.chosen?.index_invalid) {
        // 你选过、但停止时元素少了：别只说「取第 3 个」，要点明它已经不存在。
        const total = plan ? plan.count : 0
        return `第 ${no} 步「${alias}」：你选的是第 ${step.target.chosen.index + 1} 个，`
          + `但页面上现在只有 ${total} 个，需要重新选`
      }
      if (!plan) return `第 ${no} 步「${alias}」：页面上已匹配不到，将写 TODO 注释`
      const nth = plan.index != null ? `取第 ${plan.index + 1} 个` : '取第 1 个'
      return `第 ${no} 步「${alias}」：匹配 ${plan.count} 个，${nth}`
    })
    const html = `${lines.join('<br>')}<br><br>`
      + '这几步会按「最窄候选 + 录制时的序号」生成，页面结构变了可能指错元素。'
      + '生成前可以先点步骤右侧的准星，在浏览器里把元素框出来确认。是否继续？'
    try {
      await ElMessageBox.confirm(html, '有步骤定位待确认', {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '仍然执行',
        cancelButtonText: '取消',
        type: 'warning'
      })
    } catch {
      return
    }
  }
  localPending.value = true
  // 点击到服务端任务起来之间有最多一次轮询的间隔，先把节奏提上来，进度条立刻有反应。
  startPolling(1000)
  genError.value = null
  genResult.value = null
  // 回放结果不在这里清：它验的是录制本身，生成不改录制，结论仍然成立。
  try {
    const { data } = await api.generateRecording({
      model: config.model,
      workspace_path: config.workspace.trim(),
      framework: config.framework,
      // 用户确认过的位置：可能来自规划，也可能是他在卡片上手改的。
      target_path: planTarget.value,
      title: config.title.trim() || 'recorded flow',
      python: (config.python || '').trim() || undefined,
      overwrite,
      plan: planData.value,
      // 用户在任务书上写的意见：不重新规划也要生效，直接带给写手。
      plan_feedback: notePayload()
    })
    genResult.value = data
    planNotes.value = []
    // 写完了：任务书这一版的任务也结束了，别让它继续盖在结果上（结果卡片在面板里）。
    closePlanSheet()
    if (data.outside && data.outside.length) {
      ElMessage.warning(`已写入 ${data.path}，但有 ${data.outside.length} 个目标以外的文件被改动`)
    } else {
      ElMessage.success(`已写入 ${data.path}`)
    }
  } catch (error) {
    const details = (error.response && error.response.data && error.response.data.details) || []
    const status = error.response && error.response.status
    if (details.includes('plan_stale')) {
      // 后端判定任务书与录制对不上：强制重新规划，别拿旧任务书硬写。
      if (planState.value) planState.value = { ...planState.value, stale: true }
      ElMessage.warning('录制步骤变过了，请重新规划任务书')
      return
    }
    const isConflict = status === 409 && details.some(d => String(d).startsWith('conflict:'))
    if (isConflict) {
      try {
        await ElMessageBox.confirm(
          `目标文件 ${planTarget.value} 已存在。覆盖后可以在「生成记录」里撤销，恢复原内容。是否覆盖？`,
          '文件已存在',
          { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' }
        )
      } catch {
        localPending.value = false
        return
      }
      localPending.value = false
      await executePlan(true)
      return
    }
    // 409 的另一半是「另一个窗口已经在生成」：那不是错误，跟着它的进度走就行。
    if (status === 409) {
      ElMessage.info('已有生成任务在进行，这里会同步显示进度')
      loadCurrent()
      return
    }
    genError.value = { message: errorText(error), details }
  } finally {
    localPending.value = false
    // 请求回来时服务端状态已经落定，立刻刷一次，别让按钮在这 1 秒里又变回可点。
    loadCurrent()
  }
}

// 回放用的候选必须和「要生成的那个框架」一致：生成时选 RF，回放就得用 rf_* 候选，
// 否则「校验通过」只能证明 Playwright 那套定位没坏，说明不了生成的脚本。
// 这里不再读生成结果：回放验的是录制本身，没有产物也该能跑。
const replayFramework = computed(() => config.framework)

// 暂停时也能跑：暂停期间不再收新步骤，IR 是稳定的，正是「先验一段再接着录」的时机。
// 录制中（没暂停）不放开——步骤一边录一边变，结论和界面上的步骤对不上。
const canReplay = computed(() => isStopped.value || (isRecording.value && paused.value))

const replayTitle = computed(() => {
  const result = replayResult.value
  if (!result) return ''
  if (result.ok) return `回放校验通过 · ${result.done}/${result.total} 步走通`
  if (result.failed_no) return `回放校验未通过 · 第 ${result.failed_no} 步（${result.failed_type || '?'}）`
  return '回放校验未执行'
})

// 回放之后步骤又变了（接着录、删步骤、改顺序）：结论只覆盖当时跑的那几步，
// 别让它看起来仍然代表整段录制。用步骤号快照判断，比数字数可靠。
const replayNos = ref('')
const replayStale = computed(() => (
  !!replayResult.value && replayNos.value !== steps.value.map((s) => s.no).join(',')
))
const replayExtra = computed(() => {
  const total = (replayResult.value && replayResult.value.total) || 0
  return steps.value.length > total ? steps.value.length - total : 0
})

// 回放要起浏览器把录制重跑一遍，步骤多时十几秒，必须给进度反馈。
async function runReplay() {
  if (replaying.value) return
  replaying.value = true
  replayResult.value = null
  replayOpen.value = false
  // 记下「跑的到底是哪几步」，回来后一比对就知道期间有没有又录了新的。
  replayNos.value = steps.value.map((s) => s.no).join(',')
  try {
    const { data } = await api.replayRecording({ framework: replayFramework.value })
    replayResult.value = data
    // 通过只关心结论，折叠；失败要直接看到是哪一步、什么错，展开。
    replayOpen.value = !data.ok
    if (data.ok) ElMessage.success(`回放校验通过：${data.done} 步全部走通`)
    else ElMessage.warning(`回放校验未通过：第 ${data.failed_no} 步`)
  } catch (error) {
    replayResult.value = {
      ok: false, done: 0, total: steps.value.length, steps: [],
      error: errorText(error), failed_no: null
    }
    replayOpen.value = true
  } finally {
    replaying.value = false
  }
}

function detailText(item) {
  const text = String(item)
  if (text.startsWith('ambiguous:')) return `第 ${text.split(':')[1]} 步定位待确认`
  if (text.startsWith('missing:')) return `第 ${text.split(':')[1]} 步：页面上已找不到目标元素`
  if (text.startsWith('conflict:')) return `目标文件已存在：${text.slice('conflict:'.length)}`
  return text
}

function errorText(error) {
  if (error && error.response && error.response.data && error.response.data.error) return error.response.data.error
  return (error && error.message) || '操作失败'
}

// 用户把两个窗口挪乱后，点「并排」重新摆一次。
async function relayoutPanel() {
  try {
    const { data } = await api.relayoutRecordingPanel()
    if (data.laid_out) ElMessage.success('已重新并排')
    else ElMessage.warning('无法调整窗口位置（可能已被关闭或系统拒绝）')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

// ---- 生命周期 ----
// 窗口变窄（多屏切换、退出全屏）时收一下面板，别把被测页面挤没。
function clampPanelWidth() {
  if (!embedMode.value) return
  const room = Math.max(340, Math.min(900, window.innerWidth - 900))
  if (panelWidth.value > room) panelWidth.value = room
}

onMounted(async () => {
  loadCurrent()
  clampPanelWidth()
  nextTick(initSortable)
  // 进度条只在生成中需要动：250ms 一跳够顺，平时空转不做事。
  tickTimer = setInterval(() => {
    if (emitRunning.value) nowTick.value = Date.now() / 1000
  }, 250)
  window.addEventListener('resize', clampPanelWidth)
  try {
    const { data } = await api.getAutomationModels()
    modelOptions.value = data.models || []
    if (!config.model && modelOptions.value.length) config.model = modelOptions.value[0].value
  } catch {
    modelOptions.value = []
  }
  if (config.workspace) detectWorkspace()
})

// 页面被 keep-alive 缓存：回到页面时刷新会话状态，离开时停止轮询。
onActivated(() => {
  pollEnabled = true
  loadCurrent()
})
onDeactivated(() => {
  pollEnabled = false
  stopPolling()
  stopEventStream()
  // 页面被缓存时浮层要收起来：body 上的滚动锁是全局的，留着会把整站锁死。
  closePlanSheet()
})
onUnmounted(() => {
  stopPolling()
  stopEventStream()
  clearInterval(tickTimer)
  window.removeEventListener('resize', clampPanelWidth)
  window.removeEventListener('keydown', onSheetKeydown, true)
  // 浮层开着就切走页面：body 的 overflow 是全局的，不还回去整站都滚不动。
  if (planSheetOpen.value) document.body.style.overflow = sheetBodyOverflow || ''
  clearTimeout(flashTimer)
  if (sortable) {
    sortable.destroy()
    sortable = null
  }
  // 页面里的框是浮层，不清掉会一直留在被测页面上。
  if (peek.value.no) api.clearRecordingHighlight().catch(() => {})
})

// 展开的那条步骤被删掉、或者列表整体换掉时，把浮层一并收掉：
// 面板会跟着 v-if 消失，但页面上的框不会自己不见。
watch(steps, (list) => {
  if (peek.value.no && !list.some(s => s.no === peek.value.no)) closePeek()
})
</script>

<style scoped>
.recording-page { max-width:1440px; margin:0 auto; animation: page-enter .32s ease both; }

/* 按钮的基础规范见 styles/buttons.css（本页根元素挂了 ui-buttons 类）；
   下面只放本页特有的按钮形态。 */

/* 卡片头两侧的轻量按钮（面板模式的「并排 / 配置」） */
.is-panel .panel-head :deep(.el-button.is-text) { height: 26px; padding: 0 8px; font-size: 12.5px; }
@keyframes page-enter { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

/* ===== 嵌入模式：单窗口，左边被测系统、右边可收起的编排面板 ===== */
.recording-page.is-embed {
  max-width:none; padding:0; height:100vh; display:flex; flex-direction:row;
  animation:none; background:#eef1f5; overflow:hidden;
}
.embed-frame { flex:1; min-width:0; height:100vh; border:0; background:#fff; display:block; }
.is-embed .page-col { flex:none; height:100vh; overflow:hidden; background:#f5f7fa; border-left:1px solid #e2e6ec;
  /* 「收起 / 展开」时面板宽度平滑过渡，iframe 跟着一起伸缩；拖动分栏时必须关掉，否则手感发飘 */
  transition:width .26s cubic-bezier(.4,0,.2,1); }
.is-embed.is-resizing .page-col { transition:none; }
.is-embed .page-col.is-panel { display:flex; flex-direction:column; gap:10px; padding:10px 12px 12px; box-sizing:border-box; }
/* 拖拽手柄要 position: relative，否则 z-index 不生效、会被 iframe 盖住点不到 */
.embed-resizer { flex:none; position:relative; width:6px; margin-left:-4px; z-index:6; cursor:col-resize; }
.embed-resizer::after { content:''; position:absolute; inset:0 2px; border-radius:2px; background:transparent; transition:background .15s ease; }
.embed-resizer:hover::after { background:#9fc0ee; }
/* 拖动期间让 iframe 不参与命中测试，避免 pointermove 被 iframe 吃掉 */
.is-embed.is-resizing .embed-frame { pointer-events:none; }
.is-embed.is-resizing .embed-resizer { cursor:col-resize; }
.embed-rail {
  flex:none; width:36px; height:100vh; border:0; border-left:1px solid #e2e6ec; background:#fff;
  display:flex; flex-direction:column; align-items:center; gap:12px; padding:14px 0; cursor:pointer; color:#6e6e73;
}
.embed-rail:hover { background:#f4f8ff; color:#0071e3; }
.rail-caret { font-size:16px; line-height:1; }
.rail-count { font-size:11.5px; font-weight:700; font-variant-numeric:tabular-nums; color:#8a8f99; }
.rail-dot { width:7px; height:7px; border-radius:50%; background:#c7ccd6; margin-top:auto; }
.rail-dot.live { background:#34c759; box-shadow:0 0 0 3px rgba(52,199,89,.18); }
.panel-collapse { border:0; background:transparent; color:#8a8f99; cursor:pointer; padding:2px 4px; border-radius:6px; line-height:1; }
.panel-collapse:hover { background:#eef1f5; color:#0071e3; }
.collapse-arrow { font-size:16px; }

/* 面板模式：录制时并排的右侧编排栏 =====
   只有一列，步骤列表吃掉剩余高度；配置默认收起，靠顶部「配置」按钮展开。 */
.recording-page.is-panel {
  max-width:none; height:100vh; padding:10px 12px 12px; display:flex; flex-direction:column; gap:10px;
  animation:none; box-sizing:border-box;
}
.panel-head { flex:none; display:flex; align-items:center; gap:8px; padding:6px 10px; border:1px solid #e6e9ef; border-radius:10px; background:#fff; box-shadow:0 1px 2px rgba(16,24,40,.05); }
.panel-dot { width:8px; height:8px; border-radius:50%; background:#c7ccd6; flex:none; }
.panel-dot.is-live { background:#34c759; box-shadow:0 0 0 3px rgba(52,199,89,.18); animation:panel-pulse 1.8s ease-in-out infinite; }
.panel-dot.is-paused { background:#ff9f0a; box-shadow:0 0 0 3px rgba(255,159,10,.2); }
@keyframes panel-pulse { 0%,100% { opacity:1; } 50% { opacity:.45; } }
.panel-title { font-size:13px; font-weight:700; color:#1d1d1f; }
.panel-count { font-size:11.5px; color:#6e6e73; background:#f2f4f7; border-radius:999px; padding:1px 8px; }
.panel-warn { font-size:11.5px; color:#b45309; background:#fff4e6; border-radius:999px; padding:1px 8px; }
.panel-actions { margin-left:auto; display:flex; gap:2px; }
.is-panel .recording-body, .is-embed .page-col .recording-body { display:flex; flex-direction:column; gap:10px; flex:1; min-height:0; }
.is-panel .main-layout { display:flex; flex-direction:column; gap:10px; flex:1; min-height:0; }
.is-panel .panel-card { margin-bottom:0; border-radius:10px; }
.is-panel .panel-card :deep(.el-card__header) { padding:10px 14px; }
.is-panel .panel-card :deep(.el-card__body) { padding:12px; }
.is-panel .steps-card { flex:1; min-height:0; display:flex; flex-direction:column; }
/* 整个卡片内容区作为唯一的滚动容器：列表和草稿一起滚，工具栏吸顶。
   之前列表自己滚、草稿另算，窄栏里草稿会被卡片裁掉。 */
.is-panel .steps-card :deep(.el-card__body) { flex:1; min-height:0; display:flex; flex-direction:column; overflow-y:auto; }
.is-panel .generate-card { flex:none; }
.is-panel .timeline { flex:none; overflow:visible; margin-top:12px; padding-right:2px; }
/* 顶栏已经有状态和步数，卡片头里的重复信息收起来 */
.is-panel .steps-card .state-pill { display:none; }
/* 工具栏在窄栏里收小一号，尽量一行放完；滚动时吸顶 */
.is-panel .toolbar {
  flex-wrap:wrap; gap:6px; position:sticky; top:-12px; z-index:2;
  background:#fff; padding:12px 0 8px; margin:-12px 0 0;
}
.is-panel .toolbar :deep(.el-button) { padding:6px 10px; font-size:12.5px; height:30px; }
.is-panel .config-card :deep(.el-card__body) { padding:14px; }
/* 面板/嵌入模式只有 600px 宽，配置改回单列 */

/* 页头：与「自动化脚本」页保持一致 */
/* 页头收一档：小字从蓝色大写标签改成灰色注脚，标题靠字重而不是字号抢视线 */
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:20px; }
.eyebrow { color:var(--muted); font-size:10.5px; font-weight:600; letter-spacing:.09em; margin-bottom:6px; text-transform:uppercase; }
h1 { margin:0; color:#15181d; font-size:22px; font-weight:600; letter-spacing:-.02em; }
.page-header p { margin:6px 0 0; color:#7a8494; font-size:12.5px; line-height:1.6; max-width:820px; }
.page-intro { min-width:0; }
.page-actions { display:flex; align-items:center; gap:8px; flex:none; padding-top:2px; }
.hint-dot { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; margin-left:3px; border-radius:50%; background:#eef1f6; color:#86909c; font-size:10px; font-weight:700; cursor:help; vertical-align:1px; }

/* 卡片 */
.panel-card { border:1px solid #ebedf0; border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04), 0 10px 30px rgba(16,24,40,.05); margin-bottom:20px; }
.panel-card :deep(.el-card__header) { padding:11px 16px; border-bottom:1px solid #f0f2f5; background:linear-gradient(180deg,#fcfdff,#f7f9fc); }
.panel-card :deep(.el-card__body) { padding:14px 16px; }
.section-header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.steps-head-right { display:flex; align-items:center; gap:8px; flex:none; }
/* 「收起」按钮：24px 高的小文字按钮，和状态标签同一基线 */
.recording-page :deep(.el-button.steps-collapse-btn.is-text) {
  height:24px;
  padding:0 6px;
  margin-right:-6px;
  border-radius:6px;
  color:#6e6e73;
  font-size:12px;
  font-weight:500;
}
.recording-page :deep(.el-button.steps-collapse-btn.is-text:hover) { background:#f2f7ff; color:#0071e3; }
.card-title { position:relative; padding-left:12px; font-size:15px; font-weight:700; color:#1d1d1f; }
.card-title::before { content:''; position:absolute; left:0; top:50%; transform:translateY(-50%); width:4px; height:15px; border-radius:3px; background:linear-gradient(180deg,#0a84ff,#0071e3); }
.field-hint { color:#909399; font-size:12px; line-height:1.5; margin-top:6px; }
.root-hint { margin-left:6px; color:#a8abb2; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11.5px; }

/* ===== 配置区（静物）=====
   默认是一排「标签 + 值」，点开才换成输入框。整块只有两种分隔线：行与行之间的细线、
   编辑态的下划线；不再给每个字段套一个盒子，也不再用蓝色竖条标记卡片标题。 */
.config-card {
  /* 全部指向 tokens.css：这套「静物」语言现在要铺到别的页面，值只能有一处定义 */
  --cfg-ink: var(--ink);
  --cfg-muted: var(--muted);
  --cfg-line: var(--line);
  --cfg-line-soft: var(--line-soft);
  --cfg-accent: var(--accent);
  --cfg-mono: var(--mono);
}
/* 卡片撑满左列：主行动要贴住卡片底边，否则下面会空出一大块白 */
.config-card { display:flex; flex-direction:column; }
.config-card :deep(.el-card__header) { flex:none; padding:14px 20px; background:#fff; }
.config-card :deep(.el-card__body) { flex:1; display:flex; flex-direction:column; padding:2px 20px 18px; }
.config-card .card-title { font-size:14px; font-weight:600; }
.config-card .card-title::before { display:none; }
.cfg-saved { margin-left:auto; font-size:11.5px; color:var(--cfg-muted); }

.cfg-form { flex:1; display:flex; flex-direction:column; }
.cfg-field { padding:13px 0 2px; border-bottom:1px solid var(--cfg-line-soft); }
.cfg-field:last-of-type { border-bottom:0; }
.cfg-field--sub { border-bottom:0; padding-top:10px; }
.cfg-label { font-size:11.5px; color:var(--cfg-muted); letter-spacing:.01em; }
.cfg-view { display:flex; align-items:center; gap:10px; height:32px; cursor:text; }
.cfg-text { flex:1; min-width:0; font-size:13px; color:var(--cfg-ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; transition:color .14s ease; }
.cfg-view:hover .cfg-text { color:#000; }
/* 工作区只能通过「更换」选目录，这一行本身不可编辑：光标和悬停态都不能骗人 */
.cfg-view.is-static { cursor:default; }
.cfg-view.is-static:hover .cfg-text { color:var(--cfg-ink); }
.cfg-text.is-empty { color:#b3bac5; }
.cfg-field--hero .cfg-text { font-size:14px; }
.cfg-mono { font-family:var(--cfg-mono); font-size:12.5px; letter-spacing:-.01em; }
/* 「编辑」只在悬停时出现：默认状态是一张摘要，不该有七个提示在喊 */
.cfg-hint { flex:none; font-size:11.5px; color:#c2c8d2; opacity:0; transition:opacity .14s ease, color .14s ease; }
.cfg-view:hover .cfg-hint { opacity:1; color:var(--cfg-accent); }
.cfg-acts { display:flex; gap:12px; flex:none; }
.cfg-act { border:0; background:none; padding:0; font-size:11.5px; color:#9aa3b1; cursor:pointer; transition:color .14s ease; }
.cfg-act:not(:disabled):hover { color:var(--cfg-accent); }
.cfg-act:disabled { color:#d3d8e0; cursor:default; }

/* 编辑态：只剩一条下划线，聚焦时变蓝加粗 */
/* 与只读行同高（32px）：切换编辑时下面的行不能跳一下 */
.cfg-input { height:32px; }
.cfg-input :deep(.el-input__wrapper) {
  height:32px; padding:0 0 5px; background:transparent; border-radius:0; box-shadow:none;
  border-bottom:1px solid var(--cfg-line);
}
.cfg-input :deep(.el-input__wrapper.is-focus) {
  border-bottom:1.5px solid var(--cfg-accent); padding-bottom:4.5px;
}
.cfg-input :deep(.el-input__inner) { font-family:var(--cfg-mono); font-size:12.5px; color:var(--cfg-ink); }
.cfg-input :deep(.el-input__inner::placeholder) { font-family:var(--cfg-mono); color:#b3bac5; }
.cfg-select { width:100%; height:32px; }
.cfg-select :deep(.el-select__wrapper) {
  min-height:32px; height:32px; padding:0 0 5px; background:transparent; box-shadow:none; border-radius:0;
  border-bottom:1.5px solid var(--cfg-accent);
}

/* 框架：只有两个取值，分段控制器比下拉少一次点击，也让「当前用哪套」一直可见 */
.cfg-seg { display:inline-flex; gap:2px; margin:8px 0 10px; padding:2px; border-radius:9px; background:#f2f4f7; }
.cfg-seg-item { border:0; background:transparent; padding:6px 12px; border-radius:7px; font-size:12px; color:#7a8494; cursor:pointer; transition:background .14s ease, color .14s ease; }
.cfg-seg-item:hover { color:var(--cfg-ink); }
.cfg-seg-item.is-on { background:#fff; color:var(--cfg-ink); box-shadow:0 1px 2px rgba(16,24,40,.09); }

/* 检测结果：小方片而不是药丸——药丸太像标签，杂在表单里更吵 */
.cfg-detect { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:0 0 10px; }
.cfg-chip { display:inline-flex; align-items:center; height:20px; padding:0 7px; border-radius:5px; background:#f4f6f9; color:#5b6472; font-size:11px; font-family:var(--cfg-mono); max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cfg-chip.is-ok { background:#edf7f1; color:#1e7a46; }
.cfg-chip.is-warn { background:#fdf6ea; color:#ad6800; }
.cfg-details { flex-basis:100%; margin-top:4px; font-size:11.5px; color:#7a8494; }
.cfg-details summary { cursor:pointer; color:#98a1b0; }

/* 高级项：折成一行，右侧直接写当前值，不展开也知道用的是哪个解释器 */
.cfg-adv { display:flex; align-items:center; gap:6px; width:100%; margin-top:12px; padding:10px 0 2px; border:0; background:none; font-size:11.5px; color:#98a1b0; cursor:pointer; text-align:left; }
.cfg-adv:hover { color:var(--cfg-accent); }
.cfg-caret { display:inline-flex; transform:rotate(-90deg); transition:transform .16s ease; }
.cfg-caret.is-open { transform:rotate(0deg); }
.cfg-adv-value { margin-left:auto; font-family:var(--cfg-mono); color:#b3bac5; }

/* 主行动：整宽收在配置末尾，它是上面这些设置的出口 */
.cfg-foot { margin-top:auto; padding-top:16px; border-top:1px solid var(--cfg-line-soft); }
.config-card .el-button.cfg-start { width:100%; height:38px; border-radius:9px; font-size:13px; letter-spacing:0; }
code { font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; font-size:12px; }
.detect-sample { color:#1d1d1f; font-family: ui-monospace, Consolas, monospace; font-size:12px; line-height:1.7; }
.evidence-line { display:flex; gap:8px; padding:3px 0; }
.evidence-kind { color:#303133; font-weight:600; min-width:110px; }
.evidence-source { color:#a1a1a6; margin-left:auto; }

/* 主布局 */
.main-layout { display:flex; flex-direction:column; gap:16px; min-width:0; }
/* 左配置 / 右步骤+生成：一屏看完，生成记录单独成页 */
/* 普通模式整页锁一屏：标题常驻，左右两列各自内部滚动，步骤再多也不把页面撑长。
   100dvh - 56(顶栏) - 44(上下留白 22×2) = 内容高度 */
.recording-page:not(.is-panel):not(.is-embed) { height:calc(100dvh - 100px); display:flex; flex-direction:column; }
/* page-col 是内容的外壳（embed 模式也用它）：普通模式下必须把高度传下去，链条才不断 */
.recording-page:not(.is-panel):not(.is-embed) .page-col { flex:1; min-height:0; display:flex; flex-direction:column; }
.recording-page:not(.is-panel):not(.is-embed) .page-col > .page-header { flex:none; }
.recording-body { display:grid; grid-template-columns:minmax(340px,400px) minmax(0,1fr); grid-template-rows:minmax(0,1fr); gap:16px; align-items:stretch; flex:1; min-height:0; }
/* 左列配置超高时自己滚 */
.recording-body .config-card { min-height:0; overflow-y:auto; }
/* 右列：步骤吃满剩余高度，生成卡贴底 */
.recording-body .main-layout { height:100%; min-height:0; }
.recording-body .steps-card { flex:1; min-height:0; display:flex; flex-direction:column; }
.recording-body .steps-card :deep(.el-card__body) { flex:1; min-height:0; overflow-y:auto; }
.recording-body .generate-card { flex:none; }
.recording-body .panel-card { margin-bottom:0; }
.gen-result-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:8px; }
.gen-result-actions .field-hint { margin-top:0; }
/* 回放校验：和生成无关，停止录制后就摆在这里，别让它看起来像生成的附属动作。 */
.replay-block { margin-top:12px; padding-top:12px; border-top:1px dashed #e6ebf2; }
.replay-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.replay-row .field-hint { margin-top:0; }
/* 回放后又接着录了：结论变旧，别让它继续冒充整段录制的结果 */
.replay-stale { margin:6px 0 0; font-size:12px; color:#b45309; }

/* 状态 */
.state-pill { display:inline-flex; align-items:center; height:24px; padding:0 10px; border-radius:999px; font-size:12px; font-weight:700; background:#f2f4f7; color:#6e6e73; }
.state-recording { background:#fff1f0; color:#e5484d; }
.state-stopped, .state-idle_timeout, .state-closed { background:#eaf7ef; color:#1e7a46; }
.state-failed, .state-cancelled { background:#fff7e6; color:#ad6800; }

/* 工具栏：两个浅色底分组（浏览器控制 / 插入步骤）。
   组内按钮常态无边框无底色，悬停才浮起白底——样式只留两档（实心 + ghost），
   比原来「八个同权重描边按钮」干净。 */
.toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; transition:gap .2s ease; }
.toolbar-group { display:flex; flex-wrap:wrap; align-items:center; gap:2px; padding:4px; border-radius:10px; background:#f5f6f9; border:1px solid #edeff3; }
.toolbar-group :deep(.el-button) { margin-left:0; height:32px; padding:0 10px; background:transparent; border-color:transparent; box-shadow:none; color:#3c4450; }
.toolbar-group :deep(.el-button + .el-button) { margin-left:0; }
/* 图标与文字统一 5px：Element Plus 默认给 icon+span 加 6px margin，和 flex gap 会叠成 11px */
.toolbar-group :deep(.el-button) { display:inline-flex; align-items:center; justify-content:center; gap:5px; }
.toolbar-group :deep(.el-button .el-icon + span) { margin-left:0; }
.toolbar-group :deep(.el-button .el-icon) { font-size:15px; flex:none; }
.toolbar-group :deep(.el-button:not(.is-disabled):hover) { background:#fff; border-color:#e1e6ef; color:#1d1d1f; box-shadow:0 1px 2px rgba(16,24,40,.08); }
.toolbar-group :deep(.el-button.is-disabled) { background:transparent; border-color:transparent; color:#b5bcc7; }
/* 实心按钮和激活态必须写在 ghost 悬停之后：选择器权重相同，靠顺序取胜 */
.toolbar-group :deep(.el-button--primary) { background:#0071e3; border-color:#0071e3; color:#fff; }
.toolbar-group :deep(.el-button--primary:not(.is-disabled):hover) { background:#0060c8; border-color:#0060c8; color:#fff; }
.toolbar-group :deep(.el-button--danger) { background:#e5484d; border-color:#e5484d; color:#fff; }
.toolbar-group :deep(.el-button--danger:not(.is-disabled):hover) { background:#d33a3f; border-color:#d33a3f; color:#fff; }
/* 激活态（正在拾取 / 已暂停）用浅色底，不用实心：否则一排里会出现三个重色块 */
.toolbar-group :deep(.el-button--warning.is-plain) { background:#fff7e6; border-color:#f3c98b; color:#b45309; }
.toolbar-group :deep(.el-button--warning.is-plain:not(.is-disabled):hover) { background:#fff2d9; border-color:#eebf74; color:#a15c07; }
.toolbar-group :deep(.el-button--success.is-plain) { background:#eaf7ef; border-color:#bfe3ce; color:#1e7a46; }
.toolbar-group :deep(.el-button--success.is-plain:not(.is-disabled):hover) { background:#e2f4ea; border-color:#a9dbc0; color:#196b3d; }
/* 停止录制里的白色方块：录制的通用记号，比再找一个图标更容易认 */
.rec-stop { width:8px; height:8px; border-radius:2px; background:currentColor; flex:none; }
/* 分组底块本身就是分隔，展开状态不再画竖线 */
.toolbar-sep { display:none; }
/* 单行 flex 切成 3 列网格属于重排，没法补间；给按钮一个归位动画，切换时就不会「啪」地跳。
   两个 keyframes 内容一样但名字不同——名字一变动画就会重播，收起和展开都能各放一次。 */
.toolbar :deep(.el-button) { animation:toolbar-settle .18s ease both; }
.toolbar.is-lean :deep(.el-button) { animation-name:toolbar-settle-lean; }
@keyframes toolbar-settle { from { opacity:.5; transform:translateY(1.5px); } to { opacity:1; transform:none; } }
@keyframes toolbar-settle-lean { from { opacity:.5; transform:translateY(1.5px); } to { opacity:1; transform:none; } }

/* 收起后工具栏排成两行三列，列宽一致；中间的竖线换成一条通栏细线，
   正好把「录制控制 / 步骤操作」分成上下两组，窄到 420px 也不会散架。
   分组容器用 display:contents 退化成排版占位，按钮重新回到同一张网格里排队。 */
.toolbar.is-lean {
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:6px;
  align-items:stretch;
}
.toolbar.is-lean .toolbar-group { display:contents; }
.toolbar.is-lean :deep(.el-button) {
  width:100%;
  min-width:0;
  padding:0 5px;
  font-size:12.5px;
  overflow:hidden;
  background:#fff;
  border-color:#e4e7ed;
}
.toolbar.is-lean :deep(.el-button:not(.is-disabled):hover) { background:#f7f9fc; border-color:#d8dde6; }
/* 收起态也要留住实心 / 激活态的底色，否则实心按钮被刷成白底白字 */
.toolbar.is-lean :deep(.el-button--primary) { background:#0071e3; border-color:#0071e3; color:#fff; }
.toolbar.is-lean :deep(.el-button--primary:not(.is-disabled):hover) { background:#0060c8; border-color:#0060c8; color:#fff; }
.toolbar.is-lean :deep(.el-button--danger) { background:#e5484d; border-color:#e5484d; color:#fff; }
.toolbar.is-lean :deep(.el-button--danger:not(.is-disabled):hover) { background:#d33a3f; border-color:#d33a3f; color:#fff; }
.toolbar.is-lean :deep(.el-button--warning.is-plain) { background:#fff7e6; border-color:#f3c98b; color:#b45309; }
.toolbar.is-lean :deep(.el-button--success.is-plain) { background:#eaf7ef; border-color:#bfe3ce; color:#1e7a46; }
.toolbar.is-lean :deep(.el-button > span) { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
.toolbar.is-lean .toolbar-sep { display:block; grid-column:1 / -1; width:100%; height:1px; margin:2px 0; background:#e6e9ee; }
/* 未录制时这一组只有两个按钮：主按钮占两列补齐整行，右边界正好和下面「添加等待」对齐 */
.toolbar.is-lean :deep(.lean-wide) { grid-column:span 2; }
.pick-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#fff7e6; color:#ad6800; font-size:12.5px; line-height:1.5; }
/* 停止后浏览器还开着：这条提示必须常驻，否则用户会以为窗口是遗留的、直接关掉 */
.keep-open-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#eef6ff; color:#175cd3; font-size:12.5px; line-height:1.5; }
.pause-hint { margin-top:12px; padding:9px 12px; border-radius:9px; background:#fff4e6; color:#b45309; border:1px dashed #f3c98b; font-size:12.5px; line-height:1.5; }
/* 解析器状态：一行 16px 的小字，就绪时几乎是背景音，只有启动中/不可用时才显眼 */
.ask-hint { display:flex; align-items:center; gap:6px; margin-top:7px; font-size:11.5px; color:#9aa1ab; line-height:1.4; }
.ask-hint .ask-dot { flex:none; width:6px; height:6px; border-radius:50%; background:#c6ccd6; }
.ask-hint.ready .ask-dot { background:#34a853; }
.ask-hint.starting .ask-dot, .ask-hint.warming .ask-dot { background:#ef9f1e; animation:ask-pulse 1.2s ease-in-out infinite; }
.ask-hint.failed { color:#b45309; }
.ask-hint.failed .ask-dot { background:#e5484d; }
@keyframes ask-pulse { 0%, 100% { opacity:.35; } 50% { opacity:1; } }
.session-error { margin-top:12px; }

/* 空状态：一行提示，别用 el-empty 那张大插画占半屏 */
.steps-empty { display:flex; align-items:center; gap:8px; margin-top:14px; padding:12px 14px; border:1px dashed #e4e7ed; border-radius:10px; background:#fbfcfe; color:#909399; font-size:13px; }
.steps-empty-dot { flex:none; width:7px; height:7px; border-radius:50%; background:#c9d6ea; }
/* 宽屏首屏右侧本来就空：给一句说明，而不是一条空横线 */
.steps-empty.is-roomy { flex-direction:column; align-items:center; gap:8px; margin-top:0; padding:72px 24px; border:0; background:transparent; text-align:center; }
.steps-empty.is-roomy .steps-empty-dot { width:9px; height:9px; margin-bottom:6px; background:#dbe4f2; box-shadow:0 0 0 6px rgba(201,214,234,.22); }
.steps-empty-title { font-size:13px; color:#5d6675; }
.steps-empty-sub { font-size:12px; color:#9aa3b1; line-height:1.75; max-width:330px; }

/* 时间线 */
.timeline { list-style:none; margin:12px 0 0; padding:0; display:flex; flex-direction:column; gap:8px; }
.step { display:flex; gap:10px; align-items:flex-start; padding:10px 10px 10px 9px; border:1px solid #ebedf0; border-radius:12px; background:#fff; transition:border-color .18s ease, box-shadow .18s ease, padding .22s ease, gap .22s ease; }
.step:hover { border-color:#dce7fb; box-shadow:0 4px 14px rgba(16,24,40,.06); }
/* 不唯一现在是「可降级生成」，用琥珀色提示而不是红色阻断 */
.step.is-blocked { border-color:#f3d8a8; background:#fffdf6; }
.step-no { flex-shrink:0; width:24px; height:24px; border-radius:50%; background:#eef4ff; color:#0071e3; font-size:12px; font-weight:700; display:flex; align-items:center; justify-content:center; font-variant-numeric:tabular-nums; }
.step-body { flex:1; min-width:0; }
.step-main { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.step-text { color:#1d1d1f; font-size:14px; line-height:1.5; word-break:break-word; }
.step-note { margin-top:4px; color:#6e6e73; font-size:12.5px; }
.step-locator { margin-top:8px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.step-alias { color:#303133; font-size:12.5px; font-weight:600; }
.locator-code { color:#6e6e73; font-size:11.5px; background:#f7f9fc; border:1px solid #eef0f4; border-radius:6px; padding:2px 6px; word-break:break-all; }
.step-actions { display:flex; flex-direction:row; align-items:center; gap:2px; flex-shrink:0; }
/* 定位预览：行内小准星，点一下在浏览器里把匹配元素全框出来并编号 */
.peek-btn { height:22px; width:22px; padding:0; margin-left:-2px; color:#8e8e93; }
.peek-btn:hover { color:#0071e3; background:#eaf3ff; }
.chosen-warn { color:#b45309; font-size:11.5px; font-weight:600; }
/* 中心被遮挡后自动上提过目标：不是错误，但用户该知道定位换了元素，否则看脚本会对不上。 */
.lifted-hint { color:#6b7280; font-size:11.5px; font-weight:600; border-bottom:1px dashed #c3c9d4; cursor:help; }
.peek-panel {
  margin-top:8px; padding:9px 10px; border-radius:9px;
  background:#f7f9fc; border:1px solid #e6ebf2;
}
.peek-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.peek-title { font-size:12.5px; color:#303133; }
.peek-desc {
  font-size:11px; color:#6e6e73; background:#fff; border:1px solid #eef0f4;
  border-radius:5px; padding:1px 5px; word-break:break-all;
}
.peek-close { margin-left:auto; height:20px; padding:0 4px; }
.peek-items { margin-top:7px; display:flex; flex-direction:column; gap:4px; }
.peek-item {
  display:flex; align-items:center; gap:8px; width:100%; text-align:left;
  padding:4px 8px; border-radius:7px; border:1px solid #e6ebf2; background:#fff;
  cursor:pointer; font-size:12px; color:#3c4048; font-family:inherit;
}
.peek-item:hover { border-color:#b9d6f7; background:#f4f9ff; }
.peek-item.is-active { border-color:#ff3b30; background:#fff1f0; color:#b42318; }
.peek-no {
  flex:none; width:16px; height:16px; border-radius:4px; background:#e8eaed; color:#5f6368;
  font-size:10.5px; line-height:16px; text-align:center; font-weight:600;
}
.peek-item.is-active .peek-no { background:#ff3b30; color:#fff; }
.peek-text { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.peek-hidden { flex:none; font-size:10.5px; color:#9aa0a6; }
.peek-more { margin-top:6px; font-size:11.5px; color:#8e8e93; }

/* ===== 定位待确认卡片 =====
   固定在右上角：嵌入模式下正好压在编排栏上，被测页面里那些带编号的框一个都不挡。
   高度封顶 + 列表自己滚，匹配到几十个也不会把整屏吃掉。 */
.loc-confirm {
  position:fixed; top:12px; right:12px; z-index:60; box-sizing:border-box;
  width:min(400px, calc(100vw - 24px)); max-height:min(70vh, 520px);
  display:flex; flex-direction:column; gap:7px; padding:12px 13px; overflow:hidden;
  background:#fff; border:1px solid #f0c987; border-radius:14px;
  box-shadow:0 16px 40px rgba(16,24,40,.18), 0 2px 6px rgba(16,24,40,.06);
  animation:loc-confirm-in .18s ease both;
}
@keyframes loc-confirm-in { from { opacity:0; transform:translateY(-6px); } to { opacity:1; transform:none; } }
.loc-head { display:flex; align-items:center; gap:8px; min-width:0; }
.loc-tag { flex:none; font-size:11px; font-weight:700; color:#b42318; background:#fff1f0; border-radius:6px; padding:2px 7px; }
.loc-step { min-width:0; font-size:12.5px; font-weight:600; color:#1d1d1f; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.loc-close { margin-left:auto; flex:none; }
.loc-lead { margin:0; font-size:12px; line-height:1.55; color:#6e6e73; }
.loc-lead b { color:#b42318; font-weight:600; }
.loc-list { display:flex; flex-direction:column; gap:4px; flex:1 1 auto; min-height:0; overflow-y:auto; max-height:34vh; padding-right:2px; }
.loc-item {
  display:flex; align-items:center; gap:8px; width:100%; text-align:left; flex:none;
  padding:6px 8px; border-radius:8px; border:1px solid #e6ebf2; background:#fff;
  cursor:pointer; font-size:12.5px; color:#3c4048; font-family:inherit;
}
.loc-item:hover { border-color:#b9d6f7; background:#f4f9ff; }
.loc-item.is-picked { border-color:#ff3b30; background:#fff1f0; color:#b42318; }
.loc-no {
  flex:none; width:18px; height:18px; border-radius:5px; background:#e8eaed; color:#5f6368;
  font-size:10.5px; line-height:18px; text-align:center; font-weight:600;
}
.loc-item.is-picked .loc-no { background:#ff3b30; color:#fff; }
.loc-text { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.loc-origin { flex:none; font-size:10.5px; color:#8a8f99; background:#f2f4f7; border-radius:4px; padding:1px 5px; }
.loc-hidden { flex:none; font-size:10.5px; color:#9aa0a6; }
.loc-note { margin:0; font-size:11.5px; line-height:1.5; color:#1e7a46; }
/* 页面跑掉了：不是错误，是「有补救办法」，用提示色而不是错误红 */
.loc-gone {
  margin:0; padding:6px 8px; border-radius:7px;
  background:#fff9ea; border:1px solid #f1ddb0; color:#7a5a12;
  font-size:11.5px; line-height:1.6;
}
.loc-foot { display:flex; align-items:center; gap:6px; }
.loc-queue { margin-right:auto; font-size:11.5px; color:#b45309; }
.loc-actions { margin-left:auto; display:flex; align-items:center; gap:6px; }

/* 次要操作组（上移 / 下移）：收起时用 max-width 收进去，文字跟着回流而不是瞬移。
   两个 30px 按钮之间还有 Element Plus 的 12px 相邻间距，实测 72px，上限给 84px 留余量。 */
.step-actions-extra {
  display:flex; align-items:center; gap:2px; flex:none; overflow:hidden;
  max-width:84px; opacity:1;
  transition:max-width .24s cubic-bezier(.4,0,.2,1), opacity .16s ease, margin-left .24s cubic-bezier(.4,0,.2,1);
}
.step-actions-extra :deep(.el-button) { flex:none; }
.step-actions-extra.is-off { max-width:0; opacity:0; margin-left:-2px; pointer-events:none; }
/* 删除在收起态也常驻：常态灰色，悬停才转红——每行一个红图标太吵，但得随手能删 */
.recording-page .step-actions .step-delete-btn:not(.is-disabled) { color:#a5abb6; }
.recording-page .step-actions .step-delete-btn:not(.is-disabled):hover { color:#e5484d; background:#fff1f0; }
/* 「在这步之后插入」：位置常驻（避免 hover 时整行文字回流），只有图标淡入 */
.step-insert-btn { opacity:0; transition:opacity .15s ease; }
.step:hover .step-insert-btn, .step-insert-btn:focus-within { opacity:1; }
@media (hover:none) { .step-insert-btn { opacity:1; } }
/* 插入成功后闪一下，指认新步骤落在哪 */
.step.is-flash { animation:step-flash 1.1s ease; }
@keyframes step-flash {
  0%, 55% { background:#fff7e0; border-color:#f0d99b; }
  100% { background:#fff; border-color:#ebedf0; }
}
/* 收起后只剩铅笔与删除：行内边距收紧，省出的宽度都给步骤文字 */
.is-lean .step { gap:8px; padding:8px; }
/* 步骤行图标：30×30 正方形，四个按钮从 176px 收到 126px */
.recording-page .step-actions :deep(.el-button.is-text) { width:30px; height:30px; padding:0; border-radius:7px; }
.recording-page .step-actions :deep(.el-button.is-text .el-icon) { font-size:14px; }

/* 拖拽排序（sortablejs）：按住编号拖动 */
.step-no { cursor:grab; }
.step-no:active { cursor:grabbing; }
.step-ghost { opacity:.45; background:#f5f9ff; border-style:dashed; border-color:#9fc0ee; }
.step-chosen { box-shadow:0 8px 22px rgba(16,24,40,.12); }
.step-drag { opacity:.95; }

/* 就地编辑 */
.step-editor { display:flex; flex-direction:column; gap:8px; }
.step-editor-actions { display:flex; justify-content:flex-end; gap:8px; }
.step-editor-poll { display:flex; align-items:center; flex-wrap:wrap; gap:8px; column-gap:10px; }
.step-editor-poll :deep(.el-checkbox) { margin-right:0; }
.step-editor-poll :deep(.el-input-number) { width:92px; }
.kind-tag { flex-shrink:0; display:inline-flex; align-items:center; gap:3px; height:22px; padding:0 9px; border-radius:6px; font-size:12px; font-weight:700; }
.kind-icon { font-size:12px; }
.kind-primary { background:#eaf3ff; color:#0071e3; }
.kind-success { background:#eaf7ef; color:#1e7a46; }
.kind-warning { background:#fff7e6; color:#ad6800; }
.kind-danger { background:#fff1f0; color:#e5484d; }
.kind-info { background:#f2f4f7; color:#6e6e73; }

/* 断言 / 等待 模板 */
.draft-card { margin-top:16px; border:1.5px dashed #c9d6ea; border-radius:14px; padding:16px; background:#fbfdff; }
.draft-head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px; }
/* 从行内「+」进来时标明锚点，和工具栏那三个按钮打开时的区别一眼可见 */
.draft-anchor { margin-right:auto; font-size:11.5px; color:#0071e3; background:#eaf3ff; border-radius:999px; padding:2px 9px; white-space:nowrap; }
.draft-title { font-size:14px; font-weight:700; color:#1d1d1f; }
.draft-nl { display:flex; gap:10px; align-items:flex-start; }
.draft-nl .el-textarea { flex:1; }
/* 固定等待没有可解析的内容：一行秒数，不占地方也不用解释 */
.sleep-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.sleep-label { font-size:13px; color:#303133; }
.sleep-unit { font-size:13px; color:#606266; }
.sleep-hint { margin-left:4px; font-size:12px; color:#909399; }
.parsed-preview { margin-top:12px; padding:12px; border-radius:10px; background:#f0faf4; border:1px solid #d6f0df; }
.parsed-line { display:flex; gap:10px; align-items:flex-start; font-size:13.5px; color:#1d1d1f; flex-wrap:wrap; }
.parsed-label { color:#1e7a46; font-weight:700; flex-shrink:0; }
.parsed-text { min-width:0; word-break:break-word; }
/* 解析结果只是「填表来源」：用户改过表单就以表单为准，标签要说清楚，否则会以为插入的还是预览那条 */
.parsed-tag { flex-shrink:0; margin-left:auto; font-size:11.5px; color:#1e7a46; background:#e2f5e9; border-radius:999px; padding:2px 9px; }
.parsed-tag.dirty { color:#a15c07; background:#fff4e2; }
.form-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px; }
.alias-option { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
.alias-option-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.alias-option-hint { flex:none; color:#a8abb2; font-size:11.5px; }
.draft-actions { margin-top:12px; display:flex; justify-content:flex-end; }

/* 插入前验证：结论用「当前页面状态」这个词，避免和回放校验混为一谈 */
.verify-box { margin-top:12px; padding:10px 12px; border-radius:10px; background:#f6f9ff; border:1px solid #dbe7fa; }
.verify-box.ok { background:#f2fbf5; border-color:#cdebd8; }
.verify-box.bad { background:#fff8f0; border-color:#f3ddc2; }
/* 轮询等待既不是通过也不是失败：此刻不成立是它的常态，单独一档中性色 */
.verify-box.neutral { background:#f6f9ff; border-color:#dbe7fa; }
.verify-box.neutral .verify-dot { background:#3b82f6; }
.verify-box.neutral .verify-title { color:#1d4f9a; }
.verify-box.neutral .verify-detail { color:#5c6675; }

/* 轮询等待参数 */
/* 高级区：默认收起，收起时靠右侧摘要说明「会不会等」 */
.advanced-box { margin-top:10px; border-radius:10px; border:1px solid #e6e9ef; background:#fcfdff; transition:border-color .18s ease, background .18s ease; }
.advanced-box.is-on { border-color:#c9dcf5; background:#f7fbff; }
.advanced-head { display:flex; align-items:center; gap:6px; padding:8px 10px; cursor:pointer; user-select:none; border-radius:10px; }
.advanced-head:hover { background:rgba(0,113,227,.035); }
/* 三角用边框画，不用 ▸ 字符：小字号下中文字体会把它渲染成一个点 */
.advanced-caret { flex:none; width:0; height:0; border-left:5px solid #9aa3b2; border-top:4px solid transparent; border-bottom:4px solid transparent; transition:transform .16s ease; }
.advanced-caret.is-open { transform:rotate(90deg); }
.advanced-title { font-size:12.5px; color:#3c4655; }
.advanced-box.is-on .advanced-title { color:#0b6bcb; font-weight:600; }
.advanced-summary { margin-left:auto; font-size:12px; color:#9aa3b2; }
.advanced-box.is-on .advanced-summary { color:#2f7fd0; }
.advanced-help { flex:none; display:inline-flex; align-items:center; justify-content:center; width:15px; height:15px; border-radius:50%; border:1px solid #cdd5e0; color:#9aa3b2; font-size:10px; line-height:1; cursor:help; }
.advanced-help:hover { border-color:#8fb8e6; color:#2f7fd0; }
.advanced-body { padding:0 10px 10px; }
.advanced-tip { max-width:250px; line-height:1.65; }
.poll-row { display:flex; align-items:center; flex-wrap:wrap; gap:6px; }
.poll-row + .poll-row { margin-top:8px; }
.poll-row :deep(.el-checkbox) { margin-right:0; }
.poll-row :deep(.el-input-number) { width:92px; }
.poll-label { font-size:12px; color:#5c6675; }
.poll-unit { font-size:12px; color:#5c6675; }
.verify-head { display:flex; align-items:center; gap:8px; min-width:0; }
.verify-dot { flex:none; width:8px; height:8px; border-radius:50%; background:#e0a03a; }
.verify-box.ok .verify-dot { background:#12a150; }
.verify-title { flex:none; font-size:12.5px; font-weight:700; color:#1f2d3d; }
.verify-box.ok .verify-title { color:#14713c; }
.verify-box.bad .verify-title { color:#a15c07; }
.verify-loc {
  flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  font-size:11px; color:#5c6675; background:#fff; border:1px solid #e7edf6; border-radius:4px; padding:1px 5px;
}
.verify-close { flex:none; margin-left:auto; border:0; background:none; color:#96a0b0; font-size:15px; line-height:1; cursor:pointer; padding:0 2px; }
.verify-close:hover { color:#4b5563; }
.verify-row { display:flex; gap:8px; margin-top:6px; font-size:12px; line-height:1.5; color:#3d4657; }
.verify-key { flex:none; width:30px; color:#8a94a6; }
.verify-val { min-width:0; word-break:break-word; }
.verify-detail { margin-top:6px; font-size:12px; color:#8a5a12; line-height:1.5; }
.verify-note { margin-top:8px; color:#8a94a6; font-size:11.5px; line-height:1.55; }

/* 生成 */
.checklist { display:flex; flex-direction:column; gap:10px; margin-bottom:18px; }
/* 未停止录制时只留一行，别让 4 条「未满足」把整张卡撑高 */
.gen-compact { margin-bottom:14px; padding:10px 12px; border-radius:10px; background:#f7f9fc; color:#6e6e73; font-size:13px; line-height:1.5; }
.gen-compact b { color:#0071e3; font-weight:700; }
.gen-compact-bad { color:#d4380d; }
.check { display:flex; align-items:flex-start; gap:10px; font-size:13px; color:#6e6e73; line-height:1.5; }
.check-text { flex:1; min-width:0; }
/* 行内补救入口：不抢主行动的注意力，但要在这一行里够得着 */
.check-action { flex:none; margin:-4px -6px -4px 0; color:#0071e3; font-size:12px; }
.check-dot { flex-shrink:0; width:9px; height:9px; margin-top:6px; border-radius:50%; background:#d0d5dd; }
.check.ok { color:#1e7a46; }
.check.ok .check-dot { background:#34c759; }
.check.bad { color:#e5484d; }
.check.bad .check-dot { background:#e5484d; }
.check.warn { color:#ad6800; }
.check.warn .check-dot { background:#ffb020; }
/* 整宽主行动：唯一比标准按钮高的一处（独占一行，不与任何按钮并排），
   926px 宽下 36px 才有分量；32px 会显得像一条薄片。 */
.recording-page :deep(.el-button.generate-btn) { width:100%; height:36px; font-size:13.5px; border-radius:9px; }
/* 生成进度：占掉按钮那块位置，高度接近，切换时整张卡不跳 */
.gen-progress { padding:10px 12px 11px; border:1px solid #e6e9ef; border-radius:9px; background:#fafbfc; }
.gen-progress-head { display:flex; align-items:baseline; gap:8px; margin-bottom:8px; }
.gen-progress-label { flex:1; min-width:0; font-size:13px; font-weight:600; color:#1f2530;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.gen-progress-pct { flex:none; font-size:12.5px; font-weight:600; color:#0071e3; font-variant-numeric:tabular-nums; }
.gen-progress :deep(.el-progress-bar__outer) { background:#e9edf2; }
.gen-progress-foot { display:flex; align-items:center; gap:8px; margin-top:8px; font-size:11.5px; color:#8a8f99; }
.gen-progress-detail { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.gen-progress-time { flex:none; font-variant-numeric:tabular-nums; }
.gen-block-reason { display:flex; align-items:flex-start; gap:7px; margin-top:2px; color:#ad6800; font-size:12.5px; line-height:1.5; }
.gen-block-dot { flex-shrink:0; width:7px; height:7px; border-radius:50%; background:#ffb020; margin-top:6px; }
.gen-error { margin-top:14px; padding:12px; border-radius:10px; background:#fff1f0; color:#e5484d; font-size:13px; }
.gen-error-title { font-weight:700; }
.gen-error-list { margin:8px 0 0; padding-left:18px; font-size:12.5px; line-height:1.6; }
.gen-result { margin-top:14px; padding:12px; border-radius:10px; background:#f0faf4; }
.gen-result-title { color:#1e7a46; font-weight:700; font-size:13.5px; }
/* 降级定位提示：绿底结果里嵌一块琥珀，别让「已写入」的绿色盖住风险 */
.gen-degraded { margin-top:10px; padding:9px 11px; border-radius:8px; background:#fff7e6; border:1px solid #f3d8a8; }
.gen-degraded-title { color:#a15c07; font-size:12.5px; font-weight:600; }
.gen-degraded ul { margin:6px 0 0; padding-left:16px; color:#8a5a12; font-size:12px; line-height:1.65; }

/* 回放校验结果：折叠时只占一行，结论先行 */
.gen-replay { margin-top:10px; padding:9px 11px; border-radius:8px; background:#fafbfc; border:1px solid #e9edf2; }
.gen-replay-head {
  display:flex; align-items:center; gap:7px; width:100%; padding:0; border:0;
  background:none; cursor:pointer; text-align:left; font:inherit;
}
.gen-replay-head:hover .gen-replay-toggle { text-decoration:underline; }
.gen-replay-dot { width:7px; height:7px; border-radius:50%; flex:none; background:#12a150; }
.gen-replay-dot.bad { background:#e0352b; }
.gen-replay-title { color:#1f2d3d; font-size:12.5px; font-weight:600; }
.gen-replay-title.bad { color:#c0271d; }
.gen-replay-toggle { margin-left:auto; color:#0071e3; font-size:12px; }
.gen-replay-error { margin-top:6px; color:#c0271d; font-size:12px; line-height:1.6; word-break:break-all; }
.gen-replay-note { margin-top:6px; color:#8a94a6; font-size:11.5px; line-height:1.55; }
.gen-replay-steps { margin:8px 0 0; padding:0; list-style:none; max-height:220px; overflow-y:auto; }
.gen-replay-steps li {
  display:flex; align-items:center; gap:7px; padding:5px 0; border-top:1px dashed #eef0f3;
  font-size:11.5px; color:#5c6675; min-width:0; flex-wrap:wrap;
}
.gen-replay-steps li.bad { color:#c0271d; }
.gen-replay-no { flex:none; width:20px; color:#9aa4b2; font-variant-numeric:tabular-nums; }
.gen-replay-type { flex:none; width:52px; color:#8892a0; }
.gen-replay-alias { flex:none; max-width:110px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.gen-replay-loc {
  flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  color:#3d4657; font-size:11px; background:#f4f6f8; padding:1px 5px; border-radius:4px;
}
.gen-replay-loc.is-empty { background:none; color:#b4bcc8; padding:1px 0; }
.gen-replay-nth { flex:none; color:#a15c07; }
.gen-replay-poll { flex:none; color:#1d4f9a; }
/* 失败原因是整句，独占一行，否则会把定位挤成省略号 */
.gen-replay-why { flex-basis:100%; padding-left:27px; color:#c0271d; font-size:11.5px; line-height:1.5; }

/* ---- 任务书 ---- */
.plan-head-meta { margin-left:auto; color:#8a94a6; font-size:11.5px; font-weight:400; }
/* 任务书入口：生成卡里只留一行状态。正文在浮层里读，长表格不该挤进 420px 的侧栏。 */
.plan-ready { display:flex; align-items:center; gap:10px; }
.plan-ready-main { flex:1; min-width:0; }
.plan-ready-title { color:#1f2530; font-size:12.5px; font-weight:600; }
.plan-ready-sub {
  margin-top:3px; color:#7a8494; font-size:11.5px; line-height:1.5;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}

/* 任务书浮层：左边通读、右边拍板。
   遮罩 z-index 取 1900——高于页面里所有自绘元素（导航 100、定位提示 60），
   低于 Element 的弹层（2000 起），意见弹窗和 toast 才能正常盖在它上面。 */
.plan-sheet-mask {
  position:fixed; inset:0; z-index:1900; display:flex; align-items:center; justify-content:center;
  padding:22px; background:rgba(16,24,40,.44);
}
.plan-sheet {
  display:flex; flex-direction:column; width:min(1120px,96vw); height:min(900px,92vh);
  overflow:hidden; background:#fff; border-radius:16px;
  box-shadow:0 24px 64px rgba(16,24,40,.26);
}
.plan-sheet-head {
  flex:none; display:flex; align-items:center; gap:10px; padding:12px 16px;
  border-bottom:1px solid #eef0f3; background:linear-gradient(180deg,#fcfdff,#f7f9fc);
}
.plan-sheet-tip { margin-left:auto; color:#9aa4b2; font-size:11.5px; }
.plan-sheet-close {
  flex:none; width:28px; height:28px; padding:0; border:0; border-radius:8px;
  background:transparent; color:#8a94a6; font-size:13px; line-height:1; cursor:pointer;
}
.plan-sheet-close:hover { background:#eef1f5; color:#1d1d1f; }
.plan-sheet-body { flex:1; min-height:0; display:grid; grid-template-columns:minmax(0,1fr) 330px; }
.plan-sheet-main { min-height:0; overflow-y:auto; padding:16px 24px 24px; }
/* 大屏上把正文的行距和表格松一格：还是同一份内容，读起来不再是窄栏里的压缩版 */
.plan-sheet-main .plan-block { padding:14px 0; }
.plan-sheet-main .plan-steps { font-size:12.5px; }
.plan-sheet-main .plan-steps td { padding:7px 6px; }
.plan-sheet-main .plan-step-no { width:26px; }
.plan-sheet-main .plan-step-action { width:46px; }
.plan-sheet-error { flex:none; overflow-y:auto; padding:18px 24px; border-bottom:1px solid #f6d5d1; background:#fff8f7; }
/* 没有旧任务书可看时，错误就是全部内容：占满并居中，别让它孤零零挂在顶上 */
.plan-sheet-error.is-solo {
  flex:1; min-height:0; display:flex; flex-direction:column; justify-content:center;
  padding:32px 40px; border-bottom:0; background:#fff;
}
.plan-sheet-error.is-solo .gen-error { max-width:720px; margin:0 auto; width:100%; }
/* 右栏固定：读两步就能点执行，不用滚到第 12 步末尾才看见按钮 */
.plan-sheet-side {
  min-height:0; display:flex; flex-direction:column;
  border-left:1px solid #eef0f3; background:#fbfcfe;
}
.plan-side-scroll { flex:1; min-height:0; overflow-y:auto; padding:14px 16px; }
.plan-side-foot { flex:none; padding:12px 16px; border-top:1px solid #eef0f3; background:#fff; }
.plan-side-foot .plan-actions { margin-top:0; }
.plan-side-foot .plan-actions :deep(.el-button + .el-button) { margin-left:0; }
.plan-side-foot .plan-actions :deep(.el-button) { flex:1; }
.plan-side-foot .field-hint, .plan-side-foot .gen-block-reason { margin-top:8px; }
/* 330px 放不下「输入框 + 确定 + 取消」一排，让它断成两行 */
.plan-sheet-side .plan-target-edit { flex-wrap:wrap; }
.plan-sheet-side .plan-target-edit :deep(.el-input) { flex:1 1 100%; }
.plan-target-edit-btns { display:flex; gap:6px; }
.plan-notes-count { margin-left:auto; color:#9aa4b2; font-size:11px; font-weight:400; }

/* 窗口本身不够宽时（嵌入模式、或被并排挤窄）就单列：正文在上，右栏缩成底部操作区。
   按钮仍然常驻，这点不能在窄屏上退化。 */
@media (max-width: 900px) {
  .plan-sheet-mask { padding:10px; }
  .plan-sheet { width:96vw; height:96vh; border-radius:12px; }
  .plan-sheet-body { grid-template-columns:1fr; grid-template-rows:minmax(0,1fr) auto; }
  .plan-sheet-side { border-left:0; border-top:1px solid #eef0f3; max-height:40vh; }
  .plan-sheet-tip { display:none; }
  .plan-sheet-head { padding:10px 12px; }
}
.plan-block { padding:10px 0; border-top:1px dashed #eef0f3; }
.plan-block:first-child { border-top:0; padding-top:0; }
.plan-block-title {
  display:flex; align-items:baseline; gap:6px;
  margin-bottom:6px; color:#1f2530; font-size:12px; font-weight:600;
}
.plan-kv { display:flex; gap:8px; font-size:11.5px; line-height:1.8; }
.plan-kv > span { flex:none; width:44px; color:#8a94a6; }
.plan-kv > b { color:#3d4657; font-weight:500; word-break:break-word; }
.plan-style-note { margin-top:5px; color:#7a8494; font-size:11.5px; line-height:1.65; }
.plan-sheet code, .plan-ready code, .plan-target code, .plan-reuse code, .plan-suggest code {
  background:#f4f6f8; color:#3d4657; padding:1px 5px; border-radius:4px;
  font-size:11px; word-break:break-all;
}
.plan-target { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.plan-path-ok { color:#12a150; font-size:11.5px; }
/* 「这是 AI 选的」标记：路径本身是重点，别做成彩色按钮抢注意力 */
.plan-path-badge {
  margin-left:6px; padding:1px 6px; border-radius:4px;
  background:#eef3fd; color:#2f6bd8; font-size:11px; font-weight:500;
}
.plan-target-edit-btn { color:#8e8e93; }
.plan-target-edit { display:flex; align-items:center; gap:6px; }
.plan-target-edit :deep(.el-input) { flex:1; }
.plan-target-error { margin-top:5px; color:#d93025; font-size:11.5px; }
.plan-target-changed {
  margin-top:8px; padding:7px 9px; border-radius:7px;
  background:#fff9ea; border:1px solid #f1ddb0; color:#7a5a12;
  font-size:11.5px; line-height:1.6;
}
.plan-suggest {
  margin-top:8px; padding:9px 10px; border:1px solid #f1ddb0; border-radius:8px;
  background:#fff9ea; font-size:11.5px; color:#7a5a12;
}
.plan-suggest-title { font-weight:600; margin-bottom:3px; }
.plan-suggest-reason { color:#94712c; line-height:1.65; }
.plan-suggest .el-button { margin-top:7px; }
.plan-reuse { margin:0; padding:0; list-style:none; }
.plan-reuse li { padding:5px 0; border-top:1px dashed #eef0f3; font-size:11.5px; }
.plan-reuse li:first-child { border-top:0; padding-top:0; }
.plan-reuse-file { margin-left:6px; color:#8a94a6; font-size:11px; }
.plan-reuse-use {
  display:block; margin-top:3px; color:#5c6675; font-size:11px;
  line-height:1.6; white-space:pre-wrap; word-break:break-all;
}
.plan-steps { width:100%; border-collapse:collapse; font-size:11.5px; }
.plan-steps td { padding:5px 4px; border-top:1px dashed #eef0f3; vertical-align:top; }
.plan-steps tr:first-child td { border-top:0; padding-top:0; }
.plan-step-no { width:18px; color:#9aa4b2; font-variant-numeric:tabular-nums; }
.plan-step-action { width:34px; color:#5c6675; white-space:nowrap; }
.plan-step-action.reuse { color:#1d4f9a; font-weight:600; }
.plan-step-note { color:#3d4657; line-height:1.65; word-break:break-word; }
.plan-risks { margin:0; padding-left:16px; color:#7a5a12; font-size:11.5px; line-height:1.7; }
.plan-notes { margin-top:10px; padding-top:10px; border-top:1px solid #eef0f3; }
.plan-notes-hint { color:#9aa4b2; font-size:11px; font-weight:400; }
.plan-note {
  position:relative; margin:6px 0; padding:7px 28px 7px 9px;
  background:#f7f9fc; border-radius:7px;
}
.plan-quote {
  margin-bottom:4px; padding-left:6px; border-left:2px solid #cfd8e3;
  color:#7a8494; font-size:11.5px; line-height:1.55; word-break:break-all;
}
.plan-note-body { color:#2f3a49; font-size:12px; line-height:1.6; white-space:pre-wrap; }
.plan-note-del {
  position:absolute; top:6px; right:6px; padding:0; border:0; background:none;
  color:#a4adba; font-size:11px; cursor:pointer;
}
.plan-note-del:hover { color:#c0271d; }
.plan-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:12px; }
.plan-stale {
  margin-bottom:10px; padding:8px 10px; border:1px solid #f3c9c3; border-radius:8px;
  background:#fff4f2; color:#c0271d; font-size:12px; line-height:1.6;
}
.plan-sel-tip {
  position:fixed; z-index:3000; height:30px; padding:0 12px; border:0; border-radius:15px;
  background:#1f2530; color:#fff; font-size:12px; line-height:30px; cursor:pointer;
  box-shadow:0 6px 18px rgba(20, 26, 36, .22);
}
.plan-sel-tip:hover { background:#0071e3; }



@media (max-height: 620px) {
  .recording-page:not(.is-panel):not(.is-embed) { height:auto; }
}

@media (max-width: 1100px) {
  /* 单列后左右并排的一屏约束就不成立了：解除固定高度，否则第一行（配置卡）
     会被 minmax(0,1fr) 压成 0 高，直接看不见。 */
  .recording-page:not(.is-panel):not(.is-embed) { height:auto; }
  .recording-body { grid-template-columns:1fr; grid-template-rows:auto; }
  .recording-body .config-card { overflow:visible; }
  .form-grid { grid-template-columns:1fr; }
}
/* 系统开了「减弱动态效果」就全部瞬时完成 */
@media (prefers-reduced-motion: reduce) {
  .is-embed .page-col,
  .step,
  .step-actions-extra,
  .toolbar { transition:none !important; }
  .toolbar :deep(.el-button) { animation:none !important; }
}
</style>
