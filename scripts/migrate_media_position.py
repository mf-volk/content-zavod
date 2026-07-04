import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import async_session_factory

async def migrate():
    """Add media_position column to drafts table."""
    print("🚀 Starting migration...")
    
    async with async_session_factory() as session:
        print("✅ Database connection established.")
        
        # Check if column exists
        try:
            await session.execute(text("SELECT media_position FROM drafts LIMIT 1"))
            print("⚠️ Column 'media_position' already exists in 'drafts'.")
        except Exception:
            print("➕ Adding 'media_position' column to 'drafts'...")
            await session.execute(text("ALTER TABLE drafts ADD COLUMN media_position VARCHAR(20) DEFAULT 'top'"))
            await session.commit()
            print("✅ Column added successfully!")

    print("✅ Migration completed successfully!")

if __name__ == "__main__":
    asyncio.run(migrate())
