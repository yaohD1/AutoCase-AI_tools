# 待审用例功能实施方案

## 需求验收

### 用户选择的方案：
- ✅ 待审用例持久化到数据库
- ✅ CaseList页面新增Tab展示
- ✅ Failed用例标记为"待改进"
- ✅ 审批弹窗显示完整详情

---

## 核心需求

1. **生成流程改变**
   - 生成用例后不再直接加入到用例列表
   - 改为加入到"待审用例"列表
   
2. **待审用例列表**
   - 在CaseList页面新增"待审"和"已通过"两个Tab
   - 支持分页展示
   
3. **审批功能**
   - 点击"待审用例"按钮打开审批弹窗
   - 弹窗显示完整的用例详情（模块、测试点、标题、优先级、前置条件、步骤、预期）
   - Pass按钮（绿色）- 通过审批，加入到正式用例列表
   - Failed按钮（红色）- 标记为"待改进""
   - 点击Pass/Failed后自动跳转下一个用例
   
4. **用例编辑**
   - 审批过程中支持修改用例
   - 修改后可以Pass
   
5. **待改进用例**
   - Failed的用例标记为"待改进"
   - 不出现在已通过列表，保留在待审列表
   - 支持后续再次编辑和审批

---

## 技术方案

### 方案A：新增status字段（推荐）

**优点：**
- 简单，只需修改现有TestCase模型
- 所有用例在同一张表，易于管理
- 状态清晰（pending/approved/failed）

**改动：**
- TestCase表新增status字段
- status取值：'pending'（待审）、'approved'（已通过）、'failed'（待改进）
- 数据库迁移添加字段

---

### 方案B：新增PendingTestCase表（备选）

**优点：**
- 待审用例和正式用例物理分离
- 可以有不同的字段结构

**缺点：**
- 复杂，需要新表、新模型、新路由
- 审批后需要迁移数据
- 维护成本高

---

### 方案C：前端临时方案（不推荐）

**优点：**
- 不改后端，前端临时存储

**缺点：**
- 刷新页面丢失
- 不符合用户需求（持久化）

---

## 推荐：方案A（新增status字段）

---

## 详细实施计划（方案A）

### Phase 1：数据库改造

#### Step 1.1：修改TestCase模型

**文件：** `backend/app/models/testcase.py`

**改动位置：** Line 22之后

**新增字段：**
```python
status = db.Column(db.String(20), default='pending')
```

**status取值说明：**
- `'pending'` - 待审批（默认）
- `'approved'` - 已通过审批
- `'failed'` - 待改进（审批失败）

**修改to_dict方法：** Line 27-43
```python
def to_dict(self):
    return {
        'id': self.id,
        'project_id': self.project_id,
        'module': self.module,
        'test_point': self.test_point,
        'title': self.title,
        'priority': self.priority,
        'preconditions': self.preconditions,
        'steps': self.steps,
        'expected': self.expected,
        'case_type': self.case_type,
        'image_source': self.image_source,
        'ai_provider': self.ai_provider,
        'status': self.status,  # 新增
        'created_at': self.created_at.isoformat() if self.created_at else None,
        'updated_at': self.updated_at.isoformat() if self.updated_at else None
    }
```

---

#### Step 1.2：数据库迁移

**方案：手动SQL执行**

```sql
-- 添加status字段
ALTER TABLE testcases ADD COLUMN status VARCHAR(20) DEFAULT 'pending';

-- 更新现有数据为approved
UPDATE testcases SET status = 'approved' WHERE status IS NULL;
```

**执行脚本创建：**

文件：`backend/migrations/add_status_field.py`

```python
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), '..', 'storage', 'app.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 添加字段
cursor.execute('ALTER TABLE testcases ADD COLUMN status VARCHAR(20) DEFAULT "pending"')

# 更新现有数据
cursor.execute('UPDATE testcases SET status = "approved" WHERE status IS NULL OR status = "pending"')

conn.commit()
conn.close()

print('Migration completed: status field added')
```

