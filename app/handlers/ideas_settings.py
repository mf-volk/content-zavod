"""Idea settings handlers."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import User, ManagedChannel, IdeaSourceType
from app.handlers.keyboards import (
    ideas_settings_keyboard,
    back_to_menu_keyboard,
)
from app.handlers.states import IdeaStates
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="ideas_settings")


async def get_current_channel(session: AsyncSession, tg_user_id: int) -> Optional[ManagedChannel]:
    """Get user's current selected channel."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.current_channel))
    )
    user = result.scalar_one_or_none()
    return user.current_channel if user else None


@router.callback_query(F.data == "ideas:settings")
async def show_ideas_settings(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show ideas settings menu."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.answer("⚠️ Канал не выбран", show_alert=True)
        return

    is_archive = channel.idea_source_type in (IdeaSourceType.ARCHIVE, IdeaSourceType.MIXED) 
    # For now toggle implies switching between recent/archive. Mixed could be advanced.
    # Let's assume toggle switches RECENT <-> ARCHIVE for simplicity, 
    # or creates a cycle if MIXED is desired.
    # User asked for "Archive search".
    
    await answer_nav(
        callback=callback,
        label="⚙️ Настройки идей",
        new_text=(
            f"⚙️ <b>Настройки поиска идей</b>\n\n"
            f"<b>Канал:</b> {channel.title}\n\n"
            "Здесь ты можешь уточнить тему поиска или включить поиск по архивам."
        ),
        reply_markup=ideas_settings_keyboard(
            current_topic=channel.idea_topic,
            is_archive=is_archive,
        ),
    )


@router.callback_query(F.data == "ideas:settings:toggle_archive")
async def toggle_archive_mode(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Toggle archive mode."""
    channel = await get_current_channel(session, callback.from_user.id)
    if not channel:
        return

    # Toggle logic: RECENT -> ARCHIVE -> RECENT
    if channel.idea_source_type == IdeaSourceType.RECENT:
        channel.idea_source_type = IdeaSourceType.ARCHIVE
    else:
        channel.idea_source_type = IdeaSourceType.RECENT
    
    await session.commit()
    
    # Update view
    is_archive = channel.idea_source_type == IdeaSourceType.ARCHIVE
    await callback.message.edit_reply_markup(
        reply_markup=ideas_settings_keyboard(
            current_topic=channel.idea_topic,
            is_archive=is_archive,
        )
    )
    await callback.answer("✅ Режим поиска изменен")


@router.callback_query(F.data == "ideas:settings:set_topic")
async def start_set_topic(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start setting topic."""
    await state.set_state(IdeaStates.waiting_for_topic)
    
    await callback.message.edit_text(
        "🏷 <b>Настройка темы</b>\n\n"
        "Напиши тему или ключевые слова, которые я должен искать в постах доноров.\n"
        "Например: <i>Маркетинг, AI, Рецепты</i>\n\n"
        "Отправь /clear чтобы сбросить тему (искать всё подряд).",
        reply_markup=back_to_menu_keyboard(), # Or back to settings? Better to settings but message changed. 
        # using standard back logic
    )
    await callback.answer()


@router.message(IdeaStates.waiting_for_topic)
async def process_set_topic(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Process topic text."""
    channel = await get_current_channel(session, message.from_user.id)
    if not channel:
        await state.clear()
        return

    text = message.text.strip()
    
    if text == "/clear":
        channel.idea_topic = None
        reply_text = "✅ Тема сброшена. Теперь ищу всё подряд."
    else:
        channel.idea_topic = text[:255]
        reply_text = f"✅ Тема установлена: <b>{channel.idea_topic}</b>"

    await session.commit()
    await state.clear()
    
    # Show settings again
    is_archive = channel.idea_source_type == IdeaSourceType.ARCHIVE
    
    await message.answer(
        f"{reply_text}\n\n"
        f"⚙️ <b>Настройки поиска идей</b>",
        reply_markup=ideas_settings_keyboard(
            current_topic=channel.idea_topic,
            is_archive=is_archive,
        ),
        parse_mode="HTML"
    )
