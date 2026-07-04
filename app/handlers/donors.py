"""Donor channels management handlers."""

from __future__ import annotations

import logging
from typing import Optional
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import User, ManagedChannel, DonorChannel, DonorPost, DonorStatus
from app.donor_parser import parse_channel
from app.handlers.keyboards import (
    donors_list_keyboard,
    donor_view_keyboard,
    back_to_menu_keyboard,
)
from app.handlers.states import DonorStates

logger = logging.getLogger(__name__)
router = Router(name="donors")


async def get_current_channel(session: AsyncSession, tg_user_id: int) -> Optional[ManagedChannel]:
    """Get user's current selected channel."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.current_channel))
    )
    user = result.scalar_one_or_none()
    return user.current_channel if user else None


from app.utils import answer_nav

@router.callback_query(F.data == "donors:list")
async def list_donors(
    callback: CallbackQuery,
    session: AsyncSession,
    nav_label: str = "📚 Доноры",  # Allow override
) -> None:
    """Show donors list for current channel."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await answer_nav(
            callback=callback,
            label=nav_label,
            new_text="⚠️ Сначала выбери канал для работы.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # Get donors
    result = await session.execute(
        select(DonorChannel)
        .where(DonorChannel.managed_channel_id == channel.id)
        .order_by(DonorChannel.username)
    )
    donors = result.scalars().all()

    if not donors:
        text = (
            f"📚 <b>Доноры для канала {channel.title}</b>\n\n"
            "Доноры — это каналы, контент которых используется для вдохновения "
            "при генерации идей.\n\n"
            "Добавь первый донорский канал:"
        )
    else:
        text = (
            f"📚 <b>Доноры для канала {channel.title}</b>\n\n"
            f"Всего доноров: {len(donors)}\n\n"
            "Выбери донора для управления:"
        )

    donor_list = [(d.id, d.username, d.status.value) for d in donors]

    await answer_nav(
        callback=callback,
        label=nav_label,
        new_text=text,
        reply_markup=donors_list_keyboard(donor_list),
    )