---

### Phase 2：后端路由改造

#### Step 2.1：修改生成用例路由

**文件：** `backend/app/routes/testcase.py`

**改动位置：** Line 60-74

**当前逻辑：**
```python
for case_data in testcases:
    testcase = TestCase(...)
    db.session.add(testcase)
```

**改为：**
```python
for case_data in testcases:
    testcase = TestCase(
        project_id=project_id,
        module=case_data.get('module', ''),
        test_point=case_data.get('test_point', ''),
        title=case_data.get('title', ''),
        priority=case_data.get('priority', 'P2'),
        preconditions=case_data.get('preconditions', ''),
        steps=json.dumps(case_data.get('steps', []), ensure_ascii=False),
        expected=case_data.get('expected', ''),
        case_type=case_data.get('case_type', 'functional'),
        image_source=image.filename,
        ai_provider=provider,
        status='pending'  # 新增：默认待审批
    )
    db.session.add(testcase)
```

---

#### Step 2.2：新增按状态查询路由

**文件：** `backend/app/routes/testcase.py`

**新增路由：** `/api/cases/pending`

```python
@testcase_bp.route('/cases/pending', methods=['GET'])
def get_pending_testcases():
    """获取待审用例列表"""
    project_id = request.args.get('project_id')
    
    query = TestCase.query.filter_by(status='pending')
    
    if project_id:
        query = query.filter_by(project_id=project_id)
    
    testcases = query.order_by(TestCase.created_at.desc()).all()
    
    return jsonify({'testcases': [tc.to_dict() for tc in testcases]}), 200
```

---

#### Step 2.3：新增审批路由

**文件：** `backend/app/routes/testcase.py`

**新增路由：** `/api/cases/<case_id>/approve`

```python
@testcase_bp.route('/cases/<case_id>/approve', methods=['POST'])
def approve_testcase(case_id):
    """审批通过用例"""
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404
    
    data = request.get_json()
    
    # 支持审批时修改用例内容
    if data.get('module'):
        testcase.module = data['module']
    if data.get('test_point'):
        testcase.test_point = data['test_point']
    if data.get('title'):
        testcase.title = data['title']
    if data.get('priority'):
        testcase.priority = data['priority']
    if data.get('preconditions'):
        testcase.preconditions = data['preconditions']
    if data.get('steps'):
        testcase.steps = data['steps']
    if data.get('expected'):
        testcase.expected = data['expected']
    if data.get('case_type'):
        testcase.case_type = data['case_type']
    
    # 设置状态为已通过
    testcase.status = 'approved'
    testcase.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'testcase': testcase.to_dict()
    }), 200
```

---

#### Step 2.4：新增标记failed路由

**文件：** `backend/app/routes/testcase.py`

**新增路由：** `/api/cases/<case_id>/fail`

```python
@testcase_bp.route('/cases/<case_id>/fail', methods=['POST'])
def fail_testcase(case_id):
    """标记用例为待改进"""
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404
    
    # 设置状态为待改进
    testcase.status = 'failed'
    testcase.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Testcase marked as failed',
        'testcase': testcase.to_dict()
    }), 200
```

---

#### Step 2.5：修改查询路由支持status过滤

**文件：** `backend/app/routes/testcase.py`

**修改路由：** `/api/cases` (Line 86-100)

```python
@testcase_bp.route('/cases', methods=['GET'])
def get_testcases():
    project_id = request.args.get('project_id')
    case_type = request.args.get('case_type')
    status = request.args.get('status')  # 新增status参数
    
    query = TestCase.query
    
    if project_id:
        query = query.filter_by(project_id=project_id)
    if case_type:
        query = query.filter_by(case_type=case_type)
    if status:
        query = query.filter_by(status=status)
    
    testcases = query.order_by(TestCase.created_at.desc()).all()
    
    return jsonify({'testcases': [tc.to_dict() for tc in testcases]}), 200
```

