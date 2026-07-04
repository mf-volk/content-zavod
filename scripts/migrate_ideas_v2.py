
import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import engine

async def migrate():
    """Add columns for Idea Selection 2.0."""
    print("Starting migration...")
    
    async with engine.begin() as conn:
        # 1. Add idea_topic
        try:
            await conn.execute(text("ALTER TABLE managed_channels ADD COLUMN idea_topic VARCHAR(255) NULL"))
            print("Added idea_topic column.")
        except Exception as e:
            if "duplicate column" in str(e) or "no such column" not in str(e): # SQLite might say 'duplicate column'
                print(f"Skipping idea_topic (might exist): {e}")
            else:
                print(f"Error adding idea_topic: {e}")

        # 2. Add idea_source_type
        try:
            await conn.execute(text("ALTER TABLE managed_channels ADD COLUMN idea_source_type VARCHAR(50) DEFAULT 'recent'"))
            print("Added idea_source_type column.")
        except Exception as e:
            print(f"Skipping idea_source_type (might exist): {e}")

    print("Migration finished.")

if __name__ == "__main__":
    asyncio.run(migrate())
