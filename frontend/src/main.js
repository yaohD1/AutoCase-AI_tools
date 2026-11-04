import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
// 暗色只引 Element 自己的变量表（html.dark 下覆盖 --el-*），
// 组件外观仍然由后面那四个样式文件决定
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/buttons.css'
import './styles/list-page.css'
import './styles/forms.css'
import './styles/dialog.css'
import './styles/motion.css'
import { liftBoot } from './composables/useBoot'

const app = createApp(App)

app.use(createPinia())
app.use(router)
// 之前没传 locale，分页和空态全是英文（Total 288 / 10/page / Go to）
app.use(ElementPlus, { locale: zhCn })

// 等首个路由解析完再挂载。直接 mount 的话，App 先渲染出一个空的 router-view，
// 路由解析完再把页面塞进去 —— 这算一次「组件替换」，
// 首屏会白拿一次 180ms 的路由淡入，和首页自己的阶梯入场叠在一起。
// 幕布必须在页面真的就绪之后才揭开：提前撤幕，露出来的可能是空 router-view。
// liftBoot 内部会补足「至少停 HOLD 毫秒」，所以这里直接调就行，不用自己计时。
router.isReady().then(() => {
  app.mount('#app')
  liftBoot()
})