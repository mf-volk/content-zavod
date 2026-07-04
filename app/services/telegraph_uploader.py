"""Image uploader for link previews in Telegram.

Tries multiple free hosting services in order:
1. Telegraph (telegra.ph/upload)
2. 0x0.st
"""

import logging
from typing import Optional
import aiohttp
from aiogram import Bot

logger = logging.getLogger(__name__)


async def _try_telegraph(photo_bytes: bytes) -> Optional[str]:
    """Upload to Telegraph."""
    try:
        form = aiohttp.FormData()
        form.add_field('file', photo_bytes, filename='photo.jpg', content_type='image/jpeg')

        async with aiohttp.ClientSession() as session:
            async with session.post("https://telegra.ph/upload", data=form, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result and isinstance(result, list) and len(result) > 0:
                        src = result[0].get('src')
                        if src:
                            url = f"https://telegra.ph{src}"
                            logger.info(f"Telegraph upload OK: {url}")
                            return url
                logger.warning(f"Telegraph upload failed: HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Telegraph upload error: {e}")
    return None


async def _try_0x0(photo_bytes: bytes) -> Optional[str]:
    """Upload to 0x0.st."""
    try:
        form = aiohttp.FormData()
        form.add_field('file', photo_bytes, filename='photo.jpg', content_type='image/jpeg')

        async with aiohttp.ClientSession() as session:
            async with session.post("https://0x0.st", data=form, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    url = (await resp.text()).strip()
                    if url.startswith("http"):
                        logger.info(f"0x0.st upload OK: {url}")
                        return url
                logger.warning(f"0x0.st upload failed: HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"0x0.st upload error: {e}")
    return None


async def upload_photo_bytes(photo_bytes: bytes) -> Optional[str]:
    """Upload photo bytes to any available hosting. Returns public URL."""
    # Try services in order
    for uploader in [_try_telegraph, _try_0x0]:
        url = await uploader(photo_bytes)
        if url:
            return url

    logger.error("All image hosting services failed")
    return None


async def upload_photo_file_id(bot: Bot, file_id: str) -> Optional[str]:
    """Download photo from Telegram by file_id and upload to hosting."""
    try:
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        return await upload_photo_bytes(file_bytes.read())
    except Exception as e:
        logger.error(f"Failed to download file from Telegram: {e}")
        return None
