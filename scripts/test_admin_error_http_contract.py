from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--password',default='Demo@2026SuperAdmin'); a=ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=15) as c:
        login=await c.post('/auth/login',json={'username':'super_admin_demo','password':a.password}); login.raise_for_status()
        token=login.json()['data']['access_token']; headers={'Authorization':f'Bearer {token}'}
        me=await c.get('/auth/me',headers=headers); me.raise_for_status(); current_id=me.json()['data']['id']; missing=str(uuid.uuid4())
        responses=[
            ('missing_roles',await c.put(f'/admin/users/{missing}/roles',headers=headers,json={'roles':['customer']}),'USER_NOT_FOUND',404),
            ('missing_status',await c.put(f'/admin/users/{missing}/status?status=disabled',headers=headers),'USER_NOT_FOUND',404),
            ('self_delete',await c.delete(f'/admin/users/{current_id}',headers=headers),'SELF_ACCOUNT_DELETE_FORBIDDEN',400),
        ]
        checks=[]
        for name,response,code,status in responses:
            detail=response.json().get('detail'); checks.append({'name':name,'status':response.status_code,'detail':detail,'pass':response.status_code==status and isinstance(detail,dict) and detail.get('code')==code})
        result={'checks':checks,'pass':all(item['pass'] for item in checks)}; print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['pass']: raise SystemExit(1)

if __name__=='__main__': asyncio.run(main())
