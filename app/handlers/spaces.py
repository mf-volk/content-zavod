"""Spaces handler for managing material collections."""

from __future__ import annotations

import json
import logging
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ContentType, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    User, Space, SpaceMaterial, Draft, ManagedChannel,
    SpaceStatus, SpaceMaterialType, Idea, DraftStatus,
)
from app.handlers.states import SpaceStates
from app.handlers.keyboards import back_to_menu_keyboard
from app.llm_client import llm_client
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="spaces")

MAX_MATERIALS_PER_SPACE = 7


# ============================================================
# KEYBOARDS
# ============================================================


MAX_SPACES = 7


def spaces_list_keyboard(spaces: list, page: int = 0) -> InlineKeyboardMarkup:
    """Keyboard for spaces list."""
    buttons = []

    for space in spaces:
        status_icon = {
            SpaceStatus.COLLECTING: "📥",
            SpaceStatus.PROCESSING: "⏳",
            SpaceStatus.READY: "✅",
            SpaceStatus.ERROR: "❌",
        }.get(space.status, "📁")

        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {space.title}",
                callback_data=f"spaces:view:{space.id}",
            )
        ])

    # Only show create button if under limit
    if len(spaces) < MAX_SPACES:
        buttons.append([
            InlineKeyboardButton(text="➕ Загрузить документы", callback_data="spaces:create")
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def space_view_keyboard(space: Space) -> InlineKeyboardMarkup:
    """Keyboard for viewing a space."""
    buttons = []

    # Material management buttons (toggle selection / delete)
    for m in space.materials[:MAX_MATERIALS_PER_SPACE]:
        type_icons = {
            SpaceMaterialType.TEXT: "📝",
            SpaceMaterialType.VOICE: "🎤",
            SpaceMaterialType.AUDIO: "🎵",
            SpaceMaterialType.IMAGE: "📸",
            SpaceMaterialType.DOCUMENT_WORD: "📄",
            SpaceMaterialType.DOCUMENT_EXCEL: "📊",
            SpaceMaterialType.DOCUMENT_PDF: "📕",
            SpaceMaterialType.LINK: "🔗",
            SpaceMaterialType.VIDEO: "🎬",
            SpaceMaterialType.YOUTUBE: "📺",
        }
        icon = type_icons.get(m.material_type, "📎")
        name = (m.file_name or (m.content[:20] if m.content else m.material_type.value))[:18]

        # Selection checkbox
        checkbox = "☑️" if m.is_selected else "⬜️"

        buttons.append([
            InlineKeyboardButton(
                text=f"{checkbox} {icon} {name}",
                callback_data=f"spaces:toggle:{space.id}:{m.id}"
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"spaces:del_mat:{space.id}:{m.id}"
            ),
        ])

    # Show ideas button FIRST if ideas exist
    if space.status == SpaceStatus.READY and space.generated_ideas:
        buttons.append([
            InlineKeyboardButton(text="💡 Показать идеи", callback_data=f"spaces:ideas:{space.id}:0")
        ])

    # Add materials button - show in COLLECTING, READY and ERROR statuses
    if space.status in (SpaceStatus.COLLECTING, SpaceStatus.READY, SpaceStatus.ERROR):
        if len(space.materials) < MAX_MATERIALS_PER_SPACE:
            buttons.append([
                InlineKeyboardButton(text="📥 Загрузить ещё", callback_data=f"spaces:add:{space.id}")
            ])

    # Generate ideas button
    if space.materials:
        selected_count = sum(1 for m in space.materials if m.is_selected)
        if selected_count > 0:
            # If no ideas yet - show "Generate ideas" button
            if not space.generated_ideas or space.status != SpaceStatus.READY:
                buttons.append([
                    InlineKeyboardButton(text=f"✨ Сгенерировать идеи ({selected_count})", callback_data=f"spaces:process:{space.id}")
                ])
            else:
                # If ideas already exist - show "Refresh ideas" button
                buttons.append([
                    InlineKeyboardButton(text=f"🔄 Обновить идеи ({selected_count})", callback_data=f"spaces:process:{space.id}")
                ])

    buttons.append([
        InlineKeyboardButton(text="🗑 Удалить папку", callback_data=f"spaces:delete:{space.id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ К списку", callback_data="spaces:list")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def collecting_keyboard(space_id: int, count: int) -> InlineKeyboardMarkup:
    """Keyboard while collecting materials."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Готово ({count}/{MAX_MATERIALS_PER_SPACE})",
            callback_data=f"spaces:finish_collecting:{space_id}"
        )],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="spaces:list")],
    ])


# ============================================================
# HELPERS
# ============================================================


async def get_user_with_channel(session: AsyncSession, tg_user_id: int):
    """Get user with current channel."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.current_channel))
    )
    return result.scalar_one_or_none()


def get_material_type_from_message(message: Message) -> Optional[SpaceMaterialType]:
    """Determine material type from message."""
    if message.voice:
        return SpaceMaterialType.VOICE
    elif message.audio:
        return SpaceMaterialType.AUDIO
    elif message.photo:
        return SpaceMaterialType.IMAGE
    elif message.video:
        return SpaceMaterialType.VIDEO
    elif message.document:
        mime = message.document.mime_type or ""
        filename = message.document.file_name or ""

        if "word" in mime or filename.endswith(".docx") or filename.endswith(".doc"):
            return SpaceMaterialType.DOCUMENT_WORD
        elif "excel" in mime or "spreadsheet" in mime or filename.endswith(".xlsx") or filename.endswith(".xls"):
            return SpaceMaterialType.DOCUMENT_EXCEL
        elif "pdf" in mime or filename.endswith(".pdf"):
            return SpaceMaterialType.DOCUMENT_PDF
        else:
            return None  # Unsupported document type
    elif message.text:
        text = message.text.strip()
        if text.startswith("http://") or text.startswith("https://"):
            # Check if it's a YouTube link
            from app.services.document_processor import is_youtube_url
            if is_youtube_url(text):
                return SpaceMaterialType.YOUTUBE
            return SpaceMaterialType.LINK
        return SpaceMaterialType.TEXT

    return None


# ============================================================
# HANDLERS
# ============================================================


@router.callback_query(F.data == "spaces:list")
async def list_spaces(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Show list of user's spaces."""
    await state.clear()

    user = await get_user_with_channel(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Get user's spaces
    result = await session.execute(
        select(Space)
        .where(Space.user_id == user.id)
        .order_by(Space.created_at.desc())
        .limit(20)
    )
    spaces = result.scalars().all()

    if not spaces:
        text = (
            "📁 <b>Мои папки</b>\n\n"
            "У вас пока нет папок с документами.\n\n"
            f"<i>Максимум {MAX_SPACES} папок, до {MAX_MATERIALS_PER_SPACE} файлов в каждой.\n"
            "Загрузите Word, Excel, PDF, ссылки, аудио — \n"
            "бот извлечёт информацию и создаст идеи для постов.</i>"
        )
    else:
        remaining = MAX_SPACES - len(spaces)
        limit_text = f"Использовано {len(spaces)} из {MAX_SPACES}" if remaining > 0 else "Достигнут лимит папок"
        text = (
            "📁 <b>Мои папки</b>\n\n"
            f"{limit_text}\n"
            f"<i>В каждой папке до {MAX_MATERIALS_PER_SPACE} файлов</i>\n\n"
            "Выберите папку:"
        )

    await answer_nav(
        callback=callback,
        label="📁 Мои папки",
        new_text=text,
        reply_markup=spaces_list_keyboard(spaces),
    )


@router.callback_query(F.data == "spaces:create")
async def create_space_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Start creating a new space."""
    user = await get_user_with_channel(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    channel_id = user.current_channel.id if user.current_channel else None

    await state.update_data(channel_id=channel_id, user_id=user.id)
    await state.set_state(SpaceStates.waiting_for_title)

    await callback.message.edit_text(
        "📁 <b>Создание папки</b>\n\n"
        f"Введите название для новой папки (максимум {MAX_SPACES}):\n"
        "<i>Например: «Маркетинговые исследования» или «Идеи на январь»</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="spaces:list")]
        ]),
        parse_mode="HTML",
    )


@router.message(SpaceStates.waiting_for_title, F.text)
async def create_space_title(message: Message, session: AsyncSession, state: FSMContext):
    """Create space with given title."""
    title = message.text.strip()[:255]

    if not title or title.startswith("/"):
        await message.answer("⚠️ Введите корректное название.")
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")

    # Create space
    space = Space(
        user_id=user_id,
        channel_id=channel_id,
        title=title,
        status=SpaceStatus.COLLECTING,
    )
    session.add(space)
    await session.commit()
    await session.refresh(space)

    await state.update_data(space_id=space.id)
    await state.set_state(SpaceStates.waiting_for_materials)

    await message.answer(
        f"✅ Папка <b>«{title}»</b> создана!\n\n"
        f"Отправляйте материалы (до {MAX_MATERIALS_PER_SPACE} штук):\n\n"
        "📄 <b>Документы:</b> .pdf, .docx, .xlsx (до 20 МБ)\n"
        "🔗 <b>Ссылки:</b> http:// или https://\n"
        "🎤 <b>Аудио:</b> голосовые, .mp3, .ogg (до 25 МБ)\n"
        "📸 <b>Фото:</b> .jpg, .png (до 20 МБ)\n"
        "📝 <b>Текст:</b> просто напишите сообщение\n\n"
        "Когда закончите — нажмите <b>Готово</b>.",
        reply_markup=collecting_keyboard(space.id, 0),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("spaces:add:"))
async def add_materials_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Start adding materials to existing space."""
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    current_count = len(space.materials)
    if current_count >= MAX_MATERIALS_PER_SPACE:
        await callback.answer(f"⚠️ Достигнут лимит ({MAX_MATERIALS_PER_SPACE} материалов)", show_alert=True)
        return

    await state.update_data(space_id=space.id)
    await state.set_state(SpaceStates.waiting_for_materials)

    await callback.message.edit_text(
        f"📥 <b>Добавление в «{space.title}»</b>\n\n"
        f"Загружено: {current_count}/{MAX_MATERIALS_PER_SPACE}\n\n"
        "📄 <b>Документы:</b> .pdf, .docx, .xlsx (до 20 МБ)\n"
        "🔗 <b>Ссылки:</b> http:// или https://\n"
        "🎤 <b>Аудио:</b> голосовые, .mp3, .ogg (до 25 МБ)\n"
        "📸 <b>Фото:</b> .jpg, .png (до 20 МБ)\n"
        "📝 <b>Текст:</b> просто напишите сообщение",
        reply_markup=collecting_keyboard(space.id, current_count),
        parse_mode="HTML",
    )


@router.message(SpaceStates.waiting_for_materials, F.content_type.in_({
    ContentType.TEXT, ContentType.VOICE, ContentType.AUDIO,
    ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO,
}))
async def collect_material(message: Message, session: AsyncSession, state: FSMContext, bot: Bot):
    """Collect material and save to space."""
    data = await state.get_data()
    space_id = data.get("space_id")

    if not space_id:
        await message.answer("⚠️ Ошибка: папка не выбрана.")
        await state.clear()
        return

    # Get space with materials count
    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if not space:
        await message.answer("❌ Папка не найдена.")
        await state.clear()
        return

    current_count = len(space.materials)
    if current_count >= MAX_MATERIALS_PER_SPACE:
        await message.answer(
            f"⚠️ Достигнут лимит материалов ({MAX_MATERIALS_PER_SPACE}).\n"
            "Нажмите <b>Готово</b> для обработки.",
            reply_markup=collecting_keyboard(space.id, current_count),
            parse_mode="HTML",
        )
        return

    # Determine material type
    material_type = get_material_type_from_message(message)

    if not material_type:
        # Get filename for better error message
        filename = ""
        if message.document:
            filename = f"\nФайл: {message.document.file_name or 'неизвестный'}"

        await message.answer(
            f"⚠️ <b>Формат не поддерживается</b>{filename}\n\n"
            "Поддерживаемые форматы:\n"
            "• Документы: .pdf, .docx, .xlsx (до 20 МБ)\n"
            "• Аудио: голосовые, .mp3, .ogg (до 25 МБ)\n"
            "• Фото: .jpg, .png (до 20 МБ)\n"
            "• Ссылки и текст",
            parse_mode="HTML"
        )
        return

    # Create material record
    material = SpaceMaterial(
        space_id=space.id,
        material_type=material_type,
    )

    # Save file or content
    type_name = ""

    if material_type == SpaceMaterialType.TEXT:
        material.content = message.text
        type_name = "Текст"

    elif material_type == SpaceMaterialType.LINK:
        material.content = message.text.strip()
        material.source_url = message.text.strip()
        type_name = "Ссылка"

    elif material_type == SpaceMaterialType.YOUTUBE:
        material.content = message.text.strip()
        material.source_url = message.text.strip()
        type_name = "YouTube"

    elif material_type == SpaceMaterialType.VOICE:
        material.file_id = message.voice.file_id
        material.file_name = f"voice_{message.voice.file_id}.ogg"
        type_name = "Голосовое"

    elif material_type == SpaceMaterialType.AUDIO:
        material.file_id = message.audio.file_id
        material.file_name = message.audio.file_name or f"audio_{message.audio.file_id}"
        type_name = "Аудио"

    elif material_type == SpaceMaterialType.IMAGE:
        photo = message.photo[-1]
        material.file_id = photo.file_id
        material.file_name = f"photo_{photo.file_id}.jpg"
        type_name = "Фото"

    elif material_type == SpaceMaterialType.VIDEO:
        material.file_id = message.video.file_id
        material.file_name = message.video.file_name or f"video_{message.video.file_id}"
        type_name = "Видео"

    elif material_type in (SpaceMaterialType.DOCUMENT_WORD, SpaceMaterialType.DOCUMENT_EXCEL, SpaceMaterialType.DOCUMENT_PDF):
        material.file_id = message.document.file_id
        material.file_name = message.document.file_name
        type_names = {
            SpaceMaterialType.DOCUMENT_WORD: "Word",
            SpaceMaterialType.DOCUMENT_EXCEL: "Excel",
            SpaceMaterialType.DOCUMENT_PDF: "PDF",
        }
        type_name = type_names.get(material_type, "Документ")

    session.add(material)
    await session.commit()

    new_count = current_count + 1

    await message.answer(
        f"✅ <b>{type_name}</b> добавлен! ({new_count}/{MAX_MATERIALS_PER_SPACE})\n\n"
        "Продолжайте отправлять материалы или нажмите <b>Готово</b>.",
        reply_markup=collecting_keyboard(space.id, new_count),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("spaces:finish_collecting:"))
async def finish_collecting(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Finish collecting and go back to space view."""
    await state.clear()
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    materials_count = len(space.materials)

    if materials_count == 0:
        await callback.answer("⚠️ Добавьте хотя бы один материал", show_alert=True)
        return

    # Show space view with process button
    await callback.answer()
    await show_space_view(callback, space)


@router.callback_query(F.data.startswith("spaces:view:"))
async def view_space(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """View space details."""
    await state.clear()
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    await callback.answer()
    await show_space_view(callback, space)


async def show_space_view(callback: CallbackQuery, space: Space):
    """Show space details view."""
    status_text = {
        SpaceStatus.COLLECTING: "📥 Сбор материалов",
        SpaceStatus.PROCESSING: "⏳ Обработка...",
        SpaceStatus.READY: "✅ Готово",
        SpaceStatus.ERROR: "❌ Ошибка",
    }.get(space.status, "❓")

    selected_count = sum(1 for m in space.materials if m.is_selected)
    total_count = len(space.materials)

    text = (
        f"📁 <b>{space.title}</b>\n\n"
        f"Статус: {status_text}\n"
        f"Материалов: {total_count}/{MAX_MATERIALS_PER_SPACE}\n"
        f"Выбрано для генерации: {selected_count}\n\n"
        "<i>Нажмите на материал чтобы включить/выключить его для генерации идей. "
        "Нажмите 🗑 чтобы удалить материал.</i>"
    )

    if space.summary:
        text += f"\n\n<b>Краткое содержание:</b>\n{space.summary[:400]}..."

    await callback.message.edit_text(
        text,
        reply_markup=space_view_keyboard(space),
        parse_mode="HTML",
    )
    # Note: Don't call callback.answer() here - it may timeout after long processing
    # and callers should handle it themselves if needed


@router.callback_query(F.data.startswith("spaces:process:"))
async def process_space(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Process selected materials in space and generate ideas."""
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials), selectinload(Space.channel))
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    if not space.materials:
        await callback.answer("⚠️ Добавьте материалы для обработки", show_alert=True)
        return

    # Filter only selected materials
    selected_materials = [m for m in space.materials if m.is_selected]

    if not selected_materials:
        await callback.answer("⚠️ Выберите хотя бы один материал для генерации", show_alert=True)
        return

    # Answer callback BEFORE long operation to avoid "query too old" error
    await callback.answer()

    # Update status
    space.status = SpaceStatus.PROCESSING
    await session.commit()

    await callback.message.edit_text(
        f"⏳ <b>Обработка папки «{space.title}»</b>\n\n"
        f"Выбрано материалов: {len(selected_materials)}\n\n"
        "1️⃣ Извлечение текста из документов\n"
        "2️⃣ Транскрипция аудио\n"
        "3️⃣ Анализ изображений\n"
        "4️⃣ Загрузка ссылок\n"
        "5️⃣ Генерация идей\n\n"
        "<i>⏱ Это может занять 1-3 минуты...</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"spaces:cancel:{space_id}")]
        ]),
    )

    try:
        from app.services.document_processor import process_material

        all_texts = []

        # Process only SELECTED materials
        for material in selected_materials:
            if material.is_processed and material.processed_text:
                all_texts.append(material.processed_text)
                continue

            file_bytes = None

            # Download file if needed
            if material.file_id:
                try:
                    file = await bot.get_file(material.file_id)
                    file_bytes = await bot.download_file(file.file_path)
                    if hasattr(file_bytes, 'read'):
                        file_bytes = file_bytes.read()
                except Exception as e:
                    logger.error(f"Error downloading file: {e}")
                    material.error_message = f"Ошибка загрузки: {e}"
                    continue

            # Process material
            processed_text, language, error = await process_material(
                material_type=material.material_type.value,
                file_bytes=file_bytes,
                file_name=material.file_name,
                content=material.content,
                openai_client=llm_client.client,
                auto_translate=True,
            )

            if error:
                material.error_message = error
                logger.warning(f"Material {material.id} processing error: {error}")
            else:
                material.processed_text = processed_text
                material.language = language
                material.is_processed = True
                if processed_text:
                    all_texts.append(processed_text)

        await session.commit()

        # Generate ideas from all processed texts
        if all_texts:
            # Format materials with clear separation and numbering
            formatted_materials = []
            for i, text in enumerate(all_texts, 1):
                formatted_materials.append(f"=== МАТЕРИАЛ {i} ===\n{text}")

            combined_text = "\n\n".join(formatted_materials)
            materials_count = len(all_texts)

            channel_title = space.channel.title if space.channel else "канала"
            channel_tov = space.channel.tone_of_voice if space.channel else None

            # Generate ideas using LLM
            ideas_result = await llm_client.generate_ideas_from_space(
                materials_text=combined_text,
                space_title=space.title,
                channel_title=channel_title,
                tone_of_voice=channel_tov,
                materials_count=materials_count,
            )

            space.summary = ideas_result.get("summary", "")
            space.generated_ideas = json.dumps(ideas_result.get("ideas", []), ensure_ascii=False)
            space.status = SpaceStatus.READY
        else:
            space.status = SpaceStatus.ERROR
            space.summary = "Не удалось извлечь текст из материалов"

        await session.commit()

        # Show result
        await session.refresh(space, ["materials"])
        await show_space_view(callback, space)

    except Exception as e:
        logger.error(f"Error processing space {space_id}: {e}", exc_info=True)
        space.status = SpaceStatus.ERROR
        await session.commit()

        await callback.message.edit_text(
            f"❌ <b>Ошибка обработки</b>\n\n{str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"spaces:view:{space_id}")]
            ]),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("spaces:cancel:"))
