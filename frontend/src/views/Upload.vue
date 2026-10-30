<template>
  <div class="upload-page">

    <!-- 页头：四个 KPI 原来是没有容器的一排 28px 裸数字，压在页面顶端比标题还响。
         收成右侧一行小字，数字留着重（.stat-num），标签退到 11px 灰。 -->
    <header class="upload-head">
      <div class="upload-head-main">
        <div class="eyebrow">{{ headCopy.eyebrow }}</div>
        <h1 class="upload-title">{{ headCopy.title }}</h1>
        <p class="upload-sub">{{ headCopy.desc }}</p>
      </div>
      <div class="upload-stats">
        <div class="stat">
          <span class="stat-num">{{ statusCount.pending }}</span>
          <span class="stat-label">待审批</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ statusCount.approved }}</span>
          <span class="stat-label">已通过</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ statusCount.total }}</span>
          <span class="stat-label">AI 生成</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ approvalRate }}</span>
          <span class="stat-label">通过率</span>
        </div>
      </div>
    </header>

    <!-- 三个模式是同一层级的切换，用下划线 tab；「Agent 探索」是跳出去，靠在右边分开 -->
    <nav class="mode-tabs">
      <button type="button" :class="['mode-tab', { 'is-on': uploadMode === 'image' }]" @click="switchMode('image')">原型图</button>
      <button type="button" :class="['mode-tab', { 'is-on': uploadMode === 'doc' }]" @click="switchMode('doc')">文档</button>
      <button type="button" :class="['mode-tab', { 'is-on': uploadMode === 'knowledge' }]" @click="switchMode('knowledge')">知识库</button>
      <span class="mode-spacer"></span>
      <button type="button" class="mode-jump" @click="goToAutomation">Agent 探索<span class="jump-arrow">↗</span></button>
    </nav>

    <!-- 自动化工具：从「渐变图标大卡」降成浅底一行。
         它仍然可点、仍然独占一行，但不再和上传卡片抢第一眼。 -->
    <button type="button" class="tool-entry" @click="$router.push('/recording')">
      <span class="tool-entry-icon"><el-icon :size="15"><Operation /></el-icon></span>
      <span class="tool-entry-body">
        <span class="tool-entry-title">自动化工具</span>
        <span class="tool-entry-desc">在浏览器里录制操作，插入断言和等待，生成 Playwright 或 Robot Framework 脚本并写入项目</span>
      </span>
      <span class="tool-entry-arrow">→</span>
    </button>

    <el-card v-if="uploadMode !== 'knowledge'" class="upload-card">
      <div class="card-body-layout">
        <div class="config-panel">
          <div class="section-title">配置</div>
          <!-- 字段规范与录制页配置卡一致：标签 11.5px 灰在上、值在下、不画盒子，
               只靠字段之间的一条细线分隔。控件本身透明，聚焦时才出一条蓝线。 -->
          <div class="form-block">
            <div class="form-field">
              <div class="form-label">
                <span>项目</span>
                <button type="button" class="form-label-act" @click="showCreateProject = true">＋ 新建</button>
              </div>
              <el-select v-model="form.projectId" placeholder="选择项目" class="form-ctl" @change="onProjectChange">
                <el-option
                  v-for="project in projects"
                  :key="project.id"
                  :label="project.name"
                  :value="project.id"
                />
              </el-select>
            </div>

            <div class="form-field">
              <div class="form-label">
                <span>迭代</span>
                <button type="button" class="form-label-act" @click="showCreateSprint = true">＋ 新建</button>
              </div>
              <el-select v-model="form.sprintId" placeholder="选择迭代" class="form-ctl" @change="loadStatusCount">
                <el-option
                  v-for="s in sprints"
                  :key="s.id"
                  :label="s.name"
                  :value="s.id"
                />
              </el-select>
            </div>

            <div class="form-toggle">
              <el-switch v-model="form.linkedAnalysis" />
              <span class="form-toggle-text">关联分析</span>
              <span class="form-toggle-hint">多图时合并分析，AI 理解页面关联流程</span>
            </div>

            <div class="form-field">
              <div class="form-label"><span>原型分析模型</span></div>
              <el-select v-model="form.analyzeConfigId" placeholder="选择分析模型" class="form-ctl">
                <el-option
                  v-for="config in filteredAIConfigs"
                  :key="config.id"
                  :label="getProviderLabel(config)"
                  :value="config.id"
                />
              </el-select>
              <!-- 选中模型后才出现的参数区：属于这个字段的附属信息，
                   所以左对齐、缩进为零，用淡底而不是再套一个带边框的盒子 -->
              <div v-if="currentAnalyzeModel" class="up-model">
                <div class="up-model-head">
                  <span class="up-model-name">{{ currentAnalyzeModel.model || '-' }}</span>
                  <el-input
                    v-if="editingAnalyzeModel"
                    v-model="currentAnalyzeModel.model"
                    size="small"
                    class="up-model-input"
                    @blur="editingAnalyzeModel = false; saveModelConfig(currentAnalyzeModel)"
                  />
                  <button v-else type="button" class="up-model-act" @click="editingAnalyzeModel = true">编辑</button>
                </div>
                <div class="up-model-meta">
                  <span class="up-model-k">温度</span>
                  <el-input-number
                    v-model="currentAnalyzeModel.temperature"
                    :min="0" :max="2" :step="0.1"
                    size="small" controls-position="right"
                    class="up-num" style="width: 78px"
                    @change="saveModelConfig(currentAnalyzeModel)"
                  />
                  <span class="up-model-k">Token</span>
                  <el-input-number
                    v-model="currentAnalyzeModel.max_tokens"
                    :min="100" :max="32000" :step="200"
                    size="small" controls-position="right"
                    class="up-num" style="width: 92px"
                    @change="saveModelConfig(currentAnalyzeModel)"
                  />
                </div>
                <div class="up-model-api">{{ currentAnalyzeModel.api_base || '-' }}</div>
              </div>
            </div>

            <div class="form-field">
              <div class="form-label"><span>用例生成模型</span></div>
              <el-select v-model="form.genConfigId" placeholder="选择生成模型" class="form-ctl">
                <el-option
                  v-for="config in aiConfigs"
                  :key="config.id"
                  :label="getProviderLabel(config)"
                  :value="config.id"
                />
              </el-select>
              <div v-if="currentGenModel" class="up-model">
                <div class="up-model-head">
                  <span class="up-model-name">{{ currentGenModel.model || '-' }}</span>
                  <el-input
                    v-if="editingGenModel"
                    v-model="currentGenModel.model"
                    size="small"
                    class="up-model-input"
                    @blur="editingGenModel = false; saveModelConfig(currentGenModel)"
                  />
                  <button v-else type="button" class="up-model-act" @click="editingGenModel = true">编辑</button>
                </div>
                <div class="up-model-meta">
                  <span class="up-model-k">温度</span>
                  <el-input-number
                    v-model="currentGenModel.temperature"
                    :min="0" :max="2" :step="0.1"
                    size="small" controls-position="right"
                    class="up-num" style="width: 78px"
                    @change="saveModelConfig(currentGenModel)"
                  />
                  <span class="up-model-k">Token</span>
                  <el-input-number
                    v-model="currentGenModel.max_tokens"
                    :min="100" :max="32000" :step="200"
                    size="small" controls-position="right"
                    class="up-num" style="width: 92px"
                    @change="saveModelConfig(currentGenModel)"
                  />
                </div>
                <div class="up-model-api">{{ currentGenModel.api_base || '-' }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="upload-panel">
          <el-upload
            class="upload-dragger"
            drag
            action="#"
            :auto-upload="false"
            :show-file-list="false"
            :on-change="handleFileChange"
            :on-remove="handleFileRemove"
            :file-list="fileList"
            :list-type="uploadMode === 'image' ? 'picture' : 'text'"
            :accept="uploadMode === 'image' ? '.jpg,.jpeg,.png,.webp' : '.docx,.doc,.md'"
            multiple
          >
            <el-icon class="el-icon--upload">
              <PictureFilled v-if="uploadMode === 'image'" />
              <Document v-else />
            </el-icon>
            <div class="el-upload__text">
              {{ uploadMode === 'image' ? '拖拽图片、Ctrl+V 粘贴截图或' : '拖拽文档到此处或' }} <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                {{ uploadMode === 'image' ? '支持 JPG/PNG/WebP 格式，支持 Ctrl+V 粘贴截图，单文件不超过 10MB' : '支持 Word(.docx/.doc) / Markdown(.md) 格式' }}
              </div>
            </template>
          </el-upload>

          <div class="file-previews" v-if="fileList.length > 0">
            <div
              v-for="(file, index) in fileList"
              :key="file.uid"
              :class="['file-preview-item', { 'is-doc': !isImageFile(file) }]"
            >
              <img v-if="isImageFile(file)" :src="file.url" />
              <div v-else class="file-doc-preview">
                <el-icon :size="16"><Document /></el-icon>
                <span>{{ file.name }}</span>
              </div>
              <span class="file-preview-badge" @click="removeFile(index)">&times;</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 底栏：原来四个按钮并排、四种颜色（灰/蓝/白/米），谁是主行动看不出来。
           现在只有一个蓝，另外三个降成文字；「查看列表」是被主行动带出来的出口，不该长成按钮。 -->
      <div class="action-bar">
        <div class="action-secondary">
          <button type="button" class="text-act" :disabled="fileList.length === 0" @click="openDescDialog">填写功能介绍</button>
          <span class="act-sep">·</span>
          <button type="button" class="text-act" :disabled="!form.projectId" @click="goToCases">查看用例列表</button>
          <span class="act-sep">·</span>
          <button type="button" class="text-act" :disabled="!form.projectId" @click="goToModules">查看模块列表</button>
        </div>
        <el-button type="primary" @click="generateCases" :loading="generating" :disabled="!canGenerate">
          生成测试用例
        </el-button>
      </div>
    </el-card>

    <el-card v-if="uploadMode === 'knowledge'" class="knowledge-card">
      <div class="kb-toolbar">
        <el-select v-model="knowledgeProjectId" placeholder="选择项目" class="sel-project" @change="loadKnowledgeFiles">
          <el-option
            v-for="p in projects"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          />
        </el-select>
        <span v-if="knowledgeFiles.length" class="kb-count">{{ knowledgeFiles.length }} 个文件</span>
        <span class="kb-spacer"></span>
        <el-upload
          :show-file-list="false"
          :before-upload="handleKnowledgeUpload"
          :disabled="!knowledgeProjectId"
          accept=".md,.txt,.docx,.doc"
        >
          <el-button type="primary" :disabled="!knowledgeProjectId" :loading="knowledgeUploading">
            上传知识文件
          </el-button>
        </el-upload>
      </div>

      <!-- 空时只留空态：表格自带的「暂无数据」会和下面的 el-empty 叠成两块 -->
      <div v-if="knowledgeFiles.length" class="flat-table">
        <el-table :data="knowledgeFiles" v-loading="kbFilesLoading" style="width:100%">
          <el-table-column prop="original_name" label="文件名" min-width="240">
            <template #default="{ row }">
              <span class="list-cell-strong">{{ row.original_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="110">
            <template #default="{ row }">
              <span class="list-cell-muted">{{ formatSize(row.file_size) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="150">
            <template #default="{ row }">
              <span class="list-cell-muted">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <button type="button" class="list-act" @click="viewKnowledgeFile(row)">查看</button>
              <button type="button" class="list-act is-danger" @click="deleteKnowledgeFile(row)">删除</button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-empty v-if="!kbFilesLoading && knowledgeFiles.length === 0" description="暂无知识文件，先选项目再上传" :image-size="54" />
    </el-card>

    <el-dialog v-if="uploadMode === 'knowledge'" v-model="showKnowledgeViewer" title="文件内容" width="700px" top="2vh">
      <el-input v-model="knowledgeViewContent" type="textarea" :rows="20" readonly />
    </el-dialog>

    <el-dialog
      v-model="showProgressDialog"
      :title="generateStep === 'uploading' ? '上传图片' : 'AI 图片分析'"
      width="420px"
      top="18vh"
      :close-on-click-modal="false"
      :show-close="false"
      center
      class="progress-dialog"
    >
      <div class="progress-content">
        <div class="ios-spinner">
          <div v-for="i in 12" :key="i" class="ios-bar" :style="{ transform: `rotate(${i * 30}deg)`, animationDelay: `${i * 0.08}s` }" />
        </div>
        <div class="progress-text">
          {{ generateStep === 'uploading' ? '正在上传图片...' : 'AI 正在分析原型图' }}
        </div>
        <div class="progress-sub">
          {{ generateStep === 'uploading' ? '请稍候' : '通常需要 30-60 秒，图片越复杂耗时越长' }}
        </div>
      </div>
    </el-dialog>

    <!-- 两个新建弹窗与配置区同一套字段写法：标签在上、下划线输入、无盒子 -->
    <el-dialog v-model="showCreateProject" title="新建项目" width="460px" class="dlg is-plain">
      <div class="form-block">
        <div class="form-field">
          <div class="form-label"><span>项目名称</span></div>
          <el-input v-model="newProject.name" class="form-input" placeholder="例如：测试资产平台" />
        </div>
        <div class="form-field">
          <div class="form-label"><span>项目描述</span></div>
          <el-input
            v-model="newProject.description"
            type="textarea"
            :rows="3"
            class="form-textarea"
            placeholder="一句话说明这个项目测什么"
          />
        </div>
      </div>
      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">项目下可以再建迭代</span>
          <div class="dlg-foot-actions">
            <el-button @click="showCreateProject = false">取消</el-button>
            <el-button type="primary" @click="createProject">创建项目</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreateSprint" title="新建迭代" width="420px" class="dlg is-plain">
      <div class="form-block">
        <div class="form-field">
          <div class="form-label"><span>迭代名称</span></div>
          <el-input v-model="newSprint.name" class="form-input" placeholder="如 v1.0、sprint-3" />
        </div>
      </div>
      <template #footer>
        <div class="dlg-foot">
          <div class="dlg-foot-actions">
            <el-button @click="showCreateSprint = false">取消</el-button>
            <el-button type="primary" @click="createSprint">创建迭代</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showDescDialog" title="填写功能介绍" width="1160px" top="5vh" class="desc-dialog" :close-on-click-modal="false">
      <div class="desc-layout" v-if="fileList.length > 0">
        <div class="desc-image-panel">
          <div v-if="isImageFile(currentFile)" class="desc-image-viewer"
               @wheel="handleDescZoom"
               @mousedown="startDescDrag"
               @mousemove="onDescDrag"
               @mouseup="endDescDrag"
               @mouseleave="endDescDrag">
              <img
                :src="getFilePreviewUrl(currentFileIndex)"
                :style="{
                  transform: `scale(${descImageScale}) translate(${descDragOffset.x}px, ${descDragOffset.y}px)`,
                  cursor: descIsDragging ? 'grabbing' : descImageScale > 1 ? 'grab' : 'default'
                }"
                draggable="false"
              />
            </div>
          <div v-else class="desc-doc-viewer">
            <pre>{{ currentDocContent }}</pre>
          </div>
          <div class="desc-image-thumbs" v-if="fileList.length > 1">
            <template v-if="uploadMode === 'image'">
              <div
                v-for="(file, index) in fileList"
                :key="file.uid"
                class="desc-thumb-wrap"
              >
                <img
                  :src="getFilePreviewUrl(index)"
                  :class="{ active: index === currentFileIndex }"
                  @click="switchFile(index)"
                />
                <span v-if="isCropFile(file)" class="crop-badge">裁剪</span>
                <span class="thumb-delete" @click.stop="removeThumb(index)">&times;</span>
              </div>
            </template>
            <template v-else>
              <div
                v-for="(file, index) in fileList"
                :key="file.uid"
                :class="['desc-doc-thumb', { active: index === currentFileIndex }]"
                @click="switchFile(index)"
              >
                {{ file.name }}
              </div>
            </template>
          </div>
          <div class="desc-toolbar">
            <span class="desc-image-info">{{ currentFileIndex + 1 }} / {{ fileList.length }}</span>
            <div class="desc-zoom-controls" v-if="isImageFile(currentFile)">
              <button type="button" class="desc-zoom-btn" @click="descZoomOut">−</button>
              <span class="desc-zoom-value">{{ Math.round(descImageScale * 100) }}%</span>
              <button type="button" class="desc-zoom-btn" @click="descZoomIn">＋</button>
              <button type="button" class="desc-zoom-reset" @click="descImageScale = 1; descDragOffset = { x: 0, y: 0 }">重置</button>
            </div>
          </div>
        </div>
        <div class="desc-form-panel">
          <div class="desc-form-label">功能介绍（可选，帮助AI理解测试目标）</div>
          <el-input
            v-model="imageDescriptions[currentFileIndex]"
            type="textarea"
            :rows="20"
            placeholder="请描述功能介绍、业务逻辑、您关注的测试重点等..."
          />
        </div>
      </div>
      <el-empty v-else description="请先上传文件" />
      <template #footer>
        <div class="desc-footer">
          <div class="desc-footer-left">
            <el-button @click="startCrop" :disabled="!isImageFile(currentFile)">框选原图</el-button>
            <el-button @click="startAnnotation" :disabled="!isImageFile(currentFile)">标注交互</el-button>
          </div>
          <el-button type="primary" @click="showDescDialog = false">完成</el-button>
        </div>
      </template>
      <div v-if="isCropping" class="crop-fullscreen" @click.self="cancelCrop">
        <div class="crop-image-container"
             @mousedown="startCropDrag"
             @mousemove="onCropDrag"
             @mouseup="endCropDrag"
             @mouseleave="endCropDrag">
          <img
            ref="cropImageRef"
            :src="getFilePreviewUrl(currentFileIndex)"
            class="crop-full-image"
            draggable="false"
          />
          <div class="crop-event-layer" />
          <div v-if="cropRect" class="crop-overlay" :style="cropOverlayStyle" />
        </div>
        <div class="crop-fullscreen-bar">
          <el-button @click="cancelCrop">取消</el-button>
          <el-button @click="clearCropRect" :disabled="!cropRect">清空</el-button>
          <el-button type="primary" @click="confirmCrop" :disabled="!cropRect" :loading="cropEnding">确定</el-button>
        </div>
      </div>
      <div v-if="isAnnotating" class="crop-fullscreen annotation-mode">
        <div class="annotation-layout">
          <div class="annotation-paths">
            <!-- 这个模式里「路径」是最容易理解错的概念，先用一句话说明它是什么 -->
            <div class="ann-head">
              <span class="ann-title">交互路径</span>
              <span class="ann-hint">一条路径 = 一次完整操作流程，按步骤顺序识别元素</span>
            </div>
            <div v-for="(path, pi) in interactionPaths" :key="pi" class="path-section">
              <div class="path-header">
                <span class="path-label">{{ path.name || '路径'+(pi+1) }}</span>
                <el-button size="small" type="danger" text @click="removePath(pi)" :disabled="interactionPaths.length<=1">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
              <el-input v-model="path.name" placeholder="路径名称" size="small" style="margin-bottom:6px" />
              <el-input v-model="path.hint" placeholder="定位提示（可选）" size="small" style="margin-bottom:8px" />
              <div class="path-desc-label">操作描述（用大白话描述你想测试什么）</div>
              <el-input v-model="path.description" type="textarea" :rows="3" placeholder="例如：我要测试登录功能" size="small" style="margin-bottom:8px" />
              <div class="path-steps-section">
                <div class="path-desc-label">步骤列表</div>
                <div v-for="(step, si) in path.steps" :key="si" class="step-row">
                  <span class="step-num">{{ si + 1 }}.</span>
                  <el-input v-model="path.steps[si]" :placeholder="'第'+(si+1)+'步'" size="small" style="flex:1" />
                  <el-button size="small" type="danger" text @click="removeStep(pi, si)" :disabled="path.steps.length<=1">
                    <el-icon><Close /></el-icon>
                  </el-button>
                </div>
                <el-button size="small" @click="addStep(pi)" style="margin-top:4px">+ 添加步骤</el-button>
              </div>
              <div class="path-actions">
                <el-button size="small" type="primary" @click="runAIAnnotation(pi)" :loading="annotating">AI 识别</el-button>
              </div>
            </div>
            <el-button size="small" text class="ann-add-path" @click="addPath">＋ 添加路径</el-button>
          </div>
          <div class="annotation-image">
            <div class="desc-image-viewer" style="flex:1;border-radius:10px;background:#fff"
                 @wheel="handleDescZoom"
                 @mousedown="startDescDrag"
                 @mousemove="onDescDrag"
                 @mouseup="endDescDrag"
                 @mouseleave="endDescDrag">
              <img
                :src="getFilePreviewUrl(currentFileIndex)"
                :style="{
                  transform: `scale(${descImageScale}) translate(${descDragOffset.x}px, ${descDragOffset.y}px)`,
                  cursor: descIsDragging ? 'grabbing' : descImageScale > 1 ? 'grab' : 'default',
                  maxWidth: '100%',
                  maxHeight: '100%',
                  objectFit: 'contain',
                  transition: 'transform 0.2s ease',
                  userSelect: 'none'
                }"
                draggable="false"
              />
            </div>
            <div class="desc-zoom-controls">
              <el-button circle size="small" @click="descZoomOut">-</el-button>
              <span>{{ Math.round(descImageScale * 100) }}%</span>
              <el-button circle size="small" @click="descZoomIn">+</el-button>
              <el-button size="small" @click="descImageScale = 1; descDragOffset = { x: 0, y: 0 }">重置</el-button>
            </div>
          </div>
        </div>
        <div class="crop-fullscreen-bar">
          <el-button @click="clearAllAnnotations" :disabled="isAnnotateEmpty">清空</el-button>
          <el-button type="primary" @click="isAnnotating = false">完成标注</el-button>
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="showModuleReview" class="dlg" width="min(1120px, 94vw)" top="4vh" :close-on-click-modal="false">
      <template #header>
        <div class="el-dialog__title">模块审核</div>
        <div class="dlg-sub">AI 拆分出 {{ modules.length }} 个模块 · 逐个确认后一起生成用例</div>
      </template>

      <div v-if="modules.length > 0" class="dlg-split">
        <div class="dlg-viewer">
          <div
            class="dlg-stage"
            @wheel="handleModuleZoom"
            @mousedown="startModuleDrag"
            @mousemove="onModuleDrag"
            @mouseup="endModuleDrag"
            @mouseleave="endModuleDrag"
          >
            <img
              v-if="getFilePreviewUrl(0)"
              :src="getFilePreviewUrl(0)"
              :style="{
                transform: `scale(${moduleImageScale}) translate(${moduleDragOffset.x}px, ${moduleDragOffset.y}px)`,
                cursor: moduleIsDragging ? 'grabbing' : moduleImageScale > 1 ? 'grab' : 'default'
              }"
              draggable="false"
            />
            <div v-else class="dlg-stage-empty">暂无预览图</div>
          </div>
          <div v-if="uploadMode === 'image' && getFilePreviewUrl(0)" class="dlg-zoom">
            <button type="button" class="zoom-btn" title="缩小" @click="moduleZoomOut">−</button>
            <span class="dlg-zoom-val">{{ Math.round(moduleImageScale * 100) }}%</span>
            <button type="button" class="zoom-btn" title="放大" @click="moduleZoomIn">＋</button>
            <button type="button" class="zoom-reset" @click="moduleImageScale = 1; moduleDragOffset = { x: 0, y: 0 }">重置</button>
          </div>
        </div>

        <div class="dlg-panel">
          <div class="dlg-bar">
            <span class="dlg-bar-text">第 <b>{{ currentModuleIndex + 1 }}</b> / {{ modules.length }} 个模块</span>
            <div class="dlg-bar-nav">
              <button type="button" class="dlg-nav" :disabled="currentModuleIndex <= 0" @click="prevModule">上一个</button>
              <button type="button" class="dlg-nav" :disabled="currentModuleIndex >= moduleForms.length - 1" @click="nextModule">下一个</button>
            </div>
          </div>
          <div class="dlg-scroll">
            <ModuleFields
              v-if="moduleForms[currentModuleIndex]"
              v-model="moduleForms[currentModuleIndex]"
              mode="generate"
              :vision-supported="genModelSupportsVision"
            />
          </div>
        </div>
      </div>
      <div v-else class="dlg-empty">AI 分析中或分析失败，请重试</div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">生成期间请勿关闭页面，完成后到用例列表审批</span>
          <div class="dlg-foot-actions">
            <el-button @click="showModuleReview = false">取消</el-button>
            <el-button type="primary" @click="confirmModules" :loading="confirming" :disabled="modules.length === 0">
              确认并生成用例
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showModuleApproval"
      class="dlg"
      width="min(720px, 92vw)"
      top="6vh"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="el-dialog__title">审批模块</div>
        <div class="dlg-sub">通过后这个模块会进入模块库，可用来生成用例</div>
      </template>

      <div
        v-if="pendingModules.length > 0 && pendingModules[currentModuleApprovalIndex]"
        class="dlg-panel is-solo"
      >
        <div class="dlg-bar">
          <span class="dlg-bar-text">第 <b>{{ currentModuleApprovalIndex + 1 }}</b> / {{ pendingModules.length }} 个待审</span>
          <div class="dlg-bar-nav">
            <button type="button" class="dlg-nav" :disabled="currentModuleApprovalIndex <= 0" @click="moveToPrevPendingModule">上一个</button>
            <button type="button" class="dlg-nav" :disabled="currentModuleApprovalIndex >= pendingModules.length - 1" @click="moveToNextPendingModuleManual">下一个</button>
          </div>
        </div>
        <div class="dlg-scroll">
          <ModuleFields v-model="pendingModules[currentModuleApprovalIndex]" />
        </div>
      </div>
      <div v-else class="dlg-empty">没有待审模块</div>

      <template #footer>
        <div class="dlg-foot">
          <span class="dlg-foot-note">「不通过」只把模块标记为废弃，不改动内容</span>
          <div class="dlg-foot-actions">
            <el-button @click="showModuleApproval = false">取消</el-button>
            <el-button type="danger" plain @click="handleModuleFail" :loading="moduleApprovalFailing">不通过</el-button>
            <el-button type="primary" @click="handleModuleApprove" :loading="moduleApprovalPassing">通过</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled, PictureFilled, Document, Loading, Delete, Close, Operation } from '@element-plus/icons-vue'
import ModuleFields from '../components/ModuleFields.vue'
import api from '../api'

const router = useRouter()

const projects = ref([])
const aiConfigs = ref([])
const sprintNames = ref([])
const sprints = ref([])
const showCreateSprint = ref(false)
const newSprint = ref({ name: '' })
const fileList = ref([])
const generating = ref(false)
const showCreateProject = ref(false)
const showDescDialog = ref(false)
const currentFileIndex = ref(0)
const imageDescriptions = ref([])
const interactionPaths = ref([{ name: '', hint: '', description: '', steps: [''] }])
const uploadMode = ref('image')
const currentDocContent = ref('')

const knowledgeProjectId = ref('')
const knowledgeFiles = ref([])
const kbFilesLoading = ref(false)
const knowledgeUploading = ref(false)
const showKnowledgeViewer = ref(false)
const knowledgeViewContent = ref('')
const descImageScale = ref(1)
const descIsDragging = ref(false)
const descDragStart = ref({ x: 0, y: 0 })
const descDragOffset = ref({ x: 0, y: 0 })
const isCropping = ref(false)
const isAnnotating = ref(false)
const cropStart = ref(null)
const cropRect = ref(null)
const cropLocked = ref(false)
const cropImageRef = ref(null)
const cropEnding = ref(false)
const showModuleReview = ref(false)
const confirming = ref(false)
const moduleImageScale = ref(1)
const moduleIsDragging = ref(false)
const moduleDragStart = ref({ x: 0, y: 0 })
const moduleDragOffset = ref({ x: 0, y: 0 })
const modules = ref([])
const currentModuleIndex = ref(0)
const moduleForms = ref([])
const uploadedImageIds = ref([])
const analyzing = ref(false)
const generateStep = ref('')
const showProgressDialog = computed(() => !!generateStep.value)
const pendingModuleCount = ref(0)
const pendingModules = ref([])
const showModuleApproval = ref(false)
const currentModuleApprovalIndex = ref(0)
const moduleApprovalPassing = ref(false)
const moduleApprovalFailing = ref(false)
const modulePassing = ref(false)
const moduleFailing = ref(false)
const editingAnalyzeModel = ref(false)
const editingGenModel = ref(false)

const form = ref({
  projectId: '',
  sprintId: '',
  analyzeConfigId: '',
  genConfigId: '',
  linkedAnalysis: true,
  caseTypes: [],
  caseCount: 10,
  smartMode: false
})

const newProject = ref({
  name: '',
  description: ''
})

const canGenerate = computed(() => {
  return form.value.projectId && fileList.value.length > 0 && form.value.analyzeConfigId && form.value.genConfigId
})

const genModelSupportsVision = computed(() => {
  const config = aiConfigs.value.find(c => c.id === form.value.genConfigId)
  return config?.supports_vision !== false
})

const cropOverlayStyle = computed(() => {
  if (!cropRect.value) return { display: 'none' }
  return {
    left: cropRect.value.x + 'px',
    top: cropRect.value.y + 'px',
    width: cropRect.value.width + 'px',
    height: cropRect.value.height + 'px'
  }
})

const currentAnalyzeModel = computed(() => {
  return aiConfigs.value.find(c => c.id === form.value.analyzeConfigId)
})

const currentGenModel = computed(() => {
  return aiConfigs.value.find(c => c.id === form.value.genConfigId)
})

const statusCount = ref({ pending: 0, approved: 0, total: 0 })

const approvalRate = computed(() => {
  const total = statusCount.value.total
  if (total === 0) return '0%'
  return Math.round(statusCount.value.approved / total * 100) + '%'
})

// 页头文案跟着模式走：三种模式原来共用一句「上传Figma设计图」当卡片标题，
// 切到文档模式也不变，得靠看上传区才知道自己在哪一档。
const HEAD_COPY = {
  image: {
    eyebrow: '测试资产 · PROTOTYPE',
    title: '上传原型图',
    desc: '拖进 Figma 导出的页面图，AI 先拆出模块，审批通过后再生成测试用例。'
  },
  doc: {
    eyebrow: '测试资产 · DOCUMENT',
    title: '上传接口文档',
    desc: '支持 Word 与 Markdown，按章节拆模块，和原型图走同一套审批流程。'
  },
  knowledge: {
    eyebrow: '测试资产 · KNOWLEDGE',
    title: '知识库',
    desc: '沉淀的规范与业务资料，生成用例时会作为检索依据带上。'
  }
}
const headCopy = computed(() => HEAD_COPY[uploadMode.value] || HEAD_COPY.image)

const filteredAIConfigs = computed(() => {
  return aiConfigs.value.filter(c => c.supports_vision !== false)
})

const currentFile = computed(() => {
  return fileList.value[currentFileIndex.value]
})

function isImageFile(file) {
  const name = file?.name || ''
  return /\.(jpg|jpeg|png|webp)$/i.test(name)
}

onMounted(() => {
  loadProjects()
  loadAIConfigs()
  document.addEventListener('paste', handlePaste)
})

onUnmounted(() => {
  document.removeEventListener('paste', handlePaste)
})

function onProjectChange() {
  form.value.sprintId = ''
  loadStatusCount()
}

async function loadStatusCount() {
  loadSprints()
  loadPendingModuleCount()
  if (!form.value.projectId) return
  try {
    const params = { project_id: form.value.projectId }
    if (form.value.sprintId) params.sprint_id = form.value.sprintId
    const [pendingRes, approvedRes] = await Promise.all([
      api.getPendingTestcases(params),
      api.getTestcases({ ...params, status: 'approved' })
    ])
    const pending = pendingRes.data.counts?.pending || 0
    const failed = pendingRes.data.counts?.failed || 0
    const approved = (approvedRes.data.testcases || []).length
    statusCount.value = {
      pending,
      approved,
      total: pending + approved + failed
    }
  } catch {
  }
}

async function loadPendingModuleCount() {
  if (!form.value.projectId) return
  try {
    const params = { project_id: form.value.projectId }
    if (form.value.sprintId) params.sprint_id = form.value.sprintId
    const res = await api.getPendingModules(params)
    pendingModuleCount.value = (res.data.modules || []).length
  } catch {}
}

async function openPendingModuleDialog() {
  if (!form.value.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  try {
    const params = { project_id: form.value.projectId }
    if (form.value.sprintId) params.sprint_id = form.value.sprintId
    const res = await api.getPendingModules(params)
    pendingModules.value = res.data.modules || []
    if (pendingModules.value.length === 0) {
      ElMessage.info('没有待审模块')
      return
    }
    currentModuleApprovalIndex.value = 0
    showModuleApproval.value = true
  } catch {
    ElMessage.error('加载待审模块失败')
  }
}

async function handleModuleApprove() {
  moduleApprovalPassing.value = true
  try {
    const mod = pendingModules.value[currentModuleApprovalIndex.value]
    await api.approvePendingModule(mod.id, {
      module: mod.module,
      ui_elements: mod.ui_elements,
      function_description: mod.function_description,
      interaction_flow: mod.interaction_flow,
      test_focus: mod.test_focus,
      case_types: mod.case_types,
      case_count: mod.case_count,
      smart_mode: mod.smart_mode
    })
    ElMessage.success('模块审批通过')
    moveToNextPendingModule()
  } catch (error) {
    ElMessage.error('审批失败')
  } finally {
    moduleApprovalPassing.value = false
  }
}

async function handleModuleFail() {
  moduleApprovalFailing.value = true
  try {
    const mod = pendingModules.value[currentModuleApprovalIndex.value]
    await api.failPendingModule(mod.id)
    ElMessage.warning('模块不通过')
    moveToNextPendingModule()
  } catch {
    ElMessage.error('操作失败')
  } finally {
    moduleApprovalFailing.value = false
  }
}

function moveToNextPendingModule() {
  currentModuleApprovalIndex.value++
  if (currentModuleApprovalIndex.value >= pendingModules.value.length) {
    ElMessage.success('所有待审模块已完成审批')
    showModuleApproval.value = false
    loadPendingModuleCount()
    return
  }
}

function moveToPrevPendingModule() {
  if (currentModuleApprovalIndex.value <= 0) return
  currentModuleApprovalIndex.value--
}

function moveToNextPendingModuleManual() {
  if (currentModuleApprovalIndex.value >= pendingModules.value.length - 1) return
  currentModuleApprovalIndex.value++
}

async function loadProjects() {
  try {
    const res = await api.getProjects()
    projects.value = res.data.projects
  } catch (error) {
    ElMessage.error('加载项目列表失败')
  }
}

async function saveModelConfig(config) {
  if (!config || !config.id) return
  try {
    await api.updateAIConfig(config.id, {
      model: config.model,
      temperature: config.temperature,
      max_tokens: config.max_tokens
    })
  } catch {
  }
}

async function loadAIConfigs() {
  try {
    const res = await api.getAIConfigs()
    aiConfigs.value = (res.data.configs || []).filter(c => c.is_active)
  } catch (error) {
    ElMessage.error('加载AI配置失败')
  }
}

async function loadSprints() {
  if (!form.value.projectId) return
  try {
    const res = await api.getSprints({ project_id: form.value.projectId })
    sprints.value = res.data.sprints || []
  } catch {}
}

function onSprintChange() {
}

async function createSprint() {
  const name = newSprint.value.name.trim()
  if (!name) { ElMessage.warning('请输入迭代名称'); return }
  if (sprints.value.some(s => s.name === name)) { ElMessage.warning('迭代名称已存在'); return }
  try {
    const res = await api.createSprint({ project_id: form.value.projectId, name })
    sprints.value.unshift(res.data.sprint)
    form.value.sprintId = res.data.sprint.id
    showCreateSprint.value = false
    newSprint.value.name = ''
    ElMessage.success('迭代创建成功')
  } catch (error) {
    ElMessage.error('创建迭代失败')
  }
}

function getProviderLabel(config) {
  const name = config.provider || config.id
  const model = config.model ? ` / ${config.model}` : ''
  return `${name}${model}`
}

function handlePaste(e) {
  if (uploadMode.value !== 'image') return
  const items = e.clipboardData?.items
  if (!items) return
  
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault()
      const blob = item.getAsFile()
      const filename = `screenshot_${Date.now()}.png`
      const file = new File([blob], filename, { type: 'image/png' })
      
      fileList.value.push({
        name: filename,
        url: URL.createObjectURL(blob),
        raw: file
      })
      imageDescriptions.value.push('')
      ElMessage.success('截图已添加')
      break
    }
  }
}

