import { http } from './http'
export const adminApi={users:(q='')=>http.get('/admin/users',{params:{q}}),roles:()=>http.get('/admin/roles'),permissions:()=>http.get('/admin/permissions'),updateRoles:(id:string,roles:string[])=>http.put(`/admin/users/${id}/roles`,{roles}),updateStatus:(id:string,status:'active'|'disabled')=>http.put(`/admin/users/${id}/status`,null,{params:{status}})}