async def cancel_space_processing(callback: CallbackQuery, session: AsyncSession):
    """Cancel space processing and return to view."""
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    # Reset status back to COLLECTING
    space.status = SpaceStatus.COLLECTING
    await session.commit()

    await callback.answer("⏹ Обработка отменена")

    # Return to space view
    await show_space_view(callback, space)


@router.callback_query(F.data.startswith("spaces:ideas:"))
async def show_space_ideas(callback: CallbackQuery, session: AsyncSession):
    """Show generated ideas from space with pagination (3 ideas per page like donors)."""
    parts = callback.data.split(":")
    space_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    result = await session.execute(
        select(Space).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()

    if not space or not space.generated_ideas:
        await callback.answer("❌ Идеи не найдены", show_alert=True)
        return

    try:
        ideas = json.loads(space.generated_ideas)
    except:
        ideas = []

    if not ideas:
        await callback.answer("⚠️ Идеи не сгенерированы", show_alert=True)
        return

    # Pagination: 3 ideas per page
    IDEAS_PER_PAGE = 3
    total = len(ideas)
    total_pages = (total + IDEAS_PER_PAGE - 1) // IDEAS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * IDEAS_PER_PAGE
    end_idx = min(start_idx + IDEAS_PER_PAGE, total)
    page_ideas = ideas[start_idx:end_idx]

    # Format ideas text
    text = f"💡 <b>Идеи из «{space.title}»</b>\n\n"
    text += "_" * 30 + "\n\n"

    buttons = []

    for i, idea in enumerate(page_ideas):
        idea_num = start_idx + i + 1
        title = idea.get("title", "Без названия")
        description = idea.get("description", "")
        key_points = idea.get("key_points", [])

        text += f"<b>Идея {idea_num}:</b>\n"
        text += f"⚡ {title}\n\n"
        text += f"{description}\n"

        if key_points:
            text += "\n<b>Ключевые мысли:</b>\n"
            for point in key_points[:3]:
                text += f"• {point}\n"

        text += "\n" + "_" * 30 + "\n\n"

        # Button to create draft from this idea
        buttons.append([
            InlineKeyboardButton(
                text=f"✏️ Написать пост {idea_num}",
                callback_data=f"spaces:draft_idea:{space_id}:{start_idx + i}"
            )
        ])

    text += "👇 <b>Выбери идею для создания поста:</b>"

    # Pagination buttons
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(text="◀️ Пред.", callback_data=f"spaces:ideas:{space_id}:{page - 1}")
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(text="След. ▶️", callback_data=f"spaces:ideas:{space_id}:{page + 1}")
            )
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к папке", callback_data=f"spaces:view:{space_id}")
    ])

    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("spaces:draft_idea:"))