function switchMode(mode) {
  uploadMode.value = mode
  if (mode === 'knowledge') {
    loadKnowledgeFiles()
    return
  }
  fileList.value = []
  imageDescriptions.value = []
  currentDocContent.value = ''
  form.value.analyzeConfigId = ''
  form.value.genConfigId = ''
}

function goToAutomation() {
  router.push('/scripts')
}

async function createProject() {
  const trimmedName = newProject.value.name.trim()
  if (!trimmedName) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (projects.value.some(p => p.name === trimmedName)) {
    ElMessage.warning('项目名称已存在')
    return
  }
  try {
    const res = await api.createProject({ name: trimmedName, description: newProject.value.description })
    projects.value.unshift(res.data.project)
    form.value.projectId = res.data.project.id
    showCreateProject.value = false
    newProject.value = { name: '', description: '' }
    ElMessage.success('项目创建成功')
    loadStatusCount()
  } catch (error) {
    const msg = error.response?.data?.error || '创建项目失败'
    ElMessage.error(msg)
  }
}

function handleFileChange(file, newFileList) {
  const oldLen = fileList.value.length
  fileList.value = newFileList
  if (newFileList.length > oldLen) {
    imageDescriptions.value.push('')
    interactionPaths.value = [{ name: '', hint: '', description: '', steps: [''] }]
    currentFileIndex.value = newFileList.length - 1
    showDescDialog.value = true
    if (uploadMode.value === 'doc') {
      loadDocContent(newFileList.length - 1)
    }
  }
}

