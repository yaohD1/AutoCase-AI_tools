# Kimi模型ID问题解决总结

## 问题诊断

### 错误信息
```
Failed to generate test cases: Kimi API error: API request failed: 400 - 
{"error":"request error :model account not found: kimi-2.6"}
```

### 根本原因
数据库中Kimi配置的模型名称错误：
- **数据库中：** `kimi-2.6`（错误）
- **应该是：** `kimi-k2.6`（正确）

---

## 问题场景

**为什么会发生这个错误？**

用户在前端Settings页面添加Kimi配置时，手动填写了模型名称为 `kimi-2.6`，但根据用户反馈和API文档，正确的模型ID应该是 `kimi-k2.6`。

---

## 已执行的解决方案

### 方案：直接更新数据库

**执行步骤：**
1. 创建诊断脚本 `backend/check_db_config.py` 检查数据库配置
2. 发现模型名称不匹配：`kimi-2.6` vs `kimi-k2.6`
3. 创建修复脚本 `backend/fix_kimi_model_name.py` 更新数据库
4. 执行修复脚本，将模型名称从 `kimi-2.6` 改为 `kimi-k2.6`
5. 验证修复成功

**修复结果：**
✅ 数据库中Kimi配置的模型名称已更新为 `kimi-k2.6`
✅ Kimi配置状态为 Active（启用）
✅ API地址正确配置

---

## 当前配置状态

**数据库中的AI配置：**

```
Provider: kimi
Model: kimi-k2.6
API Base: https://api.yygu.cn/v3/llm.chat/chat/completions
Is Active: True
```

✅ 所有配置参数正确，可以正常使用

---

## 用户操作指引

### 现在可以做什么

1. **重启后端服务**（如果正在运行）
   ```bash
   cd backend
   python run.py
   ```

2. **测试生成用例功能**
   - 打开前端页面 http://localhost:5173
   - 在Upload页面：
     - 创建项目
     - 上传UI设计图
     - 选择Kimi服务
     - 点击"生成测试用例"
   - 应该能成功调用Kimi API并生成用例

3. **如果之前已测试过Vision功能**
   - Vision功能已验证支持（状态码200）
   - 可以上传图片自动识别生成用例

---

## 预防措施

### 如何避免未来的模型名称错误

**方案1：前端改进**（推荐实施）

在Settings页面添加模型名称提示或验证：

```vue
<el-form-item label="模型名称">
    <el-input v-model="configForm.model" placeholder="可选，默认模型">
        <template #suffix>
            <el-tooltip content="DeepSeek默认: deepseek-v4-flash, Kimi默认: kimi-k2.6">
                <el-icon><InfoFilled /></el-icon>
            </el-tooltip>
        </template>
    </el-input>
</el-form-item>
```

或在添加配置时提供下拉选项：

```vue
<el-form-item label="模型名称">
    <el-select v-model="configForm.model" placeholder="选择模型">
        <el-option v-if="configForm.provider === 'deepseek'" 
                   label="deepseek-v4-flash" value="deepseek-v4-flash" />
        <el-option v-if="configForm.provider === 'kimi'" 
                   label="kimi-k2.6" value="kimi-k2.6" />
    </el-select>
</el-form-item>
```

**方案2：后端验证**

在创建/更新AI配置时验证模型名称：

```python
# backend/app/routes/config.py
def create_ai_config():
    provider = data.get('provider')
    model = data.get('model')
    
    # 如果没有提供model，使用默认值
    if not model:
        default_models = {
            'deepseek': 'deepseek-v4-flash',
            'kimi': 'kimi-k2.6'
        }
        model = default_models.get(provider)
    
    # 验证模型名称是否合法
    valid_models = {
        'deepseek': ['deepseek-v4-flash', 'deepseek-v4-pro'],
        'kimi': ['kimi-k2.6']
    }
    
    if provider in valid_models and model not in valid_models[provider]:
        return jsonify({
            'error': f'Invalid model for {provider}. Valid models: {valid_models[provider]}'
        }), 400
    
    # 继续创建配置...
```

---

## 测试建议

### 完整流程测试

**测试步骤：**

1. ✅ **后端服务启动**
   ```bash
   cd backend
   python run.py
   ```

2. ✅ **前端服务启动**
   ```bash
   cd frontend
   npm run dev
   ```

3. ✅ **访问应用**
   - 打开浏览器：http://localhost:5173

4. ✅ **检查Settings页面**
   - 应该看到Kimi配置（模型：kimi-k2.6）

5. ✅ **测试完整流程**
   - Upload页面 → 创建项目 → 上传图片 → 选择Kimi → 生成用例
   - 应该成功生成并能看到测试用例列表

6. ✅ **检查CaseList页面**
   - 查看生成的测试用例
   - 应该显示模块、测试点、用例标题、步骤等信息

7. ✅ **测试导出功能**
   - 点击"导出XMind"
   - 应该能下载.xmind文件

---

## 相关文件

**诊断和修复脚本：**
- `backend/check_db_config.py` - 查看数据库AI配置
- `backend/fix_kimi_model_name.py` - 修复模型名称
- `backend/test_kimi_vision.py` - 测试Vision功能支持

**核心代码文件：**
- `backend/app/adapters/kimi_adapter.py` - Kimi适配器（默认模型：kimi-k2.6）
- `backend/app/services/case_generator.py` - 用例生成器（默认模型：kimi-k2.6）
- `backend/app/routes/config.py` - AI配置管理路由

---

## 总结

### 问题已解决 ✅

**问题：**Kim模型ID错误导致API调用失败
**原因：**数据库配置中的模型名称为 `kimi-2.6`（错误）
**解决：**更新数据库为正确的 `kimi-k2.6`
**结果：**现在可以正常使用Kimi生成测试用例

### 系统状态

- ✅ KimiAdapter已配置正确（默认模型：kimi-k2.6）
- ✅ 数据库配置已修复（模型：kimi-k2.6，Active：True）
- ✅ Vision功能已验证支持
- ✅ 可以立即投入使用

---

## 下一步

**立即可做：**
- 重启服务（如果需要）
- 测试生成用例功能
- 享受自动化测试用例生成！

**长期改进：**
- 在前端Settings页面添加模型名称提示
- 后端添加模型名称验证
- 提供模型下拉选择而非手动输入

---

**解决时间：** 2026-06-09
**解决方式：** 数据库直接更新
**测试建议：** 立即测试完整流程