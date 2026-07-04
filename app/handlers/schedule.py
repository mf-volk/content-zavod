"""Scheduling handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.services.publisher import publish_content

from app.db.models import Draft, ScheduledPost, ScheduledPostStatus, DraftStatus
from app.handlers.keyboards import (
    schedule_keyboard,
    scheduled_posts_keyboard,
    scheduled_post_view_keyboard,
    back_to_menu_keyboard,
)
from app.handlers.states import ScheduleStates
from app.utils import answer_nav
from app.handlers.keyboards import back_to_menu_keyboard

logger = logging.getLogger(__name__)
router = Router(name="schedule")


@router.callback_query(F.data == "schedule:list")
async def list_scheduled_posts(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show scheduled posts list."""
    from app.db.models import User, ManagedChannel

    # Get current channel
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == callback.from_user.id)
        .options(selectinload(User.current_channel))
    )
    user = result.scalar_one_or_none()


    if not user or not user.current_channel:
        await answer_nav(
            callback=callback,
            label="📅 Расписание",
            new_text="⚠️ Сначала выбери канал для работы.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    channel = user.current_channel

    # Get scheduled posts
    result = await session.execute(
        select(ScheduledPost)
        .join(Draft)
        .where(
            Draft.managed_channel_id == channel.id,
            ScheduledPost.status == ScheduledPostStatus.PLANNED,
        )
        .order_by(ScheduledPost.scheduled_at)
        .options(selectinload(ScheduledPost.draft))
    )
    posts = result.scalars().all()

    if not posts:
        text = (
            f"📅 <b>Расписание для канала {channel.title}</b>\n\n"
            "Нет запланированных публикаций."
        )
        posts_list = []
    else:
        text = (
            f"📅 <b>Расписание для канала {channel.title}</b>\n\n"
            f"Запланировано публикаций: {len(posts)}"
        )

        tz = pytz.timezone(channel.timezone)
        posts_list = [
            (
                p.id,
                p.draft.title or p.draft.content[:30],
                p.scheduled_at.replace(tzinfo=pytz.UTC).astimezone(tz).strftime("%d.%m %H:%M"),
            )
            for p in posts
        ]

    await answer_nav(
        callback=callback,
        label="📅 Расписание",
        new_text=text,
        reply_markup=scheduled_posts_keyboard(posts_list),
    )


@router.callback_query(F.data.startswith("schedule:draft:"))
async def schedule_draft(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show scheduling options for draft."""
    draft_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Draft).where(Draft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    await answer_nav(
        callback=callback,
        label="📅 Запланировать",
        new_text=(
            "📅 <b>Планирование публикации</b>\n\n"
            "Выбери время публикации:"
        ),
        reply_markup=schedule_keyboard(draft_id),
    )


@router.callback_query(F.data.startswith("schedule:quick:"))
async def schedule_quick(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Quick schedule (1 or 3 hours)."""
    parts = callback.data.split(":")
    hours = int(parts[2])
    draft_id = int(parts[3])

    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.managed_channel))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Черновик не найден", show_alert=True)
        return

    # Calculate scheduled time
    tz = pytz.timezone(draft.managed_channel.timezone)
    now = datetime.now(tz)
    scheduled_at = now + timedelta(hours=hours)

    # Convert to UTC for storage
    scheduled_at_utc = scheduled_at.astimezone(pytz.UTC).replace(tzinfo=None)

    # Create scheduled post
    scheduled_post = ScheduledPost(
        draft_id=draft.id,
        scheduled_at=scheduled_at_utc,
        status=ScheduledPostStatus.PLANNED,
    )
    session.add(scheduled_post)

    draft.status = DraftStatus.SCHEDULED
    await session.commit()

    time_str = scheduled_at.strftime("%d.%m.%Y %H:%M")

    await answer_nav(
        callback=callback,
        label=f"⏰ +{hours} ч.",
        new_text=(
            f"✅ <b>Пост запланирован!</b>\n\n"
            f"Время публикации: {time_str} ({draft.managed_channel.timezone})\n\n"
            f"Пост будет опубликован автоматически."
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("schedule:custom:"))
async def schedule_custom_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start custom date/time input."""
    draft_id = int(callback.data.split(":")[-1])

    await state.set_state(ScheduleStates.waiting_for_datetime)
    await state.update_data(draft_id=draft_id)

    await answer_nav(
        callback=callback,
        label="📅 Выбрать время",
        new_text=(
            "📆 <b>Выбор даты и времени</b>\n\n"
            "Отправь дату и время в формате:\n"
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\n"
            "<i>Примеры:</i>\n"
            "• <code>15.12.2024 18:00</code>\n"
            "• <code>20.12.2024 14:30</code>\n\n"
            "⏰ Время указывается по МСК (или таймзоне канала)"
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.message(ScheduleStates.waiting_for_datetime)
async def process_custom_datetime(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Process custom datetime input."""
    data = await state.get_data()
    draft_id = data.get("draft_id")

    result = await session.execute(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(selectinload(Draft.managed_channel))
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await message.answer("❌ Черновик не найден")
        await state.clear()
        return

    # Parse datetime
    try:
        # Try format: DD.MM.YYYY HH:MM
        dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Используй: ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Например: 15.12.2024 18:00"
        )
        return

    # Apply timezone
    tz = pytz.timezone(draft.managed_channel.timezone)
    dt_tz = tz.localize(dt)

    # Check if in the future
    now = datetime.now(tz)
    if dt_tz <= now:
        await message.answer(
            "❌ Время должно быть в будущем. Попробуй ещё раз."
        )
        return

    # Convert to UTC
    scheduled_at_utc = dt_tz.astimezone(pytz.UTC).replace(tzinfo=None)

    # Create or update scheduled post
    result = await session.execute(
        select(ScheduledPost).where(ScheduledPost.draft_id == draft.id)
    )
    scheduled_post = result.scalar_one_or_none()

    if scheduled_post:
        # Update existing
        scheduled_post.scheduled_at = scheduled_at_utc
        scheduled_post.status = ScheduledPostStatus.PLANNED
        scheduled_post.error_message = None
        scheduled_post.retry_count = 0
    else:
        # Create new
        scheduled_post = ScheduledPost(
            draft_id=draft.id,
            scheduled_at=scheduled_at_utc,
            status=ScheduledPostStatus.PLANNED,
        )
        session.add(scheduled_post)

    draft.status = DraftStatus.SCHEDULED
    await session.commit()

    await state.clear()

    time_str = dt_tz.strftime("%d.%m.%Y %H:%M")

    await message.answer(
        f"✅ <b>Пост запланирован!</b>\n\n"
        f"Время публикации: {time_str} ({draft.managed_channel.timezone})\n\n"
        f"Пост будет опубликован автоматически.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("schedule:view:"))
async def view_scheduled_post(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """View scheduled post details."""
    post_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ScheduledPost)
        .where(ScheduledPost.id == post_id)
        .options(
            selectinload(ScheduledPost.draft).selectinload(Draft.managed_channel)
        )
    )
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("❌ Публикация не найдена", show_alert=True)
        return

    tz = pytz.timezone(post.draft.managed_channel.timezone)
    scheduled_time = post.scheduled_at.replace(tzinfo=pytz.UTC).astimezone(tz)
    time_str = scheduled_time.strftime("%d.%m.%Y %H:%M")

    from app.utils import strip_html
    import html

    # Safe title
    safe_title = html.escape(post.draft.title or 'Без заголовка')
    
    # Safe preview (strip tags then truncate)
    raw_content = strip_html(post.draft.content)
    preview = raw_content[:300] + ('...' if len(raw_content) > 300 else '')
    safe_preview = html.escape(preview)

    await answer_nav(
        callback=callback,
        label=post.draft.title or "Публикация",
        new_text=(
            f"📅 <b>Запланированная публикация</b>\n\n"
            f"<b>{safe_title}</b>\n\n"
            f"{safe_preview}\n\n"
            f"⏰ Время: {time_str} ({post.draft.managed_channel.timezone})"
        ),
        reply_markup=scheduled_post_view_keyboard(post.id, post.draft.id),
    )


@router.callback_query(F.data.startswith("schedule:publish_now:"))
async def publish_scheduled_now(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Publish scheduled post immediately."""
    from aiogram.types import InputMediaPhoto

    post_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ScheduledPost)
        .where(ScheduledPost.id == post_id)
        .options(
            selectinload(ScheduledPost.draft).selectinload(Draft.media),
            selectinload(ScheduledPost.draft).selectinload(Draft.managed_channel),
        )
    )
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("❌ Публикация не найдена", show_alert=True)
        return

    draft = post.draft
    channel = draft.managed_channel

    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🚀 Опубликовать сейчас")
    
    # 3. Status
    progress_msg = await callback.message.answer("🚀 Публикую...")

    try:
        # Publish
        # Publish
        await publish_content(bot, channel.tg_channel_id, draft)

        # Update statuses
        post.status = ScheduledPostStatus.SENT
        post.sent_at = datetime.utcnow()
        draft.status = DraftStatus.PUBLISHED
        await session.commit()

        await progress_msg.delete()
        await callback.message.answer(
            f"✅ <b>Пост опубликован!</b>\n\n"
            f"Канал: {channel.title}",
            reply_markup=back_to_menu_keyboard(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Failed to publish scheduled post {post_id}: {e}")

        post.status = ScheduledPostStatus.ERROR
        post.error_message = str(e)
        await session.commit()
        
        await progress_msg.delete()
        await callback.message.answer(
            f"❌ Ошибка публикации: {e}",
            reply_markup=scheduled_post_view_keyboard(post.id, draft.id),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("schedule:cancel:"))
async def cancel_scheduled_post(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Cancel scheduled post."""
    post_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ScheduledPost)
        .where(ScheduledPost.id == post_id)
        .options(selectinload(ScheduledPost.draft))
    )
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("❌ Публикация не найдена", show_alert=True)
        return

    # Change draft status back to ready
    post.draft.status = DraftStatus.READY

    # Delete scheduled post
    await session.delete(post)
    await session.commit()

    # Logic:
    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("❌ Отменить публикацию")

    # 3. List
    await list_scheduled_posts(callback, session)


@router.callback_query(F.data.startswith("schedule:reschedule:"))
async def reschedule_post(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Reschedule post (change time)."""
    post_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ScheduledPost).where(ScheduledPost.id == post_id)
    )
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("❌ Публикация не найдена", show_alert=True)
        return

    await state.set_state(ScheduleStates.waiting_for_datetime)
    await state.update_data(draft_id=post.draft_id)

    await answer_nav(
        callback=callback,
        label="📅 Изменить время",
        new_text=(
            "📆 <b>Изменение времени публикации</b>\n\n"
            "Отправь новую дату и время в формате:\n"
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\n"
            "<i>Примеры:</i>\n"
            "• <code>15.12.2024 18:00</code>\n"
            "• <code>20.12.2024 14:30</code>"
        ),
        reply_markup=back_to_menu_keyboard(),
    )