function handleFileRemove(file, newFileList) {
  fileList.value = newFileList
  imageDescriptions.value = newFileList.map((_, i) => imageDescriptions.value[i] || '')
}

function removeFile(index) {
  const newList = fileList.value.filter((_, i) => i !== index)
  handleFileRemove(null, newList)
}

function switchFile(index) {
  currentFileIndex.value = index
  descImageScale.value = 1
  descDragOffset.value = { x: 0, y: 0 }
  isCropping.value = false
  cropRect.value = null
  cropStart.value = null
  if (uploadMode.value === 'doc') {
    loadDocContent(index)
  }
}

async function loadDocContent(index) {
  const file = fileList.value[index]
  if (!file) return
  currentDocContent.value = ''
  try {
    if (file.raw && file.raw instanceof File) {
      const text = await file.raw.text()
      currentDocContent.value = text.slice(0, 10000)
    }
  } catch {
    currentDocContent.value = '无法读取文件内容'
  }
}

function handleDescZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  descImageScale.value = Math.max(0.5, Math.min(3, descImageScale.value + delta))
  if (descImageScale.value <= 1) {
    descDragOffset.value = { x: 0, y: 0 }
  }
}

function descZoomIn() {
  descImageScale.value = Math.min(3, descImageScale.value + 0.2)
}

