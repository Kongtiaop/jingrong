from __future__ import annotations
import asyncio
import json
from uuid import uuid4
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def login(client):
    r=await client.post('/auth/login',json={'username':'super_admin_demo','password':'Demo@2026SuperAdmin'});r.raise_for_status();return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}

async def main():
    db=Database(get_settings()); source_id=None; child_id=None
    try:
        async with db.engine.begin() as c:
            source_id=str(uuid4());await c.execute(text("INSERT INTO aml_batch_run (id,batch_key,scan_window_start,scan_window_end,scan_days,status,run_type,started_at,finished_at) VALUES (:id,:key,now()-interval '7 days',now(),7,'completed','daily',now(),now())"),{'id':source_id,'key':f'late-source:{source_id}'})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1') as client:
            h=await login(client); first=await client.post(f'/risk-alerts/batches/{source_id}/late-rescan',headers=h);first.raise_for_status();data=first.json()['data'];child_id=data['batch_id'];assert data['created'] and data['run_type']=='late_rescan'
            second=await client.post(f'/risk-alerts/batches/{source_id}/late-rescan',headers=h);second.raise_for_status();assert second.json()['data']['created'] is False and second.json()['data']['batch_id']==child_id
            async with db.engine.connect() as c:
                row=(await c.execute(text('SELECT source_batch_id,run_type FROM aml_batch_run WHERE id=:id'),{'id':child_id})).one();assert row[0]==source_id and row[1]=='late_rescan'
            print(json.dumps({'first_created':True,'second_reused':True,'lineage_preserved':True,'pass':True}))
    finally:
        async with db.engine.begin() as c:
            if child_id: await c.execute(text('DELETE FROM aml_batch_run WHERE id=:id'),{'id':child_id})
            if source_id: await c.execute(text('DELETE FROM aml_batch_run WHERE id=:id'),{'id':source_id})
        await db.dispose()
if __name__=='__main__': asyncio.run(main())
