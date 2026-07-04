"""Content publication service."""

from __future__ import annotations

import logging
from typing import Optional, Union

from aiogram import Bot
from aiogram.types import InputMediaPhoto, Message

from app.db.models import Draft

logger = logging.getLogger(__name__)


async def publish_content(
    bot: Bot,
    chat_id: int,
    draft: Draft,
) -> Optional[Message]:
    """
    Publish content to a Telegram chat with HTML fix fallback.
    """
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.enums import ParseMode
    from app.utils import sanitize_html

    # Sanitize content first
    safe_content = sanitize_html(draft.content)

    # Apply default post text from channel settings (inserted exactly as user wrote it)
    channel = getattr(draft, 'managed_channel', None)
    if channel and channel.default_post_text:
        default_text = channel.default_post_text
        if channel.default_post_text_position == "start":
            safe_content = default_text + "\n\n" + safe_content
        else:
            safe_content = safe_content + "\n\n" + default_text

    async def _send_safe(caption_or_text, **kwargs):
        """Helper to try sending with HTML, then fallback to plain text."""
        try:
            return await bot.send_message(
                text=caption_or_text,
                parse_mode=ParseMode.HTML,
                **kwargs
            )
        except TelegramBadRequest as e:
            if "can't parse entities" in str(e) or "tag" in str(e):
                logger.warning(f"HTML parse error in draft {draft.id}, falling back to plain text. Error: {e}")
                # Fallback: Send plain text
                return await bot.send_message(
                    text=caption_or_text,
                    parse_mode=None,
                    **kwargs
                )
            raise e

    async def _send_photo_safe(photo_id, caption, **kwargs):
        """Helper for photos."""
        try:
            return await bot.send_photo(
                photo=photo_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                **kwargs
            )
        except TelegramBadRequest as e:
            if "can't parse entities" in str(e) or "tag" in str(e):
                logger.warning(f"HTML parse error in photo caption {draft.id}, falling back to plain text.")
                return await bot.send_photo(
                    photo=photo_id,
                    caption=caption,
                    parse_mode=None,
                    **kwargs
                )
            raise e

    try:
        # Diagnostic: Check admin rights and anonymity
        try:
            me = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
            logger.info(f"Bot status in chat {chat_id}: {me.status}, Anonymous: {getattr(me, 'is_anonymous', 'Unknown')}")
        except Exception as e:
            logger.warning(f"Could not check bot status in {chat_id}: {e}")

        if draft.media:
            # Check caption length limit
            is_caption_too_long = len(safe_content) > 1000
            
            # Helper to send media
            async def _send_media(caption: Optional[str] = None):
                if len(draft.media) == 1:
                    photo_id = draft.media[0].file_id
                    if caption:
                        await _send_photo_safe(photo_id, caption, chat_id=chat_id)
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=photo_id)
                else:
                    # Album
                    # Prepare media group
                    # For bottom position or separate text, we don't attach caption to album
                    # For top position w/ caption, we attach to first item
                    
                    media_group = [
                        InputMediaPhoto(
                            media=m.file_id,
                            caption=(caption if i == 0 else None),
                            parse_mode=(ParseMode.HTML if i == 0 and caption else None),
                        )
                        for i, m in enumerate(sorted(draft.media, key=lambda x: x.position))
                    ]
                    
                    # Safe send album
                    try:
                        await bot.send_media_group(chat_id=chat_id, media=media_group)
                    except TelegramBadRequest as e:
                        if "can't parse entities" in str(e) and caption:
                            logger.warning("Falling back to plain text for album.")
                            # Re-build with plain text parse mode
                            media_group = [
                                InputMediaPhoto(
                                    media=m.file_id,
                                    caption=(caption if i == 0 else None),
                                    parse_mode=None,
                                )
                                for i, m in enumerate(sorted(draft.media, key=lambda x: x.position))
                            ]
                            await bot.send_media_group(chat_id=chat_id, media=media_group)
                        else:
                            raise e

            # Logic based on position
            logger.info(f"Publishing Draft {draft.id}. Position: {draft.media_position}, Content Len: {len(safe_content)}")

            # Три режима публикации:
            # - 'top' = фото с caption (если текст короткий) ИЛИ фото + отдельный текст (если длинный)
            # - 'text_top' = текст с link preview фото снизу (используем Telegraph)
            # - 'bottom' = отдельный текст + отдельное фото

            if draft.media_position == 'text_top':
                # TEXT_TOP: Upload photo to hosting, send text with link preview below
                logger.info("Path: Text Top (Text with photo link preview below)")

                from app.services.telegraph_uploader import upload_photo_file_id
                from aiogram.types import LinkPreviewOptions

                photo_file_id = draft.media[0].file_id if draft.media else None

                if photo_file_id:
                    image_url = await upload_photo_file_id(bot, photo_file_id)

                    if image_url:
                        # Hide URL under invisible character to keep text clean
                        hidden_link = f"<a href='{image_url}'>\u200b</a>"
                        text_with_link = f"{hidden_link}{safe_content}"

                        return await bot.send_message(
                            chat_id=chat_id,
                            text=text_with_link,
                            parse_mode=ParseMode.HTML,
                            link_preview_options=LinkPreviewOptions(
                                url=image_url,
                                show_above_text=False,
                                prefer_large_media=True,
                            )
                        )
                    else:
                        logger.warning("Image upload failed, falling back to top mode")
                        if is_caption_too_long:
                            await _send_media(caption=None)
                            await _send_safe(caption_or_text=safe_content, chat_id=chat_id)
                        else:
                            await _send_media(caption=safe_content)
                else:
                    logger.error("No photo found for text_top mode")
                    await _send_safe(caption_or_text=safe_content, chat_id=chat_id)

            elif draft.media_position == 'bottom':
                # User wants text BEFORE photo (two separate messages)
                logger.info("Path: Bottom (Text message, then Photo message)")
                await _send_safe(caption_or_text=safe_content, chat_id=chat_id)
                await _send_media(caption=None)
            else:
                # Top position - try to combine if possible
                if is_caption_too_long:
                    # Text too long for caption - split into two messages
                    logger.info("Path: Top + Long Text (Photo message, then Text message)")
                    await _send_media(caption=None)
                    await _send_safe(caption_or_text=safe_content, chat_id=chat_id)
                else:
                    # Perfect! Photo with caption (single message)
                    logger.info("Path: Top + Short Text (Combined: Photo with caption)")
                    await _send_media(caption=safe_content)
                    
        else:
            # Text only
            return await _send_safe(caption_or_text=safe_content, chat_id=chat_id)
            
    except Exception as e:
        logger.error(f"Error in publish_content: {e}")
        raise e
