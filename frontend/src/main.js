import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/buttons.css'
import './styles/list-page.css'
import './styles/forms.css'
import './styles/dialog.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
// 之前没传 locale，分页和空态全是英文（Total 288 / 10/page / Go to）
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')