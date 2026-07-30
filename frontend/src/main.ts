import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import HomeView from './views/HomeView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import CustomerHomeView from './views/CustomerHomeView.vue'
import StaffDashboardView from './views/StaffDashboardView.vue'
import PublicProductsView from './views/PublicProductsView.vue'
import PlaceholderView from './views/PlaceholderView.vue'
import OrdersView from './views/OrdersView.vue'
import StaffOrdersView from './views/StaffOrdersView.vue'
import StaffCustomersView from './views/StaffCustomersView.vue'
import './styles.css'
const placeholder=(path:string,title:string)=>({path,component:PlaceholderView,meta:{requiresAuth:true,title}})
const router=createRouter({history:createWebHistory(),routes:[{path:'/login',component:LoginView},{path:'/register',component:RegisterView},{path:'/',component:HomeView},{path:'/knowledge',component:KnowledgeView,meta:{requiresAuth:true}},{path:'/products',component:PublicProductsView},{path:'/customer',component:CustomerHomeView,meta:{requiresAuth:true}},{path:'/staff',component:StaffDashboardView,meta:{requiresAuth:true}},placeholder('/assets','我的资产'),{path:'/orders',component:OrdersView,meta:{requiresAuth:true,title:'我的订单'}},{path:'/staff/orders',component:StaffOrdersView,meta:{requiresAuth:true,title:'订单审核与执行'}},{path:'/staff/customers',component:StaffCustomersView,meta:{requiresAuth:true,title:'客户管理'}}]})
router.beforeEach((to)=>{const loggedIn=!!sessionStorage.getItem('access_token');if(to.meta.requiresAuth&&!loggedIn)return'/login';if((to.path==='/login'||to.path==='/register')&&loggedIn)return'/'})
createApp(App).use(router).mount('#app')
