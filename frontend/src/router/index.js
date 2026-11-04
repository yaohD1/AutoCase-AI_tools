import { createRouter, createWebHistory } from 'vue-router'
import Upload from '../views/Upload.vue'
import CaseList from '../views/CaseList.vue'
import ModuleList from '../views/ModuleList.vue'
import Settings from '../views/Settings.vue'
import Scripts from '../views/Scripts.vue'
import AutomationGenerationDetail from '../views/AutomationGenerationDetail.vue'
import AutomationRecords from '../views/AutomationRecords.vue'
import Recording from '../views/Recording.vue'
import RecordingRecords from '../views/RecordingRecords.vue'

const routes = [
  {
    path: '/',
    name: 'Upload',
    component: Upload
  },
  {
    path: '/cases',
    name: 'CaseList',
    component: CaseList
  },
  {
    path: '/modules',
    name: 'ModuleList',
    component: ModuleList
  },
  {
    path: '/settings',
    name: 'Settings',
    component: Settings
  },
  {
    path: '/scripts',
    name: 'Scripts',
    component: Scripts
  },
  {
    path: '/scripts/records',
    name: 'AutomationRecords',
    component: AutomationRecords
  },
  {
    path: '/scripts/generations/:id',
    name: 'AutomationGenerationDetail',
    component: AutomationGenerationDetail
  },
  {
    path: '/recording',
    name: 'Recording',
    component: Recording
  },
  {
    path: '/recording/records',
    name: 'RecordingRecords',
    component: RecordingRecords
  },
  // 未知路径回首页。没有这一条时，地址打错只留下一条顶栏和空白内容区，
  // 看起来像整站挂了；改成跳首页，至少还能继续用。
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 换页回到顶部：内容区比视口长的时候，从长列表跳到短页面会停在半空，
  // 新页面淡入后看到的第一屏是空白。前进/后退仍由浏览器恢复原位置。
  scrollBehavior(to, from, savedPosition) {
    return savedPosition || { top: 0 }
  }
})

export default router