---

### Phase 3：前端API改造

#### Step 3.1：新增待审用例API

**文件：** `frontend/src/api/index.js`

**新增方法：** Line 60-61附近

```javascript
// 待审用例
getPendingTestcases(params) {
  return api.get('/cases/pending', { params })
},

approveTestcase(id, data) {
  return api.post(`/cases/${id}/approve`, data)
},

failTestcase(id) {
  return api.post(`/cases/${id}/fail`)
}
```

---

### Phase 4：前端界面改造

#### Step 4.1：CaseList页面新增Tab

**文件：** `frontend/src/views/CaseList.vue`

**改动位置：** Line 4-21（card-header部分）

**新增Tab组件：**
```vue
<template #header>
  <div class="card-header">
    <el-tabs v-model="activeTab" @tab-change="handleTabChange">
      <el-tab-pane label="待审用例" name="pending">
        <span>待审用例 ({{ pendingCount }})</span>
      </el-tab-pane>
      <el-tab-pane label="已通过" name="approved">
        <span>已通过用例</span>
      </el-tab-pane>
    </el-tabs>
    
    <div class="header-actions">
      <el-select ...>...</el-select>
      <el-button ...>批量删除</el-button>
      <el-button ...>导出XMind</el-button>
    </div>
  </div>
</template>
```

---

#### Step 4.2：根据Tab切换数据源

**文件：** `frontend/src/views/CaseList.vue`

**新增ref：**
```javascript
const activeTab = ref('pending')
const pendingTestcases = ref([])
const pendingCount = ref(0)
```

**新增handleTabChange方法：**
```javascript
function handleTabChange(tabName) {
  if (tabName === 'pending') {
    loadPendingTestcases()
  } else if (tabName === 'approved') {
    loadTestcases()
  }
}
```

**新增loadPendingTestcases方法：**
```javascript
async function loadPendingTestcases() {
  if (!selectedProject.value) {
    return
  }
  
  loading.value = true
  
  try {
    const res = await api.getPendingTestcases({ project_id: selectedProject.value })
    pendingTestcases.value = res.data.testcases
    pendingCount.value = pendingTestcases.value.length
    
    // 根据Tab切换显示数据
    testcases.value = pendingTestcases.value
    total.value = pendingCount.value
  } catch (error) {
    ElMessage.error('加载待审用例失败')
  } finally {
    loading.value = false
  }
}
```

---

#### Step 4.3：修改loadTestcases只加载approved

**文件：** `frontend/src/views/CaseList.vue`

**修改loadTestcases方法：** Line 129-145

```javascript
async function loadTestcases() {
  if (!selectedProject.value) {
    return
  }
  
  loading.value = true
  
  try {
    // 只加载已通过的用例
    const res = await api.getTestcases({ 
      project_id: selectedProject.value,
      status: 'approved'
    })
    testcases.value = res.data.testcases
    total.value = testcases.value.length
  } catch (error) {
    ElMessage.error('加载用例列表失败')
  } finally {
    loading.value = false
  }
}
```

---

#### Step 4.4：修改onMounted逻辑

**文件：** `frontend/src/views/CaseList.vue`

**修改onMounted：** Line 106-125

```javascript
onMounted(() => {
  loadProjects()
})

async function loadProjects() {
  try {
    const res = await api.getProjects()
    projects.value = res.data.projects
    
    if (route.query.project_id) {
      selectedProject.value = route.query.project_id
    } else if (projects.value.length > 0) {
      selectedProject.value = projects.value[0].id
    }
    
    if (selectedProject.value) {
      // 根据默认Tab加载对应数据
      if (activeTab.value === 'pending') {
        loadPendingTestcases()
      } else {
        loadTestcases()
      }
    }
  } catch (error) {
    ElMessage.error('加载项目列表失败')
  }
}
```

---

### Phase 5：审批弹窗改造

#### Step 5.1：创建审批弹窗组件

