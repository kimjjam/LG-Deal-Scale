import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Staff
from app.security import hash_password


async def main() -> None:
    settings = get_settings()
    if not settings.owner_name or not settings.owner_email or not settings.owner_password:
        raise RuntimeError("OWNER_NAME, OWNER_EMAIL and OWNER_PASSWORD are required")

    async with SessionLocal() as session:
        if await session.scalar(select(Staff.id).where(Staff.role == "owner")):
            raise RuntimeError("An owner account already exists")
        email = str(settings.owner_email).lower()
        if await session.scalar(select(Staff.id).where(Staff.email == email)):
            raise RuntimeError("A staff account with OWNER_EMAIL already exists")
        session.add(
            Staff(
                name=settings.owner_name.strip(),
                email=email,
                hashed_password=hash_password(settings.owner_password.get_secret_value()),
                role="owner",
            )
        )
        await session.commit()


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
