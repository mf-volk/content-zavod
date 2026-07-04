"""Analytics handler for channel statistics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import User, ManagedChannel, ChannelStats
from app.handlers.keyboards import back_to_menu_keyboard
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="analytics")


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


async def collect_current_stats(bot: Bot, channel: ManagedChannel, session: AsyncSession) -> Optional[int]:
    """Collect current subscriber count for channel."""
    try:
        count = await bot.get_chat_member_count(channel.tg_channel_id)

        # Save to DB
        today = datetime.utcnow().date()
        today_datetime = datetime.combine(today, datetime.min.time())

        # Check if we already have stats for today
        result = await session.execute(
            select(ChannelStats).where(
                ChannelStats.channel_id == channel.id,
                func.date(ChannelStats.date) == today,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.subscribers_count = count
        else:
            stats = ChannelStats(
                channel_id=channel.id,
                date=today_datetime,
                subscribers_count=count,
            )
            session.add(stats)

        await session.commit()
        return count

    except Exception as e:
        logger.error(f"Failed to collect stats for channel {channel.id}: {e}")
        return None


def format_change(current: int, previous: int) -> str:
    """Format subscriber change with emoji."""
    diff = current - previous
    if diff > 0:
        return f"📈 +{diff}"
    elif diff < 0:
        return f"📉 {diff}"
    else:
        return "➡️ 0"


# ============================================================
# KEYBOARDS
# ============================================================


def analytics_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    """Keyboard for analytics view."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="analytics:refresh")],
        [InlineKeyboardButton(text="📊 За 7 дней", callback_data="analytics:week")],
        [InlineKeyboardButton(text="📊 За 30 дней", callback_data="analytics:month")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


# ============================================================
# HANDLERS
# ============================================================


@router.callback_query(F.data == "analytics:view")
async def view_analytics(callback: CallbackQuery, session: AsyncSession, bot: Bot, state: FSMContext):
    """Show channel analytics."""
    await state.clear()

    user = await get_user_with_channel(session, callback.from_user.id)
    if not user or not user.current_channel:
        await callback.answer("⚠️ Сначала выберите канал", show_alert=True)
        return

    channel = user.current_channel

    # Collect current stats
    current_count = await collect_current_stats(bot, channel, session)

    if current_count is None:
        await callback.message.edit_text(
            f"📊 <b>Аналитика: {channel.title}</b>\n\n"
            "❌ Не удалось получить статистику.\n"
            "Убедитесь, что бот является администратором канала.",
            reply_markup=back_to_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    # Get stats for comparison
    today = datetime.utcnow().date()

    # Yesterday
    yesterday = today - timedelta(days=1)
    result = await session.execute(
        select(ChannelStats).where(
            ChannelStats.channel_id == channel.id,
            func.date(ChannelStats.date) == yesterday,
        )
    )
    yesterday_stats = result.scalar_one_or_none()

    # Week ago
    week_ago = today - timedelta(days=7)
    result = await session.execute(
        select(ChannelStats).where(
            ChannelStats.channel_id == channel.id,
            func.date(ChannelStats.date) == week_ago,
        )
    )
    week_stats = result.scalar_one_or_none()

    # Month ago
    month_ago = today - timedelta(days=30)
    result = await session.execute(
        select(ChannelStats).where(
            ChannelStats.channel_id == channel.id,
            func.date(ChannelStats.date) == month_ago,
        )
    )
    month_stats = result.scalar_one_or_none()

    # Format text
    text = f"📊 <b>Аналитика: {channel.title}</b>\n\n"
    text += f"👥 <b>Подписчиков сейчас:</b> {current_count:,}\n\n"

    text += "<b>Изменения:</b>\n"

    if yesterday_stats:
        change = format_change(current_count, yesterday_stats.subscribers_count)
        text += f"• За день: {change}\n"
    else:
        text += "• За день: <i>нет данных</i>\n"

    if week_stats:
        change = format_change(current_count, week_stats.subscribers_count)
        text += f"• За 7 дней: {change}\n"
    else:
        text += "• За 7 дней: <i>нет данных</i>\n"

    if month_stats:
        change = format_change(current_count, month_stats.subscribers_count)
        text += f"• За 30 дней: {change}\n"
    else:
        text += "• За 30 дней: <i>нет данных</i>\n"

    text += "\n<i>Статистика собирается ежедневно автоматически.</i>"

    await answer_nav(
        callback=callback,
        label="📊 Аналитика",
        new_text=text,
        reply_markup=analytics_keyboard(channel.id),
    )


@router.callback_query(F.data == "analytics:refresh")
async def refresh_analytics(callback: CallbackQuery, session: AsyncSession, bot: Bot, state: FSMContext):
    """Refresh analytics data."""
    await view_analytics(callback, session, bot, state)
    await callback.answer("✅ Обновлено")


@router.callback_query(F.data == "analytics:week")
async def show_week_stats(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Show 7-day statistics."""
    user = await get_user_with_channel(session, callback.from_user.id)
    if not user or not user.current_channel:
        await callback.answer("⚠️ Сначала выберите канал", show_alert=True)
        return

    channel = user.current_channel

    # Get last 7 days stats
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)

    result = await session.execute(
        select(ChannelStats)
        .where(
            ChannelStats.channel_id == channel.id,
            ChannelStats.date >= datetime.combine(week_ago, datetime.min.time()),
        )
        .order_by(ChannelStats.date.asc())
    )
    stats = result.scalars().all()

    text = f"📊 <b>Статистика за 7 дней: {channel.title}</b>\n\n"

    if not stats:
        text += "<i>Нет данных за этот период.</i>\n"
    else:
        text += "<b>Подписчики по дням:</b>\n"

        prev_count = None
        for stat in stats:
            date_str = stat.date.strftime("%d.%m")
            count = stat.subscribers_count

            if prev_count:
                diff = count - prev_count
                if diff > 0:
                    change = f" (+{diff})"
                elif diff < 0:
                    change = f" ({diff})"
                else:
                    change = ""
            else:
                change = ""

            text += f"• {date_str}: {count:,}{change}\n"
            prev_count = count

        # Summary
        if len(stats) >= 2:
            total_change = stats[-1].subscribers_count - stats[0].subscribers_count
            avg_change = total_change / (len(stats) - 1) if len(stats) > 1 else 0

            text += f"\n<b>Итого за период:</b> {format_change(stats[-1].subscribers_count, stats[0].subscribers_count)}"
            text += f"\n<b>В среднем в день:</b> {avg_change:+.1f}"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="analytics:view")]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "analytics:month")
async def show_month_stats(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Show 30-day statistics."""
    user = await get_user_with_channel(session, callback.from_user.id)
    if not user or not user.current_channel:
        await callback.answer("⚠️ Сначала выберите канал", show_alert=True)
        return

    channel = user.current_channel

    # Get last 30 days stats (weekly snapshots)
    today = datetime.utcnow().date()
    month_ago = today - timedelta(days=30)

    result = await session.execute(
        select(ChannelStats)
        .where(
            ChannelStats.channel_id == channel.id,
            ChannelStats.date >= datetime.combine(month_ago, datetime.min.time()),
        )
        .order_by(ChannelStats.date.asc())
    )
    all_stats = result.scalars().all()

    text = f"📊 <b>Статистика за 30 дней: {channel.title}</b>\n\n"

    if not all_stats:
        text += "<i>Нет данных за этот период.</i>\n"
    else:
        # Show weekly data points
        text += "<b>Понедельная динамика:</b>\n"

        # Group by weeks
        weeks = {}
        for stat in all_stats:
            week_num = stat.date.isocalendar()[1]
            if week_num not in weeks:
                weeks[week_num] = stat
            else:
                # Take latest in the week
                if stat.date > weeks[week_num].date:
                    weeks[week_num] = stat

        prev_count = None
        for week_num, stat in sorted(weeks.items()):
            date_str = stat.date.strftime("%d.%m")
            count = stat.subscribers_count

            if prev_count:
                diff = count - prev_count
                if diff > 0:
                    change = f" (+{diff})"
                elif diff < 0:
                    change = f" ({diff})"
                else:
                    change = ""
            else:
                change = ""

            text += f"• Неделя {week_num} ({date_str}): {count:,}{change}\n"
            prev_count = count

        # Summary
        if len(all_stats) >= 2:
            total_change = all_stats[-1].subscribers_count - all_stats[0].subscribers_count
            text += f"\n<b>Итого за месяц:</b> {format_change(all_stats[-1].subscribers_count, all_stats[0].subscribers_count)}"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="analytics:view")]
        ]),
        parse_mode="HTML",
    )
