import asyncio
from app.core.database import SessionLocal
from sqlalchemy import text

async def main():
    async with SessionLocal() as db:
        res = await db.execute(text('SELECT id, action, resource_type FROM audit_logs LIMIT 10;'))
        for r in res:
            print(r)

asyncio.run(main())
