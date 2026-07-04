"""Media management handlers (AI generation and manual upload)."""

from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import Message, CallbackQuery, PhotoSize, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Draft, DraftMedia, MediaType
from app.llm_client import llm_client
from app.handlers.keyboards import (
    ai_image_result_keyboard,
    ai_image_size_keyboard,
    draft_edit_keyboard,
    ai_prompt_selection_keyboard,
    ai_prompt_suggestion_keyboard,
    back_to_menu_keyboard,
)
from app.handlers.states import DraftStates
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="media")


@router.callback_query(F.data.startswith("media:ai_gen:"))
async def ai_gen_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start AI image generation."""
    draft_id = int(callback.data.split(":")[-1])

    await state.set_state(DraftStates.waiting_for_ai_prompt)
    await state.update_data(draft_id=draft_id)



    await answer_nav(
        callback=callback,
        label="🎨 AI Фото",
        new_text=(
            "🎨 <b>AI Генерация изображения</b>\n\n"
            "Опиши желаемое изображение на английском или русском языке.\n\n"
            "<i>Примеры:</i>\n"
            "• Modern office workspace with laptop\n"
            "• Абстрактная композиция в синих тонах\n"
            "• Futuristic city at sunset"
        ),
        reply_markup=ai_prompt_selection_keyboard(draft_id),
    )


@router.callback_query(F.data.startswith("media:suggest_prompt:"))
async def suggest_prompt(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Suggest prompt using LLM."""
    draft_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.idea))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    # Logic for suggest:
    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🪄 Предложить промт")
    
    # 3. Progress
    progress_msg = await callback.message.answer(
        "🪄 <b>Генерирую промт для изображения...</b>\n"
        "Анализирую текст поста и идею.",
        parse_mode="HTML"
    )

    prompt = await llm_client.generate_image_prompt(
        text_content=draft.content,
        idea_context=draft.idea.description if draft.idea else None,
    )

    if not prompt:
        await progress_msg.delete()
        await callback.message.answer(
            "❌ Не удалось сгенерировать промт. Попробуй придумать сам.",
            reply_markup=ai_prompt_selection_keyboard(draft_id),
            parse_mode="HTML"
        )
        return

    # Show result with option to use it
    await progress_msg.delete()
    await callback.message.answer(
        f"🎨 <b>AI Генерация изображения</b>\n\n"
        f"🪄 <b>Предложенный промт:</b>\n"
        f"<code>{prompt}</code>\n\n"
        f"Скопируй этот промт и отправь сообщением, или отредактируй его перед отправкой.",
        reply_markup=ai_prompt_suggestion_keyboard(draft_id),
        parse_mode="HTML",
    )
    await state.update_data(suggested_prompt=prompt)