**文件：** `frontend/src/views/CaseList.vue`

**新增弹窗：** Line 66-84之后

```vue
<!-- 审批弹窗 -->
<el-dialog 
  v-model="showReview" 
  title="审批用例" 
  width="800px"
  :close-on-click-modal="false">
  
  <el-form :model="reviewForm" label-width="120px">
    <el-form-item label="模块">
      <el-input v-model="reviewForm.module" />
    </el-form-item>
    
    <el-form-item label="测试点">
      <el-input v-model="reviewForm.test_point" />
    </el-form-item>
    
    <el-form-item label="用例标题">
      <el-input v-model="reviewForm.title" />
    </el-form-item>
    
    <el-form-item label="优先级">
      <el-select v-model="reviewForm.priority">
        <el-option label="P0" value="P0" />
        <el-option label="P1" value="P1" />
        <el-option label="P2" value="P2" />
      </el-select>
    </el-form-item>
    
    <el-form-item label="前置条件">
      <el-input 
        v-model="reviewForm.preconditions" 
        type="textarea"
        :rows="3" />
    </el-form-item>
    
    <el-form-item label="测试步骤">
      <div v-for="(step, index) in reviewForm.stepsList" :key="index" class="step-item">
        <el-input v-model="reviewForm.stepsList[index]" style="width: 90%" />
        <el-button 
          type="danger" 
          size="small"
          @click="removeStep(index)"
          style="margin-left: 10px">
          删除
        </el-button>
      </div>
      <el-button type="primary" size="small" @click="addStep">
        添加步骤
      </el-button>
    </el-form-item>
    
    <el-form-item label="预期结果">
      <el-input 
        v-model="reviewForm.expected" 
        type="textarea"
        :rows="2" />
    </el-form-item>
    
    <el-form-item label="用例类型">
      <el-select v-model="reviewForm.case_type">
        <el-option label="功能测试" value="functional" />
        <el-option label="UI测试" value="ui" />
        <el-option label="边界测试" value="boundary" />
        <el-option label="异常测试" value="exception" />
      </el-select>
    </el-form-item>
  </el-form>
  
  <template #footer>
    <div class="review-footer">
      <span class="progress-info">
        审批进度: {{ currentReviewIndex + 1 }} / {{ totalPendingCount }}
      </span>
      <div class="review-actions">
        <el-button type="danger" @click="handleFail" :loading="failing">
          Failed
        </el-button>
        <el-button type="success" @click="handlePass" :loading="passing">
          Pass
        </el-button>
      </div>
    </div>
  </template>
</el-dialog>
```

---

#### Step 5.2：添加审批相关状态和方法

**文件：** `frontend/src/views/CaseList.vue`

**新增ref：**
```javascript
const showReview = ref(false)
const currentReviewIndex = ref(0)
const totalPendingCount = ref(0)
const passing = ref(false)
const failing = ref(false)

const reviewForm = ref({
  id: '',
  module: '',
  test_point: '',
  title: '',
  priority: 'P2',
  preconditions: '',
  stepsList: [],
  expected: '',
  case_type: 'functional'
})
```

---

#### Step 5.3：添加审批方法

**文件：** `frontend/src/views/CaseList.vue`

**新增方法：**

