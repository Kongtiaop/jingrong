import asyncio,json,aiomysql
async def get_version():
 c=await aiomysql.connect(host="127.0.0.1",port=3307,user="wealth",password="wealth_dev",db="wealth_manager",autocommit=True)
 try:
  async with c.cursor() as cur:
   await cur.execute("SELECT VERSION(), @@version_comment, @@sql_mode")
   row=await cur.fetchone()
  return {"version":row[0],"comment":row[1],"sql_mode":row[2]}
 finally:c.close()
async def main(): print(json.dumps(await get_version(),ensure_ascii=False,indent=2))
if __name__=="__main__": asyncio.run(main())