function descZoomOut() {
  descImageScale.value = Math.max(0.5, descImageScale.value - 0.2)
  if (descImageScale.value <= 1) {
    descDragOffset.value = { x: 0, y: 0 }
  }
}

function startDescDrag(e) {
  if (descImageScale.value <= 1) return
  e.preventDefault()
  descIsDragging.value = true
  descDragStart.value = { x: e.clientX, y: e.clientY }
}

function onDescDrag(e) {
  if (!descIsDragging.value) return
  descDragOffset.value = {
    x: descDragOffset.value.x + (e.clientX - descDragStart.value.x) / descImageScale.value,
    y: descDragOffset.value.y + (e.clientY - descDragStart.value.y) / descImageScale.value
  }
  descDragStart.value = { x: e.clientX, y: e.clientY }
}

function endDescDrag() {
  descIsDragging.value = false
}

function startAnnotation() {
  isAnnotating.value = true
  descImageScale.value = 1
  descDragOffset.value = { x: 0, y: 0 }
}

const isAnnotateEmpty = computed(() => {
  const path = interactionPaths.value[0]
  if (!path) return true
  const hasSteps = path.steps && path.steps.some(s => s.trim())
  return (!path.description || !path.description.trim()) && !hasSteps
})

function startCrop() {
  isCropping.value = true
  cropRect.value = null
  cropStart.value = null
  cropLocked.value = false
}