```javascript
// 打开审批弹窗
function openReviewDialog() {
  if (pendingTestcases.value.length === 0) {
    ElMessage.warning('没有待审用例')
    return
  }
  
  currentReviewIndex.value = 0
  totalPendingCount.value = pendingTestcases.value.length
  
  // 加载第一个用例到表单
  loadTestCaseToForm(pendingTestcases.value[0])
  
  showReview.value = true
}

// 加载用例数据到表单
function loadTestCaseToForm(testcase) {
  reviewForm.value = {
    id: testcase.id,
    module: testcase.module,
    test_point: testcase.test_point,
    title: testcase.title,
    priority: testcase.priority,
    preconditions: testcase.preconditions || '',
    stepsList: parseSteps(testcase.steps),
    expected: testcase.expected || '',
    case_type: testcase.case_type
  }
}

// 解析步骤为数组
function parseSteps(steps) {
  if (!steps) return []
  
  try {
    const parsed = JSON.parse(steps)
    return parsed.map(step => step.replace(/步骤\d+：|步骤：/, '').trim())
  } catch {
    return steps.split('\n').map(step => step.trim()).filter(step => step)
  }
}

// 添加步骤
function addStep() {
  reviewForm.value.stepsList.push('')
}

// 删除步骤
function removeStep(index) {
  reviewForm.value.stepsList.splice(index, 1)
}

// Pass处理
async function handlePass() {
  passing.value = true
  
  try {
    // 准备数据
    const data = {
      module: reviewForm.value.module,
      test_point: reviewForm.value.test_point,
      title: reviewForm.value.title,
      priority: reviewForm.value.priority,
      preconditions: reviewForm.value.preconditions,
      steps: JSON.stringify(
        reviewForm.value.stepsList.map(step => `步骤：${step}`)
      ),
      expected: `预期：${reviewForm.value.expected}`,
      case_type: reviewForm.value.case_type
    }
    
    await api.approveTestcase(reviewForm.value.id, data)
    
    ElMessage.success('用例审批通过')
    
    // 移动到下一个
    moveToNextTestCase()
    
  } catch (error) {
    ElMessage.error('审批失败: ' + (error.response?.data?.error || error.message))
  } finally {
    passing.value = false
  }
}

// Failed处理
async function handleFail() {
  failing.value = true
  
  try {
    await api.failTestcase(reviewForm.value.id)
    
    ElMessage.warning('用例标记为待改进')
    
    // 移动到下一个
    moveToNextTestCase()
    
  } catch (error) {
    ElMessage.error('标记失败: ' + (error.response?.data?.error || error.message))
  } finally {
    failing.value = false
  }
}

// 移动到下一个用例
function moveToNextTestCase() {
  currentReviewIndex.value++
  
  if (currentReviewIndex.value >= totalPendingCount.value) {
    // 审批完成
    ElMessage.success('所有待审用例已完成审批')
    showReview.value = false
    loadPendingTestcases()  // 刷新列表
    return
  }
  
  // 加载下一个用例
  const nextTestcase = pendingTestcases.value[currentReviewIndex.value]
  loadTestCaseToForm(nextTestcase)
}
```

---

#### Step 5.4：修改表格操作列

**文件：** `frontend/src/views/CaseList.vue`

**改动位置：** Line 50-55

**根据Tab显示不同按钮：**
```vue
<el-table-column label="操作" width="150" fixed="right">
  <template #default="{ row }">
    <el-button text @click="viewTestcase(row)">查看</el-button>
    
    <!-- 待审Tab显示审批按钮 -->
    <el-button 
      v-if="activeTab === 'pending'"
      text 
      type="primary"
      @click="reviewSingleTestcase(row)">
      审批
    </el-button>
    
    <!-- 已通过Tab显示删除按钮 -->
    <el-button 
      v-if="activeTab === 'approved'"
      text 
      type="danger"
      @click="deleteTestcase(row.id)">
      删除
    </el-button>
  </template>
</el-table-column>
```

**新增单个审批方法：**
```javascript
function reviewSingleTestcase(testcase) {
  // 找到该用例在列表中的索引
  const index = pendingTestcases.value.findIndex(tc => tc.id === testcase.id)
  
  currentReviewIndex.value = index
  totalPendingCount.value = pendingTestcases.value.length
  
  loadTestCaseToForm(testcase)
  showReview.value = true
}
```

---

#### Step 5.5：添加样式

**文件：** `frontend/src/views/CaseList.vue`

**位置：** style部分（Line 232-253）