@router.callback_query(F.data.startswith("media:select_size:"))
async def select_image_size(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Show image size selection."""
    await callback.answer()
    draft_id = int(callback.data.split(":")[-1])

    await answer_nav(
        callback=callback,
        label="📐 Размер изображения",
        new_text=(
            "📐 <b>Выбери формат изображения:</b>\n\n"
            "📱 <b>Вертикальный</b> — для Stories, Reels\n"
            "⬜ <b>Квадратный</b> — универсальный\n"
            "🖼 <b>Горизонтальный</b> — для постов в ленте"
        ),
        reply_markup=ai_image_size_keyboard(draft_id),
    )


@router.callback_query(F.data.startswith("media:ai_gen_size:"))
async def ai_gen_with_size(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Generate image with selected size."""
    await callback.answer()

    parts = callback.data.split(":")
    size = parts[2]  # 1024x1024, 1792x1024, or 1024x1792
    draft_id = int(parts[3])

    # Get prompt from state
    data = await state.get_data()
    prompt = data.get("suggested_prompt")

    if not prompt:
        from app.handlers.keyboards import back_to_draft_keyboard
        await callback.message.answer(
            "❌ Не удалось найти промт. Попробуй перегенерировать.",
            reply_markup=back_to_draft_keyboard(draft_id)
        )
        return

    # Save selected size in state
    await state.update_data(image_size=size)

    # Remove buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Trigger generation
    await _run_generation(
        message=callback.message,
        session=session,
        state=state,
        draft_id=draft_id,
        prompt=prompt,
        user_id=callback.from_user.id,
        is_existing_message=False,
        size=size
    )


@router.callback_query(F.data.startswith("media:ai_gen_suggested:"))
async def ai_gen_suggested(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Generate image using suggested prompt (from message text)."""
    # Answer immediately to prevent timeout
    await callback.answer()
    
    draft_id = int(callback.data.split(":")[-1])

    # Extract prompt from message text
    # The message format is: "... <code>prompt</code> ..."
    # Telegram entity offsets are in UTF-16 code units.
    # Python strings are characters (codepoints). Emojis can cause offset mismatch if not handled.
    
    prompt = None
    
    # Try to get from state first if available (add this later to suggest_prompt)
    data = await state.get_data()
    prompt = data.get("suggested_prompt")
    
    if not prompt and callback.message.entities:
        for entity in callback.message.entities:
            if entity.type == "code":
                # Robust extraction using UTF-16
                text_utf16 = callback.message.text.encode("utf-16-le")
                start_byte = entity.offset * 2
                len_bytes = entity.length * 2
                prompt_bytes = text_utf16[start_byte : start_byte + len_bytes]
                prompt = prompt_bytes.decode("utf-16-le")
                break
    
    if not prompt:
        from app.handlers.keyboards import back_to_draft_keyboard
        await callback.message.answer("❌ Не удалось найти промт. Попробуй перегенерировать.", reply_markup=back_to_draft_keyboard(draft_id))
        return

    # Update logic:
    # 1. Edit current message to show "Generating..." state instead of sending new messages
    # This prevents clutter.
    # Update logic:
    # User wants to keep the prompt message visible in history.
    # So we DO NOT edit the prompt message. We send a NEW status message.
    # But we should probably remove the buttons from the prompt message to prevent double-clicks?
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    
    # Trigger generation with NEW message flow (with default size for backward compatibility)
    await _run_generation(
        message=callback.message,
        session=session,
        state=state,
        draft_id=draft_id,
        prompt=prompt,
        user_id=callback.from_user.id,
        is_existing_message=False,  # Send new message so prompt stays in history
        size="1024x1024"  # Default size
    )


from aiogram.utils.chat_action import ChatActionSender

# ... (imports)

async def _run_generation(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    draft_id: int,
    prompt: str,
    user_id: int,
    is_existing_message: bool = False,
    size: str = "1024x1024",
) -> None:
    """Shared generation logic."""
    from app.handlers.keyboards import back_to_draft_keyboard  # Local import to avoid circular dep if any

    result = await session.execute(
        select(Draft).where(Draft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        # If message exists, edit it, otherwise answer
        msg_text = "❌ Черновик не найден"
        if is_existing_message:
            await message.edit_text(msg_text, reply_markup=back_to_menu_keyboard())
        else:
            await message.answer(msg_text, reply_markup=back_to_menu_keyboard())
        await state.clear()
        return

    # Progress message 
    # If starting from manual prompt (text), reply with new message.
    # If starting from callback (suggested), we expect message to already be edited by caller OR we edit it here.
    # In this new flow, caller (ai_gen_suggested) edits it. 
    # process_ai_prompt sends new.
    
    status_msg = message
    if not is_existing_message:
        status_msg = await message.answer(
            "🎨 <b>Генерирую изображение...</b>\n\n"
            "⏱ Используем лучшую модель — генерация занимает 2-5 минут.\n"
            "Качество того стоит, подождите немного!",
            parse_mode="HTML",
            reply_markup=back_to_draft_keyboard(draft_id)
        )
    
    logger.info(f"Image Gen: Starting generation for user {user_id}")
    
    # Define callback for status updates
    async def update_status(text: str):
        try:
            # Only update if text changes significantly?
            # For now updating is fine, but we removed intermediate status updates in lower layer.
            # This callback might not be called often now.
            await status_msg.edit_text(text, reply_markup=back_to_draft_keyboard(draft_id))
        except Exception as e:
            logger.warning(f"Failed to update status message: {e}")

    # Generate image with typing animation
    try:
        # Release any DB locks/transactions before long wait
        await session.commit()

        # Import here to avoid circular dependency
        from app.image_generation import image_generator
        image_bytes = await image_generator.generate_bytes(prompt, size=size, status_callback=update_status)
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        error_text = "❌ Ошибка при генерации изображения. Попробуй позже."
        await status_msg.edit_text(error_text, reply_markup=back_to_draft_keyboard(draft_id))
        await state.clear()
        return

    logger.info(f"Image Gen: Finished generation for user {user_id}")

    if not image_bytes:
        error_msg = "❌ Не удалось сгенерировать изображение. Попробуй другой промпт."
        await status_msg.edit_text(error_msg, reply_markup=back_to_draft_keyboard(draft_id))
        await state.clear()
        return

    # Refetch draft to ensure fresh partial (in case detached or timed out?)
    # If session expired?
    # No, session is still valid.
    
    # Send image preview
    photo_file = BufferedInputFile(image_bytes, filename="image.png")
    
    # We must send a NEW message with the photo because we cannot convert a text message to a photo message via edit in all cases (or it's tricky).
    # Easier to send new photo message and delete the progress message.
    try:
        if is_existing_message:
             # Delete the "Generating..." text message if we are sending a photo
             await status_msg.delete()
        else:
             # Also delete progress msg for manual flow to clean up
             await status_msg.delete()
             pass
    except Exception:
        pass
        
    sent = await message.answer_photo(
        photo=photo_file,
        caption="🎨 <b>Изображение сгенерировано!</b>\n\nВыбери действие:",
        reply_markup=ai_image_result_keyboard(draft_id),
        parse_mode="HTML",
    )

    # Save prompt and temp file_id for later
    # Re-fetch draft just in case
    draft = await session.get(Draft, draft_id)
    if draft:
        draft.last_image_prompt = prompt
        draft.temp_image_id = sent.photo[-1].file_id
        await session.commit()

    await state.clear()



@router.message(DraftStates.waiting_for_ai_prompt)
async def process_ai_prompt(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Process AI image generation prompt."""
    data = await state.get_data()
    draft_id = data.get("draft_id")
    prompt = message.text

    # Save prompt in state
    await state.update_data(suggested_prompt=prompt)

    # Show size selection
    await message.answer(
        f"🎨 <b>AI Генерация изображения</b>\n\n"
        f"🪄 <b>Промт:</b>\n"
        f"<code>{prompt}</code>\n\n"
        f"📐 <b>Выбери формат изображения:</b>",
        reply_markup=ai_image_size_keyboard(draft_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("media:ai_save:"))
async def save_ai_image(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Save AI-generated image to draft."""
    draft_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.media))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    if not draft.temp_image_id:
        # Try to recover from the message photo if the temp flag was cleared (e.g., after delays)
        message_photo_id = None
        if callback.message and callback.message.photo:
            message_photo_id = callback.message.photo[-1].file_id

        if draft.media:
            await callback.answer("⚠️ Изображение уже сохранено или не найдено.", show_alert=True)
            await callback.message.edit_caption(
                caption=(
                    f"✅ <b>Черновик (Изображение сохранено)</b>\n\n"
                    f"{draft.content[:900]}{'...' if len(draft.content) > 900 else ''}"
                ),
                reply_markup=draft_edit_keyboard(
                    draft.id, 
                    has_media=True,
                    media_position=draft.media_position,
                    content_length=len(draft.content) if draft.content else 0
                ),
                parse_mode="HTML",
            )
            return

        if message_photo_id:
            draft.temp_image_id = message_photo_id
        else:
            await callback.answer("❌ Изображение не найдено (возможно, устарело). Генерируй снова.", show_alert=True)
            return

    # Clear existing media
    for media in draft.media:
        await session.delete(media)

    # Add new media
    media = DraftMedia(
        draft_id=draft.id,
        file_id=draft.temp_image_id,
        media_type=MediaType.PHOTO,
        position=0,
    )
    session.add(media)

    # Clear temp
    draft.temp_image_id = None
    await session.commit()

    await callback.answer("✅ Изображение сохранено!")

    # Show draft
    await callback.message.edit_caption(
        caption=(
            f"✅ <b>Изображение добавлено к посту!</b>\n\n"
            f"{draft.content[:900]}{'...' if len(draft.content) > 900 else ''}"
        ),
        reply_markup=draft_edit_keyboard(draft.id, has_media=True, content_length=len(draft.content) if draft.content else 0),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("media:ai_regen:"))
async def regenerate_ai_image(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Regenerate AI image with same or new prompt."""
    draft_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Draft).where(Draft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    # Use last prompt if available, otherwise ask for new
    if draft.last_image_prompt:
        await state.set_state(DraftStates.waiting_for_ai_prompt)
        await state.update_data(draft_id=draft_id)

        await callback.message.edit_caption(
            caption=(
                "🔄 <b>Перегенерация изображения</b>\n\n"
                f"Предыдущий промпт:\n<i>{draft.last_image_prompt}</i>\n\n"
                "Отправь новый промпт или используй предыдущий (отправь любой текст):"
            ),
            parse_mode="HTML",
        )
        await callback.answer()
    else:
        # No previous prompt, start from scratch
        await ai_gen_start(callback, state)


@router.callback_query(F.data.startswith("media:add:"))
async def add_manual_photo_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start adding manual photos."""
    draft_id = int(callback.data.split(":")[-1])

    await state.set_state(DraftStates.waiting_for_photo)
    await state.update_data(draft_id=draft_id, photos=[])

    await answer_nav(
        callback=callback,
        label="📷 Добавить фото",
        new_text=(
            "📸 <b>Добавление фото</b>\n\n"
            "Отправь одну или несколько фотографий.\n\n"
            "Когда закончишь, отправь (или нажми) команду /done"
        ),
        reply_markup=None,
    )


@router.message(DraftStates.waiting_for_photo, F.photo)
async def collect_photos(
    message: Message,
    state: FSMContext,
) -> None:
    """Collect photos from user."""
    data = await state.get_data()
    photos = data.get("photos", [])

    # Get largest photo
    photo: PhotoSize = message.photo[-1]
    photos.append(photo.file_id)

    await state.update_data(photos=photos)

    if len(photos) == 1:
        await message.answer(
            f"✅ Фото получено! Всего: {len(photos)}\n\n"
            "Отправь ещё фото или отправь (или нажми) команду /done для завершения."
        )
    else:
        await message.answer(f"✅ Получено {len(photos)} фото. Отправь ещё или отправь (или нажми) команду /done")


@router.message(DraftStates.waiting_for_photo, F.text.in_(["/done", "Готово"]))
async def save_manual_photos(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Save manually uploaded photos to draft."""
    data = await state.get_data()
    draft_id = data.get("draft_id")
    photos = data.get("photos", [])

    # Fetch draft first to allow returning to it
    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.media))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await message.answer("❌ Черновик не найден")
        await state.clear()
        return

    if not photos:
        await message.answer(
            "↩️ Фото не добавлены. Возврат к черновику.",
            reply_markup=draft_edit_keyboard(
                draft.id, 
                has_media=bool(draft.media),
                media_position=draft.media_position,
                content_length=len(draft.content) if draft.content else 0
            ),
        )
        await state.clear()
        return

    # Clear existing media
    for media in draft.media:
        await session.delete(media)

    # Add new photos
    for i, file_id in enumerate(photos):
        media = DraftMedia(
            draft_id=draft.id,
            file_id=file_id,
            media_type=MediaType.ALBUM if len(photos) > 1 else MediaType.PHOTO,
            position=i,
        )
        session.add(media)

    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ Добавлено {len(photos)} фото к посту!",
        reply_markup=draft_edit_keyboard(
            draft.id, 
            has_media=True,
            media_position=draft.media_position,
            content_length=len(draft.content) if draft.content else 0
        ),
    )


@router.callback_query(F.data.startswith("media:clear:"))
async def clear_media(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Clear all media from draft."""
    draft_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.media))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    # Delete all media
    for media in draft.media:
        await session.delete(media)

    await session.commit()

    # Logic:
    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🗑 Удалить фото")

    # 3. Show updated draft
    await callback.message.answer(
        f"✏️ <b>{draft.title or 'Черновик'}</b>\n\n"
        f"{draft.content}",
        reply_markup=draft_edit_keyboard(
            draft.id,
            has_media=False,
            media_position=draft.media_position,
            content_length=len(draft.content) if draft.content else 0
        ),
        parse_mode="HTML",
    )
