import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import PublicProductsView from './views/PublicProductsView.vue'
import PlaceholderView from './views/PlaceholderView.vue'
import OrdersView from './views/OrdersView.vue'
import StaffOrdersView from './views/StaffOrdersView.vue'
import StaffCustomersView from './views/StaffCustomersView.vue'
import EnterpriseVerificationView from './views/EnterpriseVerificationView.vue'
import RiskAssessmentView from './views/RiskAssessmentView.vue'
import AdvisorWorkspaceView from './views/AdvisorWorkspaceView.vue'
import CustomerBackofficeView from './views/CustomerBackofficeView.vue'
import CustomerRagView from './views/CustomerRagView.vue'
import EmployeeWorkspaceView from './views/EmployeeWorkspaceView.vue'
import AdminWorkspaceView from './views/AdminWorkspaceView.vue'
import BackofficeShellView from './views/BackofficeShellView.vue'
import './styles.css'
const placeholder=(path:string,title:string)=>({path,component:PlaceholderView,meta:{requiresAuth:true,title}})
const router=createRouter({history:createWebHistory(),routes:[{path:'/login',component:LoginView},{path:'/register',component:RegisterView},{path:'/',component:HomeView},{path:'/products',component:PublicProductsView},{path:'/customer-center',component:CustomerBackofficeView,meta:{requiresAuth:true,title:'客户个人中心'}},{path:'/backoffice',component:BackofficeShellView,meta:{requiresAuth:true,title:'管理后台'}},{path:'/admin-workspace',redirect:'/backoffice'},{path:'/employee-workspace',redirect:'/backoffice'},{path:'/customer-center/orders',redirect:'/customer-center'},{path:'/customer-center/enterprise-verification',component:EnterpriseVerificationView,meta:{requiresAuth:true,title:'企业客户认证'}},{path:'/customer-center/risk-assessment',component:RiskAssessmentView,meta:{requiresAuth:true,title:'风险测评'}},{path:'/customer-center/advisor',component:AdvisorWorkspaceView,meta:{requiresAuth:true,title:'投顾服务'}},{path:'/customer-center/service',component:CustomerRagView,meta:{requiresAuth:true,title:'智能客服'}},{path:'/employee-workspace/orders',component:StaffOrdersView,meta:{requiresAuth:true,title:'订单审核与执行'}},{path:'/employee-workspace/customers',component:StaffCustomersView,meta:{requiresAuth:true,title:'客户管理'}}]})
router.beforeEach((to)=>{const loggedIn=!!sessionStorage.getItem('access_token');if(to.meta.requiresAuth&&!loggedIn)return'/login'})
createApp(App).use(router).mount('#app')