async def create_draft_from_space_idea(callback: CallbackQuery, session: AsyncSession):
    """Create draft from a space idea (like donors flow)."""
    parts = callback.data.split(":")
    space_id = int(parts[2])
    idea_idx = int(parts[3])

    # Get space with channel
    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.channel))
    )
    space = result.scalar_one_or_none()

    if not space or not space.generated_ideas:
        await callback.answer("❌ Идеи не найдены", show_alert=True)
        return

    try:
        ideas = json.loads(space.generated_ideas)
    except:
        ideas = []

    if idea_idx >= len(ideas):
        await callback.answer("❌ Идея не найдена", show_alert=True)
        return

    idea = ideas[idea_idx]
    title = idea.get("title", "Без названия")
    description = idea.get("description", "")
    key_points = idea.get("key_points", [])

    # Get channel for TOV
    channel = space.channel
    if not channel:
        # Try to get user's current channel
        from app.handlers.channels import get_current_channel
        channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.answer("⚠️ Выберите канал для создания поста", show_alert=True)
        return

    # Show progress
    progress_msg = await callback.message.edit_text(
        f"⏳ <b>Создание поста...</b>\n\n"
        f"Идея: {title}\n\n"
        f"Генерирую текст поста с учётом стиля канала.",
        parse_mode="HTML",
    )

    # Prepare idea description for LLM
    idea_description = description
    if key_points:
        idea_description += "\n\nКлючевые мысли:\n" + "\n".join(f"- {p}" for p in key_points)

    # Generate draft
    try:
        await session.commit()  # Release DB lock

        draft_data = await llm_client.generate_draft(
            idea_title=title,
            idea_description=idea_description,
            tone_of_voice=channel.tone_of_voice,
            language=channel.language,
        )
    except Exception as e:
        logger.error(f"Failed to generate draft: {e}")
        await progress_msg.edit_text(
            "❌ Ошибка генерации. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"spaces:ideas:{space_id}:0")]
            ])
        )
        return

    if not draft_data:
        await progress_msg.edit_text(
            "❌ Не удалось сгенерировать пост. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"spaces:ideas:{space_id}:0")]
            ])
        )
        return

    # Create draft
    draft = Draft(
        managed_channel_id=channel.id,
        title=title[:500],
        content=draft_data.content,
        status=DraftStatus.DRAFT,
    )
    session.add(draft)
    await session.commit()

    # Show draft with full edit options (same as donors flow)
    from app.handlers.keyboards import draft_edit_keyboard

    await callback.message.edit_text(
        f"✅ <b>Черновик создан!</b>\n\n"
        f"{draft.content}",
        reply_markup=draft_edit_keyboard(draft.id, has_media=False),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("spaces:save_ideas:"))
