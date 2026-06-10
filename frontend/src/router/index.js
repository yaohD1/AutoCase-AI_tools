import { createRouter, createWebHistory } from 'vue-router'
import Upload from '../views/Upload.vue'
import CaseList from '../views/CaseList.vue'
import Settings from '../views/Settings.vue'

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
    path: '/settings',
    name: 'Settings',
    component: Settings
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router