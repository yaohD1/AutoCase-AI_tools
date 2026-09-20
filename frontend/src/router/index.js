import { createRouter, createWebHistory } from 'vue-router'
import Upload from '../views/Upload.vue'
import CaseList from '../views/CaseList.vue'
import ModuleList from '../views/ModuleList.vue'
import Settings from '../views/Settings.vue'
import Scripts from '../views/Scripts.vue'

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
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