async def save_space_ideas(callback: CallbackQuery, session: AsyncSession):
    """Save generated ideas to channel's ideas list."""
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()

    if not space or not space.generated_ideas or not space.channel_id:
        await callback.answer("❌ Ошибка сохранения", show_alert=True)
        return

    try:
        ideas = json.loads(space.generated_ideas)
    except:
        ideas = []

    saved_count = 0
    for idea_data in ideas:
        idea = Idea(
            managed_channel_id=space.channel_id,
            title=idea_data.get("title", "Идея из папки")[:500],
            description=idea_data.get("description", ""),
            source="space",
        )
        session.add(idea)
        saved_count += 1

    await session.commit()

    await callback.answer(f"✅ Сохранено {saved_count} идей!", show_alert=True)


@router.callback_query(F.data.startswith("spaces:toggle:"))
async def toggle_material_selection(callback: CallbackQuery, session: AsyncSession):
    """Toggle material selection for idea generation."""
    parts = callback.data.split(":")
    space_id = int(parts[2])
    material_id = int(parts[3])

    # Get material
    result = await session.execute(
        select(SpaceMaterial).where(SpaceMaterial.id == material_id)
    )
    material = result.scalar_one_or_none()

    if not material:
        await callback.answer("❌ Материал не найден", show_alert=True)
        return

    # Toggle selection
    material.is_selected = not material.is_selected
    await session.commit()

    status = "включен ✅" if material.is_selected else "выключен ⬜️"
    await callback.answer(f"Материал {status}")

    # Refresh space view
    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if space:
        await show_space_view(callback, space)