function startCropDrag(e) {
  e.stopPropagation()
  if (cropLocked.value) return
  const viewer = e.currentTarget
  const rect = viewer.getBoundingClientRect()
  cropStart.value = { x: e.clientX - rect.left, y: e.clientY - rect.top }
}

function onCropDrag(e) {
  if (!cropStart.value || cropLocked.value) return
  const viewer = e.currentTarget
  const rect = viewer.getBoundingClientRect()
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  const x = Math.min(cropStart.value.x, cx)
  const y = Math.min(cropStart.value.y, cy)
  const w = Math.abs(cx - cropStart.value.x)
  const h = Math.abs(cy - cropStart.value.y)
  if (w < 5 || h < 5) {
    cropRect.value = null
    return
  }
  cropRect.value = { x, y, width: w, height: h }
}

function endCropDrag() {
  if (cropRect.value && cropRect.value.width >= 5 && cropRect.value.height >= 5) {
    cropLocked.value = true
  }
}

async function confirmCrop() {
  if (!cropRect.value || !cropImageRef.value) return
  cropEnding.value = true
  const img = cropImageRef.value
  const viewer = img.parentElement
  const viewerRect = viewer.getBoundingClientRect()
  const imgRect = img.getBoundingClientRect()
  const scaleX = img.naturalWidth / imgRect.width
  const scaleY = img.naturalHeight / imgRect.height
  const offsetX = imgRect.left - viewerRect.left
  const offsetY = imgRect.top - viewerRect.top
  const sx = (cropRect.value.x - offsetX) * scaleX
  const sy = (cropRect.value.y - offsetY) * scaleY
  const sw = cropRect.value.width * scaleX
  const sh = cropRect.value.height * scaleY
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(sw)
  canvas.height = Math.round(sh)
  const ctx = canvas.getContext('2d')
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh)
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
  const url = URL.createObjectURL(blob)
  const uid = Date.now()
  const cropFile = {
    uid: uid,
    name: `crop_${uid}.png`,
    url: url,
    raw: new File([blob], `crop_${uid}.png`, { type: 'image/png' })
  }
  fileList.value.push(cropFile)
  imageDescriptions.value.push('')
  currentFileIndex.value = fileList.value.length - 1
  isCropping.value = false
  cropRect.value = null
  cropStart.value = null
  cropLocked.value = false
  ElMessage.success('截图已添加')
  cropEnding.value = false
}

function cancelCrop() {
  isCropping.value = false
  cropRect.value = null
  cropStart.value = null
  cropLocked.value = false
}

function clearCropRect() {
  cropRect.value = null
  cropStart.value = null
  cropLocked.value = false
}

function addPath() {
  interactionPaths.value.push({
    name: '',
    hint: '',
    description: '',
    steps: ['']
  })
}

function removePath(index) {
  interactionPaths.value.splice(index, 1)
}

function addStep(pi) {
  interactionPaths.value[pi].steps.push('')
}

function removeStep(pi, si) {
  interactionPaths.value[pi].steps.splice(si, 1)
}