**新增样式：**
```css
.review-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.progress-info {
  color: #909399;
  font-size: 14px;
}

.review-actions {
  display: flex;
  gap: 10px;
}

.step-item {
  margin-bottom: 10px;
  display: flex;
  align-items: center;
}
```

---

## 完整改造清单

### 后端文件（需要重启）

| 文件 | 改动内容 | 改动量 |
|------|----------|--------|
| backend/app/models/testcase.py | 新增status字段 + to_dict方法 | 2处 |
| backend/migrations/add_status_field.py | 新建迁移脚本 | 新文件 |
| backend/app/routes/testcase.py | 5个路由改动/新增 | 5处 |

**具体：**
1.TestCase模型新增status字段
2. 数据库迁移脚本添加status列
3. 修改generate_testcases路由（默认status='pending'）
4. 新增`/cases/pending`路由
5. 新增`/cases/<id>/approve`路由
6. 新增`/cases/<id>/fail`路由
7. 修改`/cases`路由支持status过滤

---

### 前端文件（刷新页面）

| 文件 | 改动内容 | 改动量 |
|------|----------|--------|
| frontend/src/api/index.js | 新增3个API方法 | 3处 |
| frontend/src/views/CaseList.vue | 大量改动 | 约15处 |

**具体：**
1. 新增activeTab, pendingTestcases等ref
2. 新增Tabs组件
3. 新增审批弹窗组件
4. 新增handleTabChange方法
5. 新增loadPendingTestcases方法
6. 修改loadTestcases方法（过滤status='approved'）
7. 新增openReviewDialog方法
8. 新增loadTestCaseToForm方法
9. 新增handlePass方法
10. 新增handleFail方法
11. 新增moveToNextTestCase方法
12. 新增addStep/removeStep方法
13. 修改表格操作列按钮
14. 新增样式

---

## 实施顺序建议

**推荐顺序：**

**Phase 1：数据库改造（最优先）**
1. ✅ 修改TestCase模型添加status字段
2. ✅ 创建并执行数据库迁移脚本
3. ✅ 验证字段添加成功

**Phase 2：后端路由**
4. ✅ 修改generate_testcases路由
5. ✅ 新增审批相关路由
6. ✅ 后端测试API

**Phase 3：前端界面**
7. ✅ 新增API方法
8. ✅ CaseList页面新增Tab
9. ✅ 审批弹窗组件
10. ✅ 审批方法实现

**Phase 4：集成测试**
11. ✅ 生成用例测试
12. ✅ 审批流程测试
13. ✅ Pass/Failed测试

---

## 测试验证清单

### 1. 数据库测试

**测试：**
```bash
cd backend/storage
sqlite3 app.db "SELECT * FROM testcases LIMIT 1"
```

**验证：**
- ✅ testcases表有status字段
- ✅ 默认值为'pending'
- ✅ 现有数据status='approved'

---

### 2. 生成用例测试

**测试：**
- 生成用例（选择Kimi，生成数量=5）
- 查看数据库：status应该全部为'pending'

**验证：**
- ✅ 生成的用例status='pending'
- ✅ 不出现在"已通过"列表
- ✅ 出现在"待审用例"列表

---

### 3. 待审列表测试

**测试：**
- 打开CaseList页面
- 默认显示"待审用例"Tab
- 显示所有status='pending'的用例

**验证：**
- ✅ Tab切换正常
- ✅ 待审用例数量显示正确
- ✅ 列表数据正确

---

### 4. 审批弹窗测试

**测试：**
- 点击任意待审用例的"审批"按钮
- 弹窗打开，显示用例详情
- 检查字段是否可编辑

**验证：**
- ✅ 弹窗正常打开
- ✅ 显示完整用例详情
- ✅ 所有字段可编辑
- ✅ 步骤可添加/删除

---

### 5. Pass流程测试

**测试：**
- 审批弹窗打开
- 修改部分字段（如标题）
- 点击Pass按钮
- 自动跳转下一个用例

