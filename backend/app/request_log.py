from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RequestLog


async def log_request(db: AsyncSession, **fields) -> RequestLog:
    entry = RequestLog(**fields)
    db.add(entry)
    await db.commit()
    return entry
