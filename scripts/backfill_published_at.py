import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import async_session_factory


async def backfill():
    """Set published_at = created_at for all donor posts where published_at is NULL."""
    print("🚀 Starting backfill of published_at...")

    async with async_session_factory() as session:
        # Count affected rows
        result = await session.execute(
            text("SELECT COUNT(*) FROM donor_posts WHERE published_at IS NULL")
        )
        count = result.scalar()
        print(f"📊 Found {count} posts with published_at = NULL")

        if count == 0:
            print("✅ Nothing to do.")
            return

        # Backfill: set published_at = created_at
        await session.execute(
            text("UPDATE donor_posts SET published_at = created_at WHERE published_at IS NULL")
        )
        await session.commit()
        print(f"✅ Updated {count} posts: published_at = created_at")

        # Verify
        result = await session.execute(
            text("SELECT COUNT(*) FROM donor_posts WHERE published_at IS NULL")
        )
        remaining = result.scalar()
        print(f"📊 Remaining NULL: {remaining}")

    print("✅ Backfill completed!")


if __name__ == "__main__":
    asyncio.run(backfill())
