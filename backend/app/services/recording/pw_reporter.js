// AutoCase 自带的 Playwright reporter。
//
// 为什么不直接用 `--reporter=json`：那份 JSON 的 `result.steps` 在很多项目里是空的
// （用例跑在 fixture 里、或用了 test.step 之外的封装），拿不到「每一步花了多久」。
// reporter API 是 Playwright 公开接口，`onTestEnd` 拿到的 `result.steps` 是完整的执行树，
// 而且这个文件由 AutoCase 提供、`--reporter=<绝对路径>` 指进来即可 ——
// 被测项目不需要装 allure、不需要改 playwright.config、不写任何文件进项目仓库。
//
// 输出：一行一条 JSON（JSONL），首行是 env，其后每条用例一行。
// 同时往 stdout 打 `##AC##` 前缀的轻量进度行，后端据此更新「已执行 N / M」。

const fs = require('fs')

const OUT = process.env.AUTOCASE_PW_OUT || 'autocase-results.jsonl'
const MAX_STEPS = 200
const MAX_STEP_TITLE = 240
const MAX_ERROR = 1500

function cut(text, limit) {
  const value = String(text == null ? '' : text)
  return value.length > limit ? value.slice(0, limit) : value
}

function safeProjectName(test) {
  try {
    const project = test.parent && test.parent.project ? test.parent.project() : null
    return (project && project.name) || ''
  } catch (error) {
    return ''
  }
}

// 拍平步骤树。depth 保留层级，前端按缩进画；超过 MAX_STEPS 就截断，
// 防止某个死循环用例把结果文件撑到几十兆。
function flattenSteps(steps) {
  const rows = []
  const walk = (list, depth) => {
    for (const step of list || []) {
      if (rows.length >= MAX_STEPS) return
      rows.push({
        title: cut(step.title, MAX_STEP_TITLE),
        category: step.category || '',
        duration_ms: Math.round(step.duration || 0),
        status: step.error ? 'failed' : 'passed',
        error: step.error ? cut(step.error.message, 600) : '',
        depth: depth
      })
      walk(step.steps, depth + 1)
    }
  }
  walk(steps, 0)
  return rows
}

function collectAttachments(result, outDir) {
  const rows = []
  for (const item of result.attachments || []) {
    if (!item.path) continue
    rows.push({
      name: item.name || '',
      path: item.path,
      type: item.contentType || '',
      // 只把落在本次 --output 目录里的算作可展示产物，别把项目里的任意路径带出去
      in_output: outDir ? String(item.path).indexOf(outDir) === 0 || String(item.path).indexOf(outDir.replace(/\\/g, '/')) === 0 : false
    })
  }
  return rows
}

class AutoCaseReporter {
  constructor(options) {
    this.out = (options && options.out) || OUT
    this.outDir = (options && options.outputDir) || ''
    this.ready = false
  }

  onBegin(config, suite) {
    try {
      fs.writeFileSync(this.out, '')
      const env = {
        type: 'env',
        framework: 'playwright',
        tool: 'Playwright',
        version: config.version || '',
        runtime: 'Node ' + process.version,
        workers: config.workers || 0,
        projects: (config.projects || []).map(p => p.name).filter(Boolean),
        root_dir: config.rootDir || ''
      }
      fs.appendFileSync(this.out, JSON.stringify(env) + '\n')
      this.ready = true
    } catch (error) {
      process.stdout.write('##AC-ERR##' + String(error && error.message) + '\n')
    }
  }

  onTestEnd(test, result) {
    const row = {
      type: 'test',
      title: test.title || '',
      file: test.location ? test.location.file : '',
      line: test.location ? test.location.line : 0,
      project: safeProjectName(test),
      status: result.status || '',
      duration_ms: Math.round(result.duration || 0),
      start_time: result.startTime ? new Date(result.startTime).toISOString() : '',
      retry: result.retry || 0,
      worker: result.workerIndex,
      tags: test.tags || [],
      annotations: (test.annotations || []).map(a => ({ type: a.type || '', description: a.description || '' })),
      error: result.error ? cut(result.error.message, MAX_ERROR) : '',
      error_location: result.error && result.error.location ? result.error.location : null,
      attachments: collectAttachments(result, this.outDir),
      steps: flattenSteps(result.steps)
    }
    if (this.ready) {
      try {
        fs.appendFileSync(this.out, JSON.stringify(row) + '\n')
      } catch (error) {
        process.stdout.write('##AC-ERR##' + String(error && error.message) + '\n')
      }
    }
    // 进度行：不带 steps，避免 stdout 里的长行拖慢读取
    process.stdout.write('##AC##' + JSON.stringify({
      title: row.title,
      status: row.status,
      duration_ms: row.duration_ms
    }) + '\n')
  }
}

module.exports = AutoCaseReporter