@router.callback_query(F.data.startswith("spaces:del_mat:"))
async def delete_material(callback: CallbackQuery, session: AsyncSession):
    """Delete a single material from space."""
    parts = callback.data.split(":")
    space_id = int(parts[2])
    material_id = int(parts[3])

    # Get material
    result = await session.execute(
        select(SpaceMaterial).where(SpaceMaterial.id == material_id)
    )
    material = result.scalar_one_or_none()

    if not material:
        await callback.answer("❌ Материал не найден", show_alert=True)
        return

    # Delete material
    await session.delete(material)
    await session.commit()
    await callback.answer("✅ Материал удалён")

    # Refresh space view
    result = await session.execute(
        select(Space)
        .where(Space.id == space_id)
        .options(selectinload(Space.materials))
    )
    space = result.scalar_one_or_none()

    if space:
        # If no materials left and was in READY, reset to COLLECTING
        if not space.materials:
            space.status = SpaceStatus.COLLECTING
            space.generated_ideas = None
            space.summary = None
            await session.commit()

        await show_space_view(callback, space)


@router.callback_query(F.data.startswith("spaces:delete:"))
async def delete_space(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Delete a space."""
    await state.clear()
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()

    if not space:
        await callback.answer("❌ Папка не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 <b>Удалить папку «{space.title}»?</b>\n\n"
        "Все материалы и сгенерированные идеи будут удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"spaces:confirm_delete:{space_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"spaces:view:{space_id}"),
            ]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("spaces:confirm_delete:"))
async def confirm_delete_space(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Confirm space deletion."""
    space_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Space).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()

    if space:
        await session.delete(space)
        await session.commit()
        await callback.answer("✅ Удалено")

    # Go back to list properly
    await list_spaces(callback, session, state)


@router.message(SpaceStates.waiting_for_materials, Command("cancel"))
async def cancel_collecting(message: Message, state: FSMContext):
    """Cancel material collection."""
    await state.clear()
    await message.answer(
        "❌ Сбор материалов отменён.",
        reply_markup=back_to_menu_keyboard(),
    )