@router.callback_query(F.data == "donors:add")
async def add_donor_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Start adding a donor."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.answer("⚠️ Сначала выбери канал", show_alert=True)
        return

    await state.set_state(DonorStates.waiting_for_donor_link)

    await answer_nav(
        callback=callback,
        label="➕ Добавить донора",
        new_text=(
            "➕ <b>Добавление донора</b>\n\n"
            "Отправь ссылку на публичный Telegram-канал.\n"
            "Можно добавить несколько каналов, отправив их списком (каждый с новой строки).\n\n"
            "<i>Примеры:</i>\n"
            "• @channel_username\n"
            "• https://t.me/channel_username\n\n"
            "⚠️ Канал должен быть публичным."
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.message(DonorStates.waiting_for_donor_link)
async def process_donor_link(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Process donor channel link(s)."""
    channel = await get_current_channel(session, message.from_user.id)

    if not channel:
        await message.answer("❌ Канал не выбран")
        await state.clear()
        return

    # Split by newlines or commas/spaces if needed, but newlines is best for lists
    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    
    if not lines:
        await message.answer("⚠️ Пустое сообщение. Пришлите ссылки.")
        return

    await message.answer(f"🔄 Обрабатываю {len(lines)} каналов...")

    added_count = 0
    existing_count = 0
    failed_names = []

    last_added_donor_id = None

    for line in lines:
        # Extract username
        username = None
        if line.startswith("@"):
            username = line[1:]
        elif "t.me/" in line:
            # Handle forms like https://t.me/username and t.me/username
            parts = line.split("t.me/")
            if len(parts) > 1:
                username = parts[-1].split("/")[0].split("?")[0].strip()
        else:
            username = line # Assume username if just text

        if not username:
            failed_names.append(f"{line} (формат)")
            continue
            
        username = username.lower()

        # Check existing
        try:
            result = await session.execute(
                select(DonorChannel).where(
                    DonorChannel.managed_channel_id == channel.id,
                    DonorChannel.username == username,
                )
            )
            if result.scalar_one_or_none():
                existing_count += 1
                continue

            # Parse
            parsed = await parse_channel(username)
            if not parsed:
                failed_names.append(f"@{username} (не найден/закрыт)")
                continue

            # Add to DB
            donor = DonorChannel(
                managed_channel_id=channel.id,
                username=username,
                title=None,
                subscribers_count=parsed.subscribers_count,
                status=DonorStatus.ACTIVE,
                last_parsed_at=datetime.utcnow(),
            )
            session.add(donor)
            await session.flush()
            last_added_donor_id = donor.id

            # Save posts
            for post in parsed.posts:
                donor_post = DonorPost(
                    donor_id=donor.id,
                    post_id=post.post_id,
                    text=post.text,
                    title=post.title,
                    views=post.views,
                    reactions=post.reactions,
                    published_at=post.published_at,
                )
                session.add(donor_post)
            
            added_count += 1
            
        except Exception as e:
            logger.error(f"Error adding donor {username}: {e}")
            failed_names.append(f"@{username} (ошибка)")

    await session.commit()
    await state.clear()
    
    # Report
    text = (
        f"🏁 <b>Результат загрузки:</b>\n\n"
        f"✅ Добавлено: {added_count}\n"
        f"ℹ️ Уже было: {existing_count}\n"
    )
    
    if failed_names:
        text += f"❌ Ошибки ({len(failed_names)}):\n" + "\n".join(failed_names[:10])
        if len(failed_names) > 10:
            text += f"\n...и еще {len(failed_names) - 10}"

    # Use default donors list view if multiple added, or single view if just 1
    if added_count == 1 and not failed_names and not existing_count and last_added_donor_id:
        # Show the single added donor
        markup = donor_view_keyboard(last_added_donor_id)
        msg_text = f"✅ Донор добавлен!\n\n{text}"
    else:
        # Show summary -> back to list
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(inline_keyboard=[
             [InlineKeyboardButton(text="📚 К списку доноров", callback_data="donors:list")],
             [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")]
        ])
        msg_text = text

    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("donors:view:"))
async def view_donor(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """View single donor."""
    donor_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(DonorChannel)
        .where(DonorChannel.id == donor_id)
        .options(selectinload(DonorChannel.posts))
    )
    donor = result.scalar_one_or_none()

    if not donor:
        await callback.answer("❌ Донор не найден", show_alert=True)
        return

    subs_text = f"{donor.subscribers_count:,}" if donor.subscribers_count else "?"
    last_parsed = donor.last_parsed_at.strftime("%d.%m.%Y %H:%M") if donor.last_parsed_at else "никогда"

    # Get top posts by views
    top_posts = sorted(donor.posts, key=lambda p: p.views, reverse=True)[:3]
    top_posts_text = ""
    if top_posts:
        top_posts_text = "\n\n<b>Топ посты:</b>\n"
        for i, post in enumerate(top_posts, 1):
            title = post.title[:50] + "..." if len(post.title) > 50 else post.title
            top_posts_text += f"{i}. {title} (👁 {post.views:,})\n"

    await answer_nav(
        callback=callback,
        label=f"@{donor.username}",
        new_text=(
            f"📚 <b>@{donor.username}</b>\n\n"
            f"📊 Подписчиков: {subs_text}\n"
            f"📝 Постов в базе: {len(donor.posts)}\n"
            f"🕐 Последнее обновление: {last_parsed}\n"
            f"📍 Статус: {donor.status.value}"
            f"{top_posts_text}"
        ),
        reply_markup=donor_view_keyboard(donor.id),
    )


@router.callback_query(F.data.startswith("donors:parse:"))
async def parse_single_donor(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Parse posts from single donor."""
    donor_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(DonorChannel).where(DonorChannel.id == donor_id)
    )
    donor = result.scalar_one_or_none()

    if not donor:
        await callback.answer("❌ Донор не найден", show_alert=True)
        return
    
    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🔄 Обновить посты")
    
    # 3. Progress (New message)
    progress_msg = await callback.message.answer("⏳ Обновляю посты (это может занять время)...")

    parsed = await parse_channel(donor.username)

    if not parsed:
        donor.status = DonorStatus.ERROR
        await session.commit()
        await progress_msg.delete()
        await callback.message.answer(
            f"❌ Не удалось получить данные канала @{donor.username}",
            reply_markup=donor_view_keyboard(donor.id),
            parse_mode="HTML"
        )
        return

    # Update donor info
    donor.subscribers_count = parsed.subscribers_count
    donor.status = DonorStatus.ACTIVE
    donor.last_parsed_at = datetime.utcnow()

    # Get existing post IDs
    result = await session.execute(
        select(DonorPost.post_id).where(DonorPost.donor_id == donor.id)
    )
    existing_ids = {row[0] for row in result.fetchall()}

    # Add new posts
    new_count = 0
    for post in parsed.posts:
        if post.post_id not in existing_ids:
            donor_post = DonorPost(
                donor_id=donor.id,
                post_id=post.post_id,
                text=post.text,
                title=post.title,
                views=post.views,
                reactions=post.reactions,
                published_at=post.published_at,
            )
            session.add(donor_post)
            new_count += 1

    await session.commit()

    subs_text = f"{parsed.subscribers_count:,}" if parsed.subscribers_count else "?"

    await progress_msg.delete()
    await callback.message.answer(
        f"✅ Данные обновлены!\n\n"
        f"📊 Подписчиков: {subs_text}\n"
        f"📝 Новых постов: {new_count}",
        reply_markup=donor_view_keyboard(donor.id),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "donors:parse_all")
async def parse_all_donors(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Parse all donors for current channel."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.answer("⚠️ Канал не выбран", show_alert=True)
        return

    result = await session.execute(
        select(DonorChannel).where(DonorChannel.managed_channel_id == channel.id)
    )
    donors = result.scalars().all()

    if not donors:
        await callback.answer("Нет доноров для обновления", show_alert=True)
        return

    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🔄 Обновить все")
    
    # 3. Progress
    progress_msg = await callback.message.answer(f"⏳ Обновляю {len(donors)} доноров...")

    # Parse each donor with status updates
    success = 0
    failed = 0

    for donor in donors:
        parsed = await parse_channel(donor.username)

        if parsed:
            donor.subscribers_count = parsed.subscribers_count
            donor.status = DonorStatus.ACTIVE
            donor.last_parsed_at = datetime.utcnow()

            result = await session.execute(
                select(DonorPost.post_id).where(DonorPost.donor_id == donor.id)
            )
            existing_ids = {row[0] for row in result.fetchall()}

            for post in parsed.posts:
                if post.post_id not in existing_ids:
                    donor_post = DonorPost(
                        donor_id=donor.id,
                        post_id=post.post_id,
                        text=post.text,
                        title=post.title,
                        views=post.views,
                        reactions=post.reactions,
                        published_at=post.published_at,
                    )
                    session.add(donor_post)

            success += 1
        else:
            donor.status = DonorStatus.ERROR
            failed += 1

    await session.commit()

    await progress_msg.delete()
    await callback.message.answer(
        f"✅ Обновление завершено!\n\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибки: {failed}",
        reply_markup=donors_list_keyboard(
            [(d.id, d.username, d.status.value) for d in donors]
        ),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("donors:delete:"))
async def delete_donor(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Delete donor."""
    donor_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(DonorChannel).where(DonorChannel.id == donor_id)
    )
    donor = result.scalar_one_or_none()

    if not donor:
        await callback.answer("❌ Донор не найден", show_alert=True)
        return

    username = donor.username
    await session.delete(donor)
    await session.commit()

    # Logic:
    # 1. Remove buttons from old message (view donor)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo action
    await callback.message.answer(f"🗑 Удалить @{username}")
    
    # 3. Show list
    await list_donors(callback, session, nav_label="🗑 Удалить")
