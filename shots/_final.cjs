const PW = 'C:/Users/11031/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright'
const CHROME = 'C:/Users/11031/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'
const { chromium } = require(PW)
const APP = 'http://localhost:5173'
const sleep = ms => new Promise(r => setTimeout(r, ms))
;(async () => {
  const b = await chromium.launch({ executablePath: CHROME })
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } })
  const p = await ctx.newPage()
  const bad = []
  p.on('pageerror', e => bad.push('pageerror: ' + e.message))
  p.on('console', m => { if (m.type() === 'error') bad.push('console: ' + m.text().slice(0, 140)) })
  p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()) })

  await p.goto(APP, { waitUntil: 'networkidle' })
  await sleep(1200)
  await p.evaluate(() => {
    let t = null
    const walk = (v, d) => {
      if (!v || d > 25 || t) return
      if (v.component && v.component.type && v.component.type.__name === 'Upload') { t = v.component; return }
      if (Array.isArray(v.children)) v.children.forEach(c => walk(c, d + 1))
      if (v.component) walk(v.component.subTree, d + 1)
    }
    walk(document.getElementById('app').__vue_app__._instance.subTree, 0)
    t.setupState.showCreateProject = true
  })
  await sleep(800)
  await p.screenshot({ path: 'shots/dlg-create-project.png' })
  await p.keyboard.press('Escape'); await sleep(500)

  for (const path of ['/cases', '/modules', '/scripts', '/scripts/records', '/settings', '/recording', '/recording/records']) {
    await p.goto(APP + path, { waitUntil: 'networkidle' })
    await sleep(1000)
  }
  console.log('bad:', JSON.stringify([...new Set(bad)], null, 1))
  await b.close()
})()