const annotating = ref(false)
async function runAIAnnotation(pi) {
  const path = interactionPaths.value[pi]
  if (!path || !path.description || !path.description.trim()) {
    ElMessage.warning('请先填写操作描述（大白话）')
    return
  }
  if (!form.value.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  annotating.value = true
  try {
    const formData = new FormData()
    formData.append('file', fileList.value[currentFileIndex.value].raw)
    formData.append('project_id', form.value.projectId)
    const uploadRes = await api.uploadFile(formData)
    const imageId = uploadRes.data.image.id
    const prompt = `用户想要：${path.description}\n\n请根据UI设计图，将用户的意图拆解为详细的操作步骤，每条步骤用简短中文描述。只输出JSON格式：{"steps": ["步骤1", "步骤2", ...]}`
    const res = await api.analyzeImage({ image_id: imageId, description: prompt })
    let steps = []
    try {
      const modules = res.data.modules || []
      if (modules.length > 0) {
        const content = modules[0].module || modules[0].function_description || ''
        const braceIdx = content.indexOf('{')
        if (braceIdx >= 0) {
          const parsed = JSON.parse(content.slice(braceIdx))
          steps = parsed.steps || []
        }
      }
      if (steps.length === 0 && typeof res.data.modules === 'string') {
        const braceIdx = res.data.modules.indexOf('{')
        if (braceIdx >= 0) {
          const parsed = JSON.parse(res.data.modules.slice(braceIdx))
          steps = parsed.steps || []
        }
      }
    } catch {}
    
    if (steps.length > 0) {
      path.steps = steps
      ElMessage.success(`AI 识别了 ${steps.length} 个步骤`)
    } else {
      ElMessage.warning('AI 未能识别到步骤，请手动填写')
    }
  } catch (error) {
    ElMessage.error('AI 识别失败: ' + (error.response?.data?.error || error.message))
  } finally {
    annotating.value = false
  }
}

function formatInteractionPaths(paths) {
  if (!paths || paths.length === 0) return ''
  let text = '\n\n## 用户标注的交互路径\n\n'
  paths.forEach((p, i) => {
    const validSteps = p.steps.filter(s => s.trim())
    if (validSteps.length === 0 && !p.description.trim()) return
    text += `路径${i + 1}：${p.name || `路径${i + 1}`}\n`
    if (p.description && p.description.trim()) text += `用户意图：${p.description}\n`
    if (validSteps.length > 0) {
      text += `操作步骤：${validSteps.join(' → ')}\n`
    }
    if (p.hint && p.hint.trim()) text += `位置提示：${p.hint}\n`
    text += '\n'
  })
  return text
}

async function openDescDialog() {
  if (fileList.value.length === 0) return
  currentFileIndex.value = 0
  showDescDialog.value = true
  if (uploadMode.value === 'doc') {
    loadDocContent(0)
  }
}

function getFilePreviewUrl(index) {
  const file = fileList.value[index]
  return file?.url || ''
}

function isCropFile(file) {
  return file.name && file.name.startsWith('crop_')
}

function removeThumb(index) {
  if (fileList.value.length <= 1) {
    ElMessage.warning('至少保留一张图片')
    return
  }
  removeFile(index)
  if (currentFileIndex.value >= fileList.value.length) {
    currentFileIndex.value = fileList.value.length - 1
  }
}

function clearAllAnnotations() {
  interactionPaths.value = [{ name: '', hint: '', description: '', steps: [''] }]
}

async function generateCases() {
  if (!canGenerate.value) return

  generating.value = true
  generateStep.value = 'uploading'
  uploadedImageIds.value = []

  try {
    for (const file of fileList.value) {
      const formData = new FormData()
      formData.append('file', file.raw)
      formData.append('project_id', form.value.projectId)
      const uploadRes = await api.uploadFile(formData)
      uploadedImageIds.value.push(uploadRes.data.image.id)
    }

    generateStep.value = 'analyzing'
    analyzing.value = true
    try {
      const interactionText = formatInteractionPaths(interactionPaths.value)
      const baseDesc = imageDescriptions.value[0] || ''
      const fullDescription = interactionText + (baseDesc ? '\n\n## 功能介绍\n' + baseDesc : '')
      const payload = {
        config_id: form.value.analyzeConfigId,
        description: fullDescription
      }
      if (form.value.linkedAnalysis && uploadedImageIds.value.length > 1) {
        payload.image_ids = uploadedImageIds.value
      } else {
        payload.image_id = uploadedImageIds.value[0]
      }
      const res = await api.analyzeImage(payload)
      modules.value = res.data.modules || []
      moduleForms.value = modules.value.map(m => ({
        module: m.module || '',
        ui_elements: (m.ui_elements || []).join(', '),
        function_description: m.function_description || '',
        interaction_flow: m.interaction_flow || '',
        test_focus: (m.test_focus || []).join(', '),
        case_types: [],
        case_count: form.value.caseCount,
        smart_mode: form.value.smartMode,
        use_vision: false,
        smart_case_type: false
      }))
      currentModuleIndex.value = 0
      generateStep.value = ''

      loadPendingModuleCount()
      showModuleReview.value = true
    } catch (err) {
      generateStep.value = ''
      ElMessage.error('图片分析失败：' + (err.response?.data?.error || err.message))
    } finally {
      analyzing.value = false
    }

  } catch (error) {
    generateStep.value = ''
    ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
  } finally {
    generating.value = false
  }
}

async function confirmModules() {
  const hasEmptyTypes = moduleForms.value.some(m => !m.smart_case_type && (!m.case_types || m.case_types.length === 0))
  if (hasEmptyTypes) {
    ElMessage.warning('请为每个模块至少选择一种用例类型')
    return
  }
  confirming.value = true
  try {
    generating.value = true
    const imageId = uploadedImageIds.value[0] || ''

    await api.savePendingModules(moduleForms.value.map(m => ({
      project_id: form.value.projectId,
      sprint_id: form.value.sprintId || '',
      image_id: imageId,
      module: m.module || '',
      ui_elements: m.ui_elements || '',
      function_description: m.function_description || '',
      interaction_flow: m.interaction_flow || '',
      test_focus: m.test_focus || '',
      case_types: (m.case_types || []).join(','),
      case_count: m.case_count || 10,
      smart_mode: m.smart_mode || false
    })))

    const pendingResult = await api.getPendingModules({
      project_id: form.value.projectId,
      sprint_id: form.value.sprintId || ''
    })
    const pendingList = (pendingResult.data.modules || []).filter(m => m.status === 'pending')
    for (const mod of moduleForms.value) {
      const matched = pendingList.find(m => m.module === mod.module)
      if (matched?.id) {
        await api.approvePendingModule(matched.id, {
          module: mod.module,
          ui_elements: mod.ui_elements,
          function_description: mod.function_description,
          interaction_flow: mod.interaction_flow,
          test_focus: mod.test_focus,
          case_types: (mod.case_types || []).join(','),
          case_count: mod.case_count || 10,
          smart_mode: mod.smart_mode || false
        })
      }
    }

    const promises = []
    const modelSupportsVision = genModelSupportsVision.value
    const hasMultipleImages = uploadedImageIds.value.length > 1
    const genInteractionText = formatInteractionPaths(interactionPaths.value)
    const genBaseDesc = imageDescriptions.value[0] || ''
    const batchTime = new Date().toISOString()
    for (let j = 0; j < moduleForms.value.length; j++) {
      const mod = moduleForms.value[j]
      const payload = {
        project_id: form.value.projectId,
        config_id: form.value.genConfigId,
        case_types: mod.smart_case_type ? [] : (mod.case_types || []),
        case_count: mod.case_count || 10,
        description: genInteractionText + (genBaseDesc ? '\n\n## 功能介绍\n' + genBaseDesc : ''),
        sprint_id: form.value.sprintId || '',
        generated_at: batchTime,
        modules: [{
          image_id: uploadedImageIds.value[0] || '',
          module: mod.module,
          ui_elements: mod.ui_elements.split(',').map(s => s.trim()).filter(Boolean),
          function_description: mod.function_description,
          interaction_flow: mod.interaction_flow,
          test_focus: mod.test_focus.split(',').map(s => s.trim()).filter(Boolean)
        }],
        smart_mode: mod.smart_mode || false
      }
      if (mod.use_vision && modelSupportsVision) {
        if (hasMultipleImages) {
          payload.image_ids = uploadedImageIds.value
        } else {
          payload.image_id = uploadedImageIds.value[0]
        }
      }
      promises.push(api.generateTestcases(payload))
    }
    await Promise.all(promises)
    showModuleReview.value = false
    generateStep.value = ''

    ElMessageBox.alert('生成完成，请到用例列表进行审批', '成功', {
      confirmButtonText: '前往查看',
      callback: () => {
        router.push({ path: '/cases', query: { project_id: form.value.projectId, sprint_id: form.value.sprintId, tab: 'pending' } })
      }
    })
  } catch (error) {
    ElMessage.error('生成失败：' + (error.response?.data?.error || error.message))
  } finally {
    generating.value = false
    confirming.value = false
  }
}

function prevModule() {
  if (currentModuleIndex.value > 0) {
    currentModuleIndex.value--
  }
}

function nextModule() {
  if (currentModuleIndex.value < moduleForms.value.length - 1) {
    currentModuleIndex.value++
  }
}

async function handleModulePassInReview() {
  modulePassing.value = true
  try {
    ElMessage.success('模块通过')
    if (currentModuleIndex.value < moduleForms.value.length - 1) {
      currentModuleIndex.value++
    }
  } catch {
    ElMessage.error('操作失败')
  } finally {
    modulePassing.value = false
  }
}

async function handleModuleFailInReview() {
  moduleFailing.value = true
  try {
    ElMessage.warning('模块不通过')
    if (currentModuleIndex.value < moduleForms.value.length - 1) {
      currentModuleIndex.value++
    }
  } catch {
  } finally {
    moduleFailing.value = false
  }
}

function handleModuleZoom(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  moduleImageScale.value = Math.max(0.5, Math.min(3, moduleImageScale.value + delta))
  if (moduleImageScale.value <= 1) moduleDragOffset.value = { x: 0, y: 0 }
}

function moduleZoomIn() { moduleImageScale.value = Math.min(3, moduleImageScale.value + 0.2) }
function moduleZoomOut() {
  moduleImageScale.value = Math.max(0.5, moduleImageScale.value - 0.2)
  if (moduleImageScale.value <= 1) moduleDragOffset.value = { x: 0, y: 0 }
}

function startModuleDrag(e) {
  if (moduleImageScale.value <= 1) return
  e.preventDefault()
  moduleIsDragging.value = true
  moduleDragStart.value = { x: e.clientX, y: e.clientY }
}

function onModuleDrag(e) {
  if (!moduleIsDragging.value) return
  moduleDragOffset.value = {
    x: moduleDragOffset.value.x + (e.clientX - moduleDragStart.value.x) / moduleImageScale.value,
    y: moduleDragOffset.value.y + (e.clientY - moduleDragStart.value.y) / moduleImageScale.value
  }
  moduleDragStart.value = { x: e.clientX, y: e.clientY }
}

function endModuleDrag() { moduleIsDragging.value = false }

function goToCases() {
  router.push({ path: '/cases', query: { project_id: form.value.projectId, sprint_id: form.value.sprintId } })
}

function goToModules() {
  router.push({ path: '/modules', query: { project_id: form.value.projectId, sprint_id: form.value.sprintId } })
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

// 后端给的是 ISO 字符串（2026-07-09T06:31:31.002457），直接铺进表格会断成两行
function formatTime(isoString) {
  if (!isoString) return ''
  const d = new Date(isoString)
  if (Number.isNaN(d.getTime())) return isoString
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

async function loadKnowledgeFiles() {
  if (!knowledgeProjectId.value) return
  kbFilesLoading.value = true
  try {
    const res = await api.getKnowledgeFiles({ project_id: knowledgeProjectId.value })
    knowledgeFiles.value = res.data.files || []
  } catch {
    ElMessage.error('加载知识文件失败')
  } finally {
    kbFilesLoading.value = false
  }
}

async function handleKnowledgeUpload(file) {
  if (!knowledgeProjectId.value) {
    ElMessage.warning('请先选择项目')
    return false
  }
  knowledgeUploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('project_id', knowledgeProjectId.value)
    await api.uploadKnowledgeFile(formData)
    ElMessage.success('上传成功')
    await loadKnowledgeFiles()
  } catch (error) {
    ElMessage.error('上传失败：' + (error.response?.data?.error || error.message))
  } finally {
    knowledgeUploading.value = false
  }
  return false
}

async function viewKnowledgeFile(row) {
  try {
    const res = await api.getKnowledgeFile(row.id)
    knowledgeViewContent.value = res.data.file.content || ''
    showKnowledgeViewer.value = true
  } catch {
    ElMessage.error('获取文件内容失败')
  }
}

async function deleteKnowledgeFile(row) {
  try {
    await ElMessageBox.confirm('确定删除该知识文件？', '提示', { type: 'warning' })
    await api.deleteKnowledgeFile(row.id)
    ElMessage.success('删除成功')
    await loadKnowledgeFiles()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}
</script>

<style scoped>
.upload-page {
  max-width: 1200px;
  margin: 0 auto;
}

/* ---------- 页头 ---------- */
.upload-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 40px;
  padding-bottom: 20px;
}
.upload-head-main { min-width: 0; }
.upload-title {
  margin: 5px 0 0;
  font-size: 21px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -.02em;
  color: var(--ink);
}
.upload-sub {
  margin: 7px 0 0;
  max-width: 560px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--muted);
}

/* 四个计数：数字仍是最重的一层，但只到 17px；标签退到 11px */
.upload-stats {
  flex: none;
  display: flex;
  align-items: flex-end;
  gap: 28px;
  padding-bottom: 1px;
}
.stat { text-align: right; }
.stat-num {
  display: block;
  font-size: 17px;
  line-height: 1.2;
  font-weight: 600;
  letter-spacing: -.01em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.stat-label {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

/* ---------- 模式切换：下划线 tab ---------- */
.mode-tabs {
  display: flex;
  align-items: center;
  gap: 28px;
  border-bottom: 1px solid var(--line-soft);
  margin-bottom: 18px;
}
.mode-tab {
  position: relative;
  padding: 0 0 11px;
  border: 0;
  background: none;
  font-size: 13.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.mode-tab:hover { color: var(--ink); }
.mode-tab.is-on { color: var(--ink); font-weight: 600; }
.mode-tab.is-on::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  border-radius: 2px 2px 0 0;
  background: var(--accent);
}
.mode-spacer { margin-left: auto; }
.mode-jump {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 9px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.mode-jump:hover { color: var(--accent); }
.jump-arrow { font-size: 11px; transform: translateY(-1px); }

/* ---------- 自动化工具入口：浅底一行 ---------- */
.tool-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  margin: 0 0 16px;
  padding: 11px 14px;
  background: #fafbfc;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  text-align: left;
  cursor: pointer;
  transition: background .14s ease, border-color .14s ease;
}
.tool-entry:hover { background: #f5f7fa; border-color: #d9dfe7; }
.tool-entry:focus-visible { outline: 2px solid rgba(0, 113, 227, .35); outline-offset: 1px; }
.tool-entry-icon {
  flex: none;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  background: #fff;
  border: 1px solid var(--line-soft);
  color: var(--ink-2);
}
.tool-entry-body { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 12px; }
.tool-entry-title { flex: none; font-size: 13px; font-weight: 600; color: var(--ink); }
.tool-entry-desc {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-entry-arrow {
  flex: none;
  font-size: 13px;
  color: var(--faint);
  transition: transform .14s ease, color .14s ease;
}
.tool-entry:hover .tool-entry-arrow { color: var(--accent); transform: translateX(3px); }

/* 卡片：去掉投影，只留一条边。静止的容器不需要浮在页面上 */
.upload-card,
.knowledge-card {
  border-radius: var(--radius-card);
  border: 1px solid var(--line-soft);
  box-shadow: none;
  background: #fff;
}

.upload-card :deep(.el-card__body),
.knowledge-card :deep(.el-card__body) {
  padding: 0;
}

/* 知识库：卡内不再重复一遍标题，只留一条工具栏 */
.kb-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line-soft);
}
.kb-toolbar .el-select__wrapper {
  min-height: 32px;
  border-radius: var(--radius-ctl);
  box-shadow: 0 0 0 1px var(--line-soft) inset;
  font-size: 13px;
}
.kb-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.kb-spacer { margin-left: auto; }
.knowledge-card :deep(.el-empty) { padding: 28px 0 32px; }

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.card-header {
  font-size: 18px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.3px;
}

.card-body-layout {
  display: flex;
  gap: 0;
  min-height: 420px;
}

.config-panel {
  flex: 4;
  padding: 28px 32px 20px;
  border-right: 1px solid rgba(0, 0, 0, 0.05);
}

.upload-panel {
  flex: 5;
  padding: 28px 32px 20px;
  display: flex;
  flex-direction: column;
}

/* 区块标题：跟着 eyebrow 的尺寸走，不再是大写英文 + 1.5px 字距（中文受不了这个字距） */
.section-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: .02em;
  margin-bottom: 16px;
}

/* 配置区限宽：1024 堆叠布局下不把下拉拉到 800px 宽，读起来不散。
   字段本身的写法在 styles/forms.css（.form-block / .form-field …）。 */
.config-panel .form-block {
  max-width: 560px;
  padding-bottom: 14px;
}

/* 模型参数：字段的附属信息，淡底 + 零缩进，不再是一个带边框的灰盒子 */
.up-model {
  margin: 8px 0 4px;
  padding: 9px 11px 10px;
  background: #f8fafc;
  border-radius: 8px;
}

.up-model-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 20px;
}

.up-model-name {
  flex: 1;
  min-width: 0;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.up-model-act {
  flex: none;
  border: 0;
  background: none;
  padding: 0;
  font-size: 11.5px;
  color: #9aa3b1;
  cursor: pointer;
  transition: color .14s ease;
}

.up-model-act:hover {
  color: var(--accent);
}

.up-model-input {
  flex: none;
  width: 190px;
}

.up-model-input :deep(.el-input__wrapper) {
  height: 26px;
  padding: 0 0 3px;
  background: transparent;
  border-radius: 0;
  box-shadow: none;
  border-bottom: 1px solid var(--accent);
}

.up-model-input :deep(.el-input__inner) {
  font-family: var(--mono);
  font-size: 12px;
}

.up-model-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.up-model-k {
  font-size: 11px;
  color: var(--muted);
}

.up-num {
  flex: none;
}

.up-num :deep(.el-input__wrapper) {
  height: 26px;
  padding: 0 6px;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 0 0 1px var(--line-soft) inset;
}

.up-num :deep(.el-input__inner) {
  font-size: 12px;
  color: var(--ink);
}

.up-num :deep(.el-input-number__decrease),
.up-num :deep(.el-input-number__increase) {
  background: transparent;
  border-color: var(--line-faint);
}

.up-model-api {
  margin-top: 6px;
  font-family: var(--mono);
  font-size: 11px;
  color: #9aa3b1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 虚线框吃满右侧整列的高度：原来它固定 280 高、垂直居中，
   卡片一变高下面就空出一大片。
   注意 el-upload 在 drag 模式下多包了一层 .el-upload，只给 .el-upload-dragger
   设 flex:1 是撑不开的，中间那层也得给。 */
.upload-dragger {
  width: 100%;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.upload-dragger :deep(.el-upload) {
  flex: 1;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
}

.upload-dragger :deep(.el-upload-dragger) {
  flex: 1;
  width: 100%;
  box-sizing: border-box;
  border: 1.5px dashed rgba(0, 0, 0, 0.1);
  border-radius: 16px;
  background: #fafafa;
  transition: all 0.3s ease;
  padding: 32px 24px;
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.upload-dragger :deep(.el-upload-dragger:hover) {
  border-color: rgba(0, 0, 0, 0.2);
  background: #f5f5f7;
}

.upload-dragger :deep(.el-icon--upload) {
  font-size: 40px;
  color: #86868b;
  margin-bottom: 12px;
  transition: color 0.3s;
}

.upload-dragger :deep(.el-upload-dragger:hover .el-icon--upload) {
  color: #0071e3;
}

.upload-dragger :deep(.el-upload__text) {
  font-size: 15px;
  color: #1d1d1f;
  font-weight: 500;
}

.upload-dragger :deep(.el-upload__text em) {
  color: #0071e3;
  font-style: normal;
}

.upload-dragger :deep(.el-upload__tip) {
  font-size: 12px;
  color: #86868b;
  margin-top: 8px;
}

.file-previews {
  flex: none;          /* 有预览时不能让虚线框把它压扁 */
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-wrap: wrap;
  /* 文件多时在自己这格里滚，不把卡片顶高、也不让虚线框压到最小 */
  max-height: 108px;
  overflow-y: auto;
}

.file-preview-item {
  width: 72px;
  height: 72px;
  border-radius: 10px;
  overflow: hidden;
  position: relative;
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #f5f5f7;
}

/* 文档不该塞进 72×72 的方块里：文件名被挤到 9px 还截断。
   改成一行「图标 + 文件名」，宽度跟着内容走。 */
.file-preview-item.is-doc {
  width: auto;
  max-width: 100%;
  height: 40px;
  padding: 0 26px 0 10px;
  display: flex;
  align-items: center;
  background: #fafbfc;
  border-color: var(--line-soft);
  border-radius: var(--radius-ctl);
  overflow: visible;
}
.file-preview-item.is-doc .file-doc-preview {
  width: auto;
  height: auto;
  flex-direction: row;
  gap: 8px;
  color: var(--muted);
}
.file-preview-item.is-doc .file-doc-preview span {
  max-width: 340px;
  font-size: 12px;
  line-height: 1.3;
  text-align: left;
  color: var(--ink-2);
}
.file-preview-item.is-doc .file-preview-badge {
  top: 50%;
  right: 4px;
  width: 18px;
  height: 18px;
  margin-top: -9px;
  font-size: 12px;
}

.file-preview-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.file-doc-preview {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: #86868b;
}

.file-doc-preview span {
  font-size: 9px;
  text-align: center;
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60px;
}

.file-preview-badge {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 20px;
  height: 20px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.2s;
}

.file-preview-badge:hover {
  background: rgba(0, 0, 0, 0.75);
}

/* 底栏：左侧三个出口是文字，右侧只有一个蓝按钮 */
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  border-top: 1px solid var(--line-soft);
}
.action-secondary {
  display: flex;
  align-items: center;
  gap: 10px;
}
.text-act {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}
.text-act:not(:disabled):hover { color: var(--accent); }
.text-act:disabled { color: #c9cfd8; cursor: default; }
.act-sep { font-size: 11px; color: #dfe3e8; user-select: none; }

.desc-layout {
  display: flex;
  gap: 20px;
  height: min(62vh, 640px);
}

:is(.desc-dialog, .form-dialog) :deep(.el-dialog__title) {
  font-size: 19px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -.2px;
}

.desc-image-panel {
  width: 50%;
  display: flex;
  flex-direction: column;
  background: #f6f7f9;
  border-radius: 14px;
  padding: 12px;
  border: 0.5px solid rgba(0, 0, 0, 0.06);
}

.desc-image-viewer {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border-radius: 10px;
  overflow: hidden;
  border: 0.5px solid rgba(0, 0, 0, 0.04);
}

.desc-image-viewer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.2s ease;
  user-select: none;
}

/* 底部操作区：一条细线把它和内容分开，否则按钮像浮在空白里 */
.desc-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding-top: 2px;
}

.desc-footer-left {
  display: flex;
  gap: 8px;
}

.crop-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  flex-direction: column;
}

.crop-image-container {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  position: relative;
  cursor: crosshair;
  padding: 20px;
}

.crop-full-image {
  max-width: 90vw;
  max-height: 80vh;
  object-fit: contain;
  user-select: none;
}

.crop-overlay {
  position: absolute;
  border: 2px dashed #409EFF;
  background: rgba(64, 158, 255, 0.15);
  pointer-events: none;
  z-index: 2;
}

.crop-event-layer {
  position: absolute;
  inset: 0;
  z-index: 1;
}

.crop-fullscreen-bar {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 16px 0 24px;
}

.annotation-layout {
  display: flex;
  gap: 20px;
  flex: 1;
  padding: 20px;
  overflow: hidden;
}

.annotation-paths {
  width: 40%;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  padding: 16px 18px;
  overflow-y: auto;
}

.ann-head {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line-soft);
  margin-bottom: 4px;
}

.ann-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: .02em;
}

.ann-hint {
  font-size: 11.5px;
  color: var(--faint);
  line-height: 1.5;
}

/* 每条路径之间用一条线分开即可：白卡里再套灰块，
   一层套一层反而看不出哪几条输入属于同一条路径 */
.path-section {
  padding: 14px 0 4px;
  border-bottom: 1px solid var(--line-faint);
}

.path-section:last-of-type {
  border-bottom: 0;
}

.path-label {
  font-weight: 600;
  font-size: 13px;
  color: #333;
}

.path-desc-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.path-steps-section {
  margin-bottom: 12px;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}

.step-num {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
  width: 20px;
}

.path-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  margin-top: 8px;
}

