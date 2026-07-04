"""Image generation with a selectable provider: OpenAI or Kie.ai.

The provider is chosen via ``settings.ai_provider`` ("openai" | "kie").
OpenRouter is intentionally not supported.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Awaitable, Callable, Optional

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)

StatusCallback = Optional[Callable[[str], Awaitable[None]]]

# Map the app's WxH size to a Kie.ai aspect ratio.
_ASPECT_RATIOS = {
    "1024x1024": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
}


class ImageGenerator:
    """Provider-agnostic image generator (OpenAI or Kie.ai)."""

    def __init__(self) -> None:
        self.provider = settings.ai_provider
        self.size = settings.image_size

    async def generate_bytes(
        self,
        prompt: str,
        size: Optional[str] = None,
        status_callback: StatusCallback = None,
    ) -> Optional[bytes]:
        """Generate an image and return raw PNG bytes, or None on failure."""
        target_size = size or self.size

        if status_callback:
            try:
                await status_callback(
                    "🎨 <b>Генерирую изображение...</b>\n\n"
                    "⏱ Это занимает от 10 секунд до пары минут.\n"
                    "Подождите немного!"
                )
            except Exception:
                pass

        if self.provider == "kie":
            return await self._generate_kie(prompt, target_size)
        return await self._generate_openai(prompt, target_size)

    # ---------------- OpenAI backend ----------------
    async def _generate_openai(self, prompt: str, size: str) -> Optional[bytes]:
        if not settings.openai_api_key:
            logger.error("OPENAI_API_KEY is not configured")
            return None

        from openai import AsyncOpenAI

        model = settings.openai_image_model
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

        try:
            result = await client.images.generate(
                model=model,
                prompt=prompt,
                size=self._openai_size(model, size),
                n=1,
            )
        except Exception as e:
            logger.error(f"OpenAI image generation failed: {e}")
            return None

        try:
            item = result.data[0]
            b64 = getattr(item, "b64_json", None)
            if b64:
                return base64.b64decode(b64)
            url = getattr(item, "url", None)
            if url:
                return await self._download(url)
        except Exception as e:
            logger.error(f"Failed to parse OpenAI image response: {e}")
        logger.error("OpenAI image response contained no image data")
        return None

    @staticmethod
    def _openai_size(model: str, size: str) -> str:
        """Map a requested WxH to a size valid for the chosen OpenAI model."""
        if model.startswith("gpt-image"):
            return {
                "1024x1024": "1024x1024",
                "1792x1024": "1536x1024",
                "1024x1792": "1024x1536",
            }.get(size, "1024x1024")
        # dall-e-3 accepts the classic sizes directly
        if size in ("1024x1024", "1792x1024", "1024x1792"):
            return size
        return "1024x1024"

    # ---------------- Kie.ai backend ----------------
    async def _generate_kie(self, prompt: str, size: str) -> Optional[bytes]:
        if not settings.kie_api_key:
            logger.error("KIE_API_KEY is not configured")
            return None

        base = settings.kie_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.kie_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.kie_image_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": _ASPECT_RATIOS.get(size, "1:1"),
                "output_format": "png",
            },
        }

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=180)
            ) as http:
                # 1. Create the generation task
                async with http.post(
                    f"{base}/api/v1/jobs/createTask", json=payload, headers=headers
                ) as resp:
                    data = await resp.json()

                if data.get("code") != 200:
                    logger.error(f"Kie.ai createTask error: {data}")
                    return None
                task_id = (data.get("data") or {}).get("taskId")
                if not task_id:
                    logger.error(f"Kie.ai createTask returned no taskId: {data}")
                    return None

                # 2. Poll for the result (up to ~3 minutes)
                for _ in range(60):
                    await asyncio.sleep(3)
                    async with http.get(
                        f"{base}/api/v1/jobs/recordInfo",
                        params={"taskId": task_id},
                        headers=headers,
                    ) as resp:
                        info = await resp.json()

                    record = info.get("data") or {}
                    state = record.get("state")
                    if state == "success":
                        url = self._first_result_url(record.get("resultJson"))
                        if url:
                            return await self._download(url)
                        logger.error(f"Kie.ai success without result URL: {record}")
                        return None
                    if state == "fail":
                        logger.error(f"Kie.ai task failed: {record.get('failMsg')}")
                        return None

                logger.error("Kie.ai task timed out")
                return None
        except Exception as e:
            logger.error(f"Kie.ai image generation failed: {e}")
            return None

    @staticmethod
    def _first_result_url(result_json: Optional[str]) -> Optional[str]:
        if not result_json:
            return None
        try:
            parsed = json.loads(result_json)
            urls = parsed.get("resultUrls") or []
            return urls[0] if urls else None
        except Exception:
            return None

    # ---------------- shared ----------------
    @staticmethod
    async def _download(url: str) -> Optional[bytes]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as http:
                async with http.get(url) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.error(f"Failed to download image: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
        return None


# Global instance
image_generator = ImageGenerator()


async def generate_image_bytes(
    prompt: str,
    status_callback: StatusCallback = None,
) -> Optional[bytes]:
    """Convenience wrapper used by handlers."""
    return await image_generator.generate_bytes(prompt, status_callback=status_callback)
