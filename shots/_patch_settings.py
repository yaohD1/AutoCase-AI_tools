import io

p = r'D:/autoproject/Auto_case/frontend/src/views/Settings.vue'
s = io.open(p, encoding='utf-8').read()
orig = s

# 1) 面板头去掉 h2
s = s.replace(
"""
            <div>
              <h2>模型服务</h2>
              <p class="panel-desc">所有 AI 调用都走这里的配置；标了 Vision 的才能用于原型图分析。</p>
            </div>""",
"""
            <p class="panel-desc">所有 AI 调用都走这里的配置。标了 Vision 的才能用于原型图分析。</p>""")

s = s.replace(
"""
            <div>
              <h2>项目与迭代</h2>
              <p class="panel-desc">展开项目可以管理它的迭代。用例挂在迭代下，删项目会连同用例一起删。</p>
            </div>""",
"""
            <p class="panel-desc">展开项目可以管理它的迭代。用例挂在迭代下，删项目会连同用例一起删。</p>""")

# 2) Key 改成纯展示
s = s.replace(
"""
                  <div class="cell-stack">
                    <span class="cell-main">{{ row.provider }}</span>
                    <!-- Key 默认打码，点一下才展开：整列明文摆着的风险远大于省的那一次点击 -->
                    <button type="button" class="key-line" :title="revealedKeys.has(row.id) ? '点击隐藏' : '点击显示完整 Key'" @click="toggleReveal(row)">
                      {{ revealedKeys.has(row.id) ? row.api_key : maskKey(row.api_key) }}
                    </button>
                  </div>""",
"""
                  <div class="cell-stack">
                    <span class="cell-main">{{ row.provider }}</span>
                    <!-- 后端只返回「前 6 + 后 4」，这里直接显示。
                         要换 Key 只能进编辑弹窗重填，列表不提供展开入口。 -->
                    <span class="key-line">{{ displayKey(row.api_key) }}</span>
                  </div>""")

# 3) 列宽与状态样式
s = s.replace('''<el-table-column label="服务商" min-width="210">''',
              '''<el-table-column label="服务商" min-width="190">''')

old_cols = '''              <el-table-column label="模型" min-width="170">
                <template #default="{ row }">
                  <span class="mono cell-model">{{ row.model || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="温度" width="78">
                <template #default="{ row }">
                  <span class="mono num">{{ row.temperature ?? '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="Token" width="88">
                <template #default="{ row }">
                  <span class="mono num">{{ row.max_tokens ?? '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="能力" width="96">
                <template #default="{ row }">
                  <span :class="['chip', row.supports_vision !== false ? 'is-accent' : 'is-mute']">
                    {{ row.supports_vision !== false ? 'Vision' : '纯文本' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="78">
                <template #default="{ row }">
                  <span :class="['chip', row.is_active ? 'is-ok' : 'is-mute']">
                    {{ row.is_active ? '启用' : '停用' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="156" align="right">'''

new_cols = '''              <el-table-column label="模型" min-width="250">
                <template #default="{ row }">
                  <span class="mono cell-model">{{ row.model || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="采样" width="112">
                <template #default="{ row }">
                  <span class="mono num">{{ row.temperature ?? '—' }} · {{ row.max_tokens ?? '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="能力" width="88">
                <template #default="{ row }">
                  <span :class="['cap', { 'is-vision': row.supports_vision !== false }]">
                    {{ row.supports_vision !== false ? 'Vision' : '纯文本' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="84">
                <template #default="{ row }">
                  <span :class="['cap', row.is_active ? 'is-on' : 'is-off']">
                    {{ row.is_active ? '启用' : '停用' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" align="right">'''

assert old_cols in s
s = s.replace(old_cols, new_cols)

# 4) script：去掉二次打码
s = s.replace('''// 哪些行的 Key 被临时展开：默认都打码，点一下才看
const revealedKeys = ref(new Set())
''', '')

old_fn = '''/* ---------- Key 打码 ---------- */
function maskKey(key) {
  if (!key) return '未设置'
  const k = String(key)
  if (k.length <= 12) return '\u2022'.repeat(k.length)
  return `${k.slice(0, 4)}${'\u2022'.repeat(8)}${k.slice(-4)}`
}

function toggleReveal(row) {
  const set = revealedKeys.value
  if (set.has(row.id)) set.delete(row.id)
  else set.add(row.id)
}
'''

new_fn = '''/* ---------- Key 展示 ----------
   后端 AIConfig.to_dict() 已经只返回「前 6 + 后 4」。
   这里只做兜底：万一拿到的是未脱敏的完整 key（比如老接口），也别整段摆出来。 */
function displayKey(key) {
  if (!key) return '未设置'
  const k = String(key)
  if (k.includes('\u2022\u2022') || k.includes('***')) return k
  if (k.length <= 12) return '\u2022'.repeat(k.length)
  return `${k.slice(0, 6)}\u2022\u2022\u2022\u2022${k.slice(-4)}`
}
'''

assert old_fn in s, 'old_fn not found'
s = s.replace(old_fn, new_fn)

assert s != orig
io.open(p, 'w', encoding='utf-8').write(s)
print('ok')
