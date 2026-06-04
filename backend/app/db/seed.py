"""Insert initial supermarkets. Run once after applying migrations:
    docker compose exec backend python -m app.db.seed
"""
import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.supermarket import Supermarket

SUPERMARKETS = [
    {"slug": "tienda_inglesa", "name": "Tienda Inglesa", "base_url": "https://www.tinglesa.com.uy"},
    {"slug": "disco", "name": "Disco", "base_url": "https://www.disco.com.uy"},
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        for data in SUPERMARKETS:
            existing = await session.scalar(
                select(Supermarket).where(Supermarket.slug == data["slug"])
            )
            if existing:
                print(f"  skip {data['slug']} (already exists)")
                continue
            session.add(Supermarket(**data))
            print(f"  added {data['slug']}")
        await session.commit()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(seed())