/* 整宽的 text 按钮会把文字挤到中间，看起来像另一个主操作 */
.ann-add-path {
  align-self: flex-start;
  margin: 10px 0 2px;
  padding-left: 0;
  color: var(--muted);
}

.annotation-image {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border-radius: 10px;
  padding: 12px;
}

.desc-doc-viewer {
  flex: 1;
  background: #fff;
  border-radius: 10px;
  border: 0.5px solid rgba(0, 0, 0, 0.04);
  overflow: auto;
  padding: 16px;
}

.desc-doc-viewer pre {
  margin: 0;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #1d1d1f;
  white-space: pre-wrap;
  word-break: break-word;
}

.desc-image-thumbs {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.desc-thumb-wrap {
  position: relative;
}

.crop-badge {
  position: absolute;
  top: 2px;
  right: 2px;
  background: #409EFF;
  color: #fff;
  font-size: 9px;
  border-radius: 3px;
  padding: 1px 4px;
  line-height: 1.2;
  pointer-events: none;
}

.thumb-delete {
  position: absolute;
  top: 0;
  right: 0;
  width: 16px;
  height: 16px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  border-radius: 0 8px 0 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}

.thumb-delete:hover {
  background: rgba(220, 38, 38, 0.85);
}

.desc-image-thumbs img {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border: 2px solid rgba(0, 0, 0, 0.06);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s;
}

.desc-image-thumbs img.active {
  border-color: #0071e3;
}

.desc-image-thumbs img:hover {
  border-color: rgba(0, 0, 0, 0.15);
}

.desc-doc-thumb {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12px;
  color: #86868b;
  background: #f5f5f7;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.desc-doc-thumb.active {
  background: #0071e3;
  color: #fff;
}

.desc-doc-thumb:hover:not(.active) {
  background: #e8e8ed;
  color: #1d1d1f;
}

/* 页码与缩放在同一行：页码贴左、缩放贴右。
   原来两块各自居中堆叠，中间隔着 8px 空白，看着像掉出来的。 */
.desc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 4px 2px;
}