**验证：**
- ✅ Pass成功提示
- ✅ 用例status变为'approved'
- ✅ 从待审列表移除
- ✅ 自动加载下一个待审用例
- ✅ 进度显示正确（如: 2/5）

---

### 6. Failed流程测试

**测试：**
- 审批弹窗打开
- 点击Failed按钮
- 自动跳转下一个用例

**验证：**
- ✅ Failed成功提示
- ✅ 用例status变为'failed'
- ✅ 仍保留在待审列表
- ✅ 可以再次审批

---

### 7. 审批完成测试

**测试：**
- 审批所有待审用例（最后一个）
- 点击Pass/Failed
- 弹窗自动关闭

**验证：**
- ✅ 审批完成提示
- ✅ 弹窗自动关闭
- ✅ 待审列表刷新（数量变为0或只剩failed）

---

### 8. 已通过列表测试

**测试：**
- 切换到"已通过"Tab
- 查看已通过用例列表

**验证：**
- ✅ 只显示status='approved'的用例
- ✅ Pass的用例正确显示
- ✅ 功能正常（批量删除、导出等）

---

### 9. 再次生成用例测试

**测试：**
- 已有审批过的用例
- 再次生成新用例
- 查看待审列表

**验证：**
- ✅ 新生成的用例status='pending'
- ✅ 自动添加到待审列表
- ✅ 不影响已通过列表

---

### 10. 审批修改测试

**测试：**
- 审批弹窗打开
- 修改用例内容（标题、步骤、预期）
- 点击Pass
- 查看保存的内容

**验证：**
- ✅ 修改内容成功保存
- ✅ 数据库数据正确
- ✅ 步骤格式正确（"步骤：..."）
- ✅ 预期格式正确（"预期：..."）

---

## 可能的技术难点

### 1. 数据库迁移风险

**风险：**
- SQLite不支持某些ALTER TABLE操作
- 可能需要重建表

**解决方案：**
- 先备份app.db
- 如果ALTER失败，创建新表迁移数据

---

### 2. Tabs样式问题

**可能问题：**
- Tabs和header-actions布局冲突

**解决方案：**
- 使用flex布局
- Tabs占左侧，按钮占右侧

---

### 3. 步骤编辑组件复杂度

**复杂点：**
- steps字段是JSON字符串
- 需要解析、编辑、重新序列化

**解决方案：**
- 使用数组(stepsList)临时存储
- Pass时重新JSON序列化

---

### 4. 审批进度维护

**难点：**
- Pass/Failed后如何正确跳转下一个
- 如何维护索引

**解决方案：**
- 使用currentReviewIndex记录当前索引
- Pass/Failed后索引+1
- >=totalPendingCount时关闭弹窗

---

## 后续优化建议

### 1. 批量审批功能

**建议：**
- 新增"批量Pass"按钮
- 选中多个用例后批量审批

---

### 2. 审批历史记录

**建议：**
- 新增ApprovalLog表
- 记录审批时间、操作人、修改内容

---

### 3. 审批统计

**建议：**
- 显示待审/已通过/待改进数量统计
- 每日审批进度图表

---

### 4. 用例模板功能

**建议：**
- 审批时可选择应用模板
- 快速填充常用步骤

---

### 5. 智能辅助审批

**建议：**
- 根据用例类型自动建议优先级
- 检测用例内容是否完整

---

## 总结

### 方案总结

**核心改动：**
- TestCase表新增status字段
- 生成用例默认status='pending'
- CaseList页面双Tab展示
- 审批弹窗支持Pass/Failed和编辑
- Failed用例保留待审列表可再次审批

**改动文件数量：**
- 后端：2个模型文件 + 1个迁移脚本 + 1个路由文件
- 前端：1个API文件 + 1个组件文件

**预计工作量：**
- 后端：30-40分钟
- 前端：60-90分钟
- 测试：20-30分钟
- 总计：2-2.5小时

**实施风险：**
- 低（主要新增功能，不影响现有功能）

---

等待用户确认后开始实施。