.desc-image-info {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
  letter-spacing: .02em;
}

.desc-zoom-controls {
  display: flex;
  align-items: center;
  gap: 2px;
  margin: 0;
}

.desc-zoom-btn {
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: background .14s ease, color .14s ease;
}

.desc-zoom-btn:hover {
  background: rgba(0, 0, 0, .05);
  color: var(--ink);
}

.desc-zoom-value {
  min-width: 40px;
  text-align: center;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
}

.desc-zoom-reset {
  margin-left: 6px;
  border: 0;
  background: none;
  padding: 0 2px;
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
  transition: color .14s ease;
}

.desc-zoom-reset:hover {
  color: var(--accent);
}

.desc-form-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.desc-form-label {
  font-size: 11.5px;
  color: var(--muted);
  margin-bottom: 8px;
}

.desc-form-panel .el-textarea {
  flex: 1;
}

.desc-form-panel .el-textarea :deep(.el-textarea__inner) {
  height: 100% !important;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.75;
  padding: 14px 16px;
  color: var(--ink-2);
  background: #fbfcfd;
  border: 1px solid var(--line-soft);
  box-shadow: none;
}

.desc-form-panel .el-textarea :deep(.el-textarea__inner:focus) {
  background: #fff;
  border-color: var(--accent);
  box-shadow: none;
}

.progress-dialog :deep(.el-dialog) {
  border-radius: 16px;
}

.progress-content {
  text-align: center;
  padding: 32px 0 16px;
}

.progress-text {
  margin-top: 24px;
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}

.progress-sub {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}

.ios-spinner {
  width: 36px;
  height: 36px;
  position: relative;
  margin: 0 auto;
}

.ios-bar {
  position: absolute;
  left: 50%;
  top: 8%;
  width: 6%;
  height: 18%;
  margin-left: -3%;
  background: #0071e3;
  border-radius: 2px;
  transform-origin: 50% 350%;
  opacity: 0.05;
  animation: ios-fade 1.2s linear infinite;
}

@keyframes ios-fade {
  0%, 100% { opacity: 0.05; }
  10% { opacity: 1; }
  50% { opacity: 0.05; }
}

/* 窄屏（1366 的笔记本开两栏、或者窗口拖窄）：配置区与上传区上下堆叠，
   否则左栏被压到 300px 以下，字段名和值会挤在一起 */
@media (max-width: 1080px) {
  .upload-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
  }

  .upload-stats {
    gap: 22px;
  }

  .stat {
    text-align: left;
  }

  .card-body-layout {
    flex-direction: column;
  }

  .config-panel {
    border-right: 0;
    border-bottom: 1px solid var(--line-soft);
  }

  .upload-panel {
    min-height: 300px;
    padding-top: 12px;
  }
}
</style>
