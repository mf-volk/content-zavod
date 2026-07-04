"""Channel management handlers."""

from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.enums import ChatMemberStatus
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, ManagedChannel
from app.handlers.keyboards import (
    channels_list_keyboard,
    channel_settings_keyboard,
    back_to_menu_keyboard,
    tov_options_keyboard,
    default_text_keyboard,
)
from app.handlers.states import ChannelStates
from app.llm_client import llm_client
from app import donor_parser

logger = logging.getLogger(__name__)
router = Router(name="channels")



async def get_user(session: AsyncSession, tg_user_id: int) -> Optional[User]:
    """Get user by Telegram ID."""
    result = await session.execute(
        select(User).where(User.tg_user_id == tg_user_id)
    )
    return result.scalar_one_or_none()


from app.utils import answer_nav, sanitize_html

@router.callback_query(F.data == "channels:list")
async def list_channels(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show user's channels list."""
    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Get user's channels
    result = await session.execute(
        select(ManagedChannel)
        .where(ManagedChannel.owner_id == user.id)
        .order_by(ManagedChannel.title)
    )
    channels = result.scalars().all()

    if not channels:
        text = (
            "📢 <b>Мои каналы</b>\n\n"
            "У тебя пока нет добавленных каналов.\n"
            "Нажми кнопку ниже, чтобы добавить первый канал."
        )
    else:
        text = (
            "📢 <b>Мои каналы</b>\n\n"
            "Выбери канал для работы или добавь новый:"
        )

    channel_list = [(c.id, c.title) for c in channels]

    await answer_nav(
        callback=callback,
        label="📢 Мои каналы",
        new_text=text,
        reply_markup=channels_list_keyboard(channel_list, user.current_channel_id),
    )


@router.callback_query(F.data == "channels:add")
async def add_channel_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Start adding a new channel."""
    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.set_state(ChannelStates.waiting_for_channel)

    await answer_nav(
        callback=callback,
        label="➕ Добавить канал",
        new_text=(
            "➕ <b>Добавление канала</b>\n\n"
            "Перешли мне сообщение из канала или отправь ссылку на канал.\n\n"
            "⚠️ <b>Важно:</b> Я должен быть администратором этого канала "
            "с правами на публикацию сообщений."
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.message(ChannelStates.waiting_for_channel)
async def process_channel_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Process channel input (forward or link)."""
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка: пользователь не найден")
        return

    channel_id = None
    channel_title = None
    channel_username = None

    # Check if it's a forwarded message from channel
    if message.forward_from_chat:
        chat = message.forward_from_chat
        if chat.type == "channel":
            channel_id = chat.id
            channel_title = chat.title
            channel_username = chat.username
    # Check if it's a link
    elif message.text:
        text = message.text.strip()

        # Parse @username or https://t.me/username
        if text.startswith("@"):
            channel_username = text[1:]
        elif "t.me/" in text:
            channel_username = text.split("t.me/")[-1].split("/")[0].strip()

        if channel_username:
            try:
                chat = await bot.get_chat(f"@{channel_username}")
                # Allow both 'channel' and 'supergroup'
                if chat.type in ["channel", "supergroup"]:
                    channel_id = chat.id
                    channel_title = chat.title
                else:
                    await message.answer(
                        f"❌ Это {chat.type}, а нужен канал или супергруппа.",
                        reply_markup=back_to_menu_keyboard(),
                    )
                    return
            except Exception as e:
                logger.warning(f"Failed to get channel @{channel_username}: {e}")
                await message.answer(
                    "❌ Не удалось найти канал. Проверь ссылку и попробуй снова.",
                    reply_markup=back_to_menu_keyboard(),
                )
                return

    if not channel_id:
        await message.answer(
            "❌ Не удалось определить канал. "
            "Перешли сообщение из канала или отправь ссылку @username.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # Check if bot is admin
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if bot_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await message.answer(
                "❌ Я не являюсь администратором этого канала.\n\n"
                "Добавь меня как администратора с правами на публикацию сообщений.",
                reply_markup=back_to_menu_keyboard(),
            )
            return
    except Exception as e:
        logger.warning(f"Failed to check bot status in channel {channel_id}: {e}")
        await message.answer(
            "❌ Не удалось проверить права в канале. "
            "Убедись, что я добавлен как администратор.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # Check if user is admin in this channel
    try:
        user_member = await bot.get_chat_member(channel_id, message.from_user.id)
        is_user_admin = user_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except Exception as e:
        logger.warning(f"Failed to check user admin status in channel {channel_id}: {e}")
        is_user_admin = False

    if not is_user_admin:
        await message.answer(
            "❌ Ты не являешься администратором этого канала.\n\n"
            "Только администраторы канала могут добавить его в бота.",
            reply_markup=back_to_menu_keyboard(),
        )
        await state.clear()
        return

    # Check if channel already exists
    result = await session.execute(
        select(ManagedChannel).where(ManagedChannel.tg_channel_id == channel_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Channel exists - allow any admin to use it
        user.current_channel_id = existing.id
        await session.commit()
        await state.clear()

        await message.answer(
            f"✅ Канал <b>{existing.title}</b> выбран!\n\n"
            "Теперь ты можешь работать с этим каналом.",
            reply_markup=channel_settings_keyboard(existing.id),
            parse_mode="HTML",
        )
        return

    # Create channel
    channel = ManagedChannel(
        tg_channel_id=channel_id,
        title=channel_title or "Без названия",
        username=channel_username,
        owner_id=user.id,
    )
    session.add(channel)
    await session.flush()  # Generate ID

    # Set as current channel
    user.current_channel_id = channel.id
    await session.commit()

    await state.clear()
    
    # helper vars
    is_supergroup = (chat.type == "supergroup")
    chat_type_label = "Группа" if is_supergroup else "Канал"

    # Prepare success message
    msg_text = (
        f"✅ {chat_type_label} <b>{channel.title}</b> успешно добавлен!\n\n"
        "Теперь ты можешь:\n"
        "• Добавить доноров для анализа\n"
        "• Настроить стиль (Tone of Voice)\n"
        "• Генерировать идеи и посты"
    )

    # Add specific tip for supergroups
    if is_supergroup:
        msg_text += (
            "\n\n💡 <b>Совет:</b> Чтобы бот писал от имени группы (с аватаркой группы), "
            "зайди в <b>Управление группой -> Администраторы -> Бот</b> "
            "и включи галочку <b>'Анонимность' (Remain Anonymous)</b>."
        )

    await message.answer(
        msg_text,
        reply_markup=channel_settings_keyboard(channel.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("channels:select:"))
async def select_channel(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Select channel as current."""
    channel_id = int(callback.data.split(":")[-1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    # Check ownership
    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    user.current_channel_id = channel_id
    await session.commit()

    await callback.answer(f"✅ Выбран канал: {channel.title}")

    # Refresh list
    await list_channels(callback, session)


@router.callback_query(F.data.startswith("channels:settings:"))
async def show_channel_settings(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show channel settings menu."""
    channel_id = int(callback.data.split(":")[-1])

    # Check ownership
    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    import pytz
    tz = pytz.timezone(channel.timezone)
    from datetime import datetime
    now_local = datetime.now(tz)
    tz_abbr = now_local.strftime('%Z')

    await answer_nav(
        callback=callback,
        label=f"⚙️ {channel.title}",
        new_text=(
            f"⚙️ <b>Настройки канала</b>\n\n"
            f"<b>{channel.title}</b>\n"
            f"ID: <code>{channel.tg_channel_id}</code>\n"
            f"Username: @{channel.username or 'нет'}\n\n"
            f"🎨 <b>Стиль (ToV):</b>\n"
            f"<i>{channel.tone_of_voice or 'Не настроен (используется нейтральный)'}</i>\n\n"
            f"🌍 <b>Часовой пояс:</b> {channel.timezone} ({tz_abbr})"
        ),
        reply_markup=channel_settings_keyboard(channel.id),
    )


@router.callback_query(F.data.startswith("channels:timezone:"))
async def show_timezone_options(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show timezone selection options."""
    import pytz
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    channel_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ManagedChannel).where(ManagedChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    # Popular timezones for Russian-speaking users
    popular_timezones = [
        ("Europe/Moscow", "Москва (MSK, UTC+3)"),
        ("Europe/Samara", "Самара (SAMT, UTC+4)"),
        ("Asia/Yekaterinburg", "Екатеринбург (YEKT, UTC+5)"),
        ("Asia/Novosibirsk", "Новосибирск (NOVT, UTC+7)"),
        ("Asia/Krasnoyarsk", "Красноярск (KRAT, UTC+7)"),
        ("Asia/Irkutsk", "Иркутск (IRKT, UTC+8)"),
        ("Asia/Yakutsk", "Якутск (YAKT, UTC+9)"),
        ("Asia/Vladivostok", "Владивосток (VLAT, UTC+10)"),
        ("Europe/Kaliningrad", "Калининград (EET, UTC+2)"),
        ("Europe/Kiev", "Киев (EET, UTC+2)"),
        ("Europe/Minsk", "Минск (MSK, UTC+3)"),
        ("Asia/Almaty", "Алматы (ALMT, UTC+6)"),
        ("Asia/Tashkent", "Ташкент (UZT, UTC+5)"),
        ("Europe/London", "Лондон (GMT, UTC+0)"),
        ("America/New_York", "Нью-Йорк (EST, UTC-5)"),
    ]

    buttons = []
    for tz_name, tz_label in popular_timezones:
        current_marker = " ✓" if channel.timezone == tz_name else ""
        buttons.append([InlineKeyboardButton(
            text=f"{tz_label}{current_marker}",
            callback_data=f"channels:set_timezone:{channel_id}:{tz_name}"
        )])

    buttons.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=f"channels:settings:{channel_id}"
    )])

    await answer_nav(
        callback=callback,
        label="🌍 Часовой пояс",
        new_text=(
            f"🌍 <b>Выберите часовой пояс</b>\n\n"
            f"Текущий: <b>{channel.timezone}</b>\n\n"
            f"Это влияет на время публикации постов по расписанию.\n"
            f"Например, если вы установите \"12:00\", пост выйдет в 12:00 по выбранному часовому поясу."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("channels:set_timezone:"))
async def set_timezone(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Set channel timezone."""
    parts = callback.data.split(":")
    channel_id = int(parts[2])
    new_timezone = ":".join(parts[3:])  # Handle timezones with colons like "America/New_York"

    result = await session.execute(
        select(ManagedChannel).where(ManagedChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    channel.timezone = new_timezone
    await session.commit()

    await callback.answer(f"✅ Часовой пояс изменен на {new_timezone}", show_alert=True)

    # Redirect back to channel settings
    await show_channel_settings(callback, session)


@router.callback_query(F.data.regexp(r"^channels:tov:(\d+)$"))
async def show_tov_options(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show tone of voice onboarding options."""

    channel_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ManagedChannel).where(ManagedChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    await answer_nav(
        callback=callback,
        label="🎨 Настроить стиль (ToV)",
        new_text=(
            "🎨 <b>Настройка стиля (Tone of Voice)</b>\n\n"
            "Можно автоматически выделить стиль из последних постов канала или"
            " задать его вручную.\n\n"
            f"Текущий стиль:\n<i>{channel.tone_of_voice or 'Не настроен'}</i>"
        ),
        reply_markup=tov_options_keyboard(channel.id),
    )


@router.callback_query(F.data.regexp(r"^channels:tov:manual:(\d+)$"))
async def set_tov_manual_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start manual tone of voice input."""
    channel_id = int(callback.data.split(":")[-1])

    await state.set_state(ChannelStates.waiting_for_tov)
    await state.update_data(channel_id=channel_id)

    await answer_nav(
        callback=callback,
        label="🎨 Настроить стиль (ToV)",
        new_text=(
            "🎨 <b>Настройка стиля (Tone of Voice)</b>\n\n"
            "Опиши желаемый стиль постов для этого канала.\n\n"
            "<i>Примеры:</i>\n"
            "• <b>Дружелюбный:</b> много эмодзи, на 'ты', теплый тон, шутки.\n"
            "• <b>Экспертный:</b> сдержанный, без воды, профессиональный сленг, на 'вы'.\n"
            "• <b>Дерзкий:</b> провокационный, короткие фразы, сарказм, обращение на 'ты'.\n"
            "• <b>Новостной:</b> сухие факты, объективность, без эмоций.\n"
            "• <b>Лайфстайл:</b> вдохновляющий, легкий, сторителлинг, много 'я'.\n"
            "• <b>Женский/Заботливый:</b> мягкий, уютный, много поддержки и любви.\n\n"
            "Можешь описать своими словами или скомбинировать стили:\n"
            "<i>'Серьезный эксперт, но иногда шутит'</i>\n\n"
            "Отправь описание стиля:"
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.callback_query(F.data.regexp(r"^channels:tov:auto:(\d+)$"))
async def generate_tov_from_channel(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Generate tone of voice automatically from channel posts."""

    channel_id = int(callback.data.split(":")[-1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    if not channel.username:
        await callback.answer(
            "Для автогенерации нужен публичный @username канала.",
            show_alert=True,
        )
        return

    loading_message = await callback.message.answer(
        "⏳ Собираю последние посты и ищу тон канала..."
    )

    try:
        parsed = await donor_parser.parse_channel(channel.username.lstrip("@"))
    except Exception as e:
        logger.error(f"Failed to parse channel @{channel.username}: {e}")
        parsed = None

    # Handle parsing failure or empty result
    if not parsed or not parsed.posts:
        # Fallback to manual forwarding
        await state.set_state(ChannelStates.waiting_for_tov_forwards)
        await state.update_data(channel_id=channel_id, tov_texts=[])
        
        await loading_message.edit_text(
            "⚠️ <b>Автоматический анализ не удался.</b>\n"
            "(Возможно, это закрытый канал или супергруппа)\n\n"
            "📥 <b>Перешли сюда 3-5 постов</b> из этого канала/группы.\n"
            "Я проанализирую их текст и определю стиль.\n\n"
            "Как закончишь пересылать — нажми кнопку <b>✅ Готово</b>.",
            reply_markup=tov_options_keyboard(channel.id, waiting_for_forwards=True),
            parse_mode="HTML"
        )
        return

    # Prepare posts for LLM
    tone_posts = []
    for post in parsed.posts:
        tone_posts.append(
            {
                "title": post.title,
                "text": post.text,
                "views": post.views,
                "reactions": post.reactions,
            }
        )

    tone = await llm_client.infer_tone_of_voice_from_posts(
        channel_title=channel.title,
        posts=tone_posts,
        language=channel.language,
    )

    if not tone:
        await loading_message.edit_text(
            "❌ Не удалось определить стиль. Попробуй ещё раз или задай его вручную.",
            reply_markup=channel_settings_keyboard(channel.id),
        )
        return

    channel.tone_of_voice = tone
    await session.commit()

    safe_tone = sanitize_html(tone)
    await loading_message.edit_text(
        f"✅ Стиль для канала <b>{channel.title}</b> обновлён!\n\n{safe_tone}",
        reply_markup=channel_settings_keyboard(channel.id),
        parse_mode="HTML",
    )


@router.message(ChannelStates.waiting_for_tov_forwards)
async def process_tov_forwards(
    message: Message,
    state: FSMContext,
) -> None:
    """Accumulate forwarded messages for ToV analysis."""
    # Check if message has text
    text = message.text or message.caption or ""
    if not text:
        await message.answer("⚠️ В этом сообщении нет текста. Перешли пост с текстом.")
        return

    data = await state.get_data()
    tov_texts = data.get("tov_texts", [])
    
    # Add text if unique (simple check)
    if text not in tov_texts:
        tov_texts.append(text)
        await state.update_data(tov_texts=tov_texts)
        
    await message.answer(
        f"📥 Принято постов: <b>{len(tov_texts)}</b>\n\n"
        "Перешли ещё или нажми <b>✅ Готово</b>.",
        reply_markup=tov_options_keyboard(data.get("channel_id"), waiting_for_forwards=True),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "channels:tov:forwards_done")
async def finish_tov_forwards(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Finish collecting forwards and generate ToV."""
    data = await state.get_data()
    channel_id = data.get("channel_id")
    tov_texts = data.get("tov_texts", [])
    
    if not tov_texts:
        await callback.answer("❌ Ты не переслал ни одного поста!", show_alert=True)
        return

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()
    
    if not channel:
        await callback.message.edit_text("❌ Канал не найден")
        await state.clear()
        return

    loading_message = await callback.message.answer(
        f"⏳ Анализирую {len(tov_texts)} постов..."
    )
    
    # Prepare dummy objects for LLM
    tone_posts = []
    for text in tov_texts:
        tone_posts.append({
            "title": "Forwarded Post",
            "text": text,
            "views": 0,
            "reactions": 0
        })

    try:
        tone = await llm_client.infer_tone_of_voice_from_posts(
            channel_title=channel.title,
            posts=tone_posts,
            language=channel.language,
        )
    except Exception as e:
        logger.error(f"LLM ToV error: {e}")
        tone = None

    if not tone:
        await loading_message.edit_text(
            "❌ Не удалось определить стиль. Попробуй задать вручную.",
            reply_markup=channel_settings_keyboard(channel.id),
        )
        return

    channel.tone_of_voice = tone
    await session.commit()
    await state.clear()

    safe_tone = sanitize_html(tone)
    await loading_message.edit_text(
        f"✅ Стиль для канала <b>{channel.title}</b> обновлён (на основе пересланных постов)!\n\n{safe_tone}",
        reply_markup=channel_settings_keyboard(channel.id),
        parse_mode="HTML",
    )


@router.message(ChannelStates.waiting_for_tov)
async def process_tov_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Process tone of voice input."""
    data = await state.get_data()
    channel_id = data.get("channel_id")

    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка")
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await message.answer("❌ Канал не найден")
        await state.clear()
        return

    tone_text = sanitize_html(message.text)
    channel.tone_of_voice = tone_text
    await session.commit()

    await state.clear()

    await message.answer(
        f"✅ Стиль для канала <b>{channel.title}</b> сохранён!\n\n{tone_text}",
        reply_markup=channel_settings_keyboard(channel.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("channels:delete:"))
async def delete_channel(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Delete channel."""
    channel_id = int(callback.data.split(":")[-1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    title = channel.title

    # Clear current channel if this was selected
    if user.current_channel_id == channel_id:
        user.current_channel_id = None

    await session.delete(channel)
    await session.commit()

    # Use answer_nav flow
    # We first answer the callback to remove buttons from the "Are you sure?" or settings menu
    
    # But wait, delete_channel is triggered from a button. 
    # Logic: 
    # 1. Remove old menu buttons
    # 2. Echo "Trash Channel" or similar? Or just notification?
    # The user instruction says: "Echo the action (e.g. '📂 Drafts')".
    # Here the action is "Delete Channel".
    
    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # 2. Echo action
    await callback.message.answer(f"🗑 Удалить канал {title}")

    # 3. Show list again (list_channels uses answer_nav internally, so it will echo "My Channels".
    # Ideally we should just call it.
    await list_channels(callback, session)


# ============================================================
# DEFAULT POST TEXT
# ============================================================


@router.callback_query(F.data.regexp(r"^channels:default_text:(\d+)$"))
async def show_default_text_options(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show default post text settings."""
    channel_id = int(callback.data.split(":")[-1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    has_text = bool(channel.default_post_text)
    pos_label = "В начале" if channel.default_post_text_position == "start" else "В конце"

    if has_text:
        text = (
            "📌 <b>Текст по умолчанию в посты</b>\n\n"
            f"Позиция: <b>{pos_label} поста</b>\n\n"
            f"Текст:\n<code>{channel.default_post_text}</code>\n\n"
            "Этот текст будет автоматически добавлен во все публикуемые посты."
        )
    else:
        text = (
            "📌 <b>Текст по умолчанию в посты</b>\n\n"
            "Здесь можно задать текст (ссылки, подпись и т.д.), "
            "который будет автоматически добавляться в каждый пост.\n\n"
            "Текст сохраняется строго в том виде, как вы его написали."
        )

    await answer_nav(
        callback=callback,
        label="📌 Текст по умолчанию",
        new_text=text,
        reply_markup=default_text_keyboard(channel.id, has_text),
    )


@router.callback_query(F.data.regexp(r"^channels:default_text:edit:(\d+)$"))
async def edit_default_text_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Start editing default post text."""
    channel_id = int(callback.data.split(":")[-1])

    await state.set_state(ChannelStates.waiting_for_default_text)
    await state.update_data(channel_id=channel_id)

    await answer_nav(
        callback=callback,
        label="📌 Текст по умолчанию",
        new_text=(
            "📌 <b>Введите текст по умолчанию</b>\n\n"
            "Отправьте текст, который будет добавляться в каждый пост.\n\n"
            "Это может быть:\n"
            "• Ссылка на ваш канал/сайт\n"
            "• Подпись автора\n"
            "• Хештеги\n"
            "• Любой другой текст\n\n"
            "<i>Текст будет вставлен строго как вы его напишете.</i>"
        ),
        reply_markup=back_to_menu_keyboard(),
    )


@router.message(ChannelStates.waiting_for_default_text)
async def process_default_text_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Process default post text input."""
    data = await state.get_data()
    channel_id = data.get("channel_id")

    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка")
        await state.clear()
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await message.answer("❌ Канал не найден")
        await state.clear()
        return

    # Save the text exactly as user typed it (preserve formatting)
    raw_text = message.text or message.caption or ""
    if not raw_text.strip():
        await message.answer("⚠️ Текст не может быть пустым. Отправьте текст ещё раз.")
        return

    channel.default_post_text = raw_text
    await session.commit()
    await state.clear()

    pos_label = "В начале" if channel.default_post_text_position == "start" else "В конце"

    await message.answer(
        f"✅ <b>Текст по умолчанию сохранён!</b>\n\n"
        f"Позиция: <b>{pos_label} поста</b>\n\n"
        f"Текст:\n<code>{raw_text}</code>",
        reply_markup=default_text_keyboard(channel.id, has_text=True),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^channels:default_text:pos:(start|end):(\d+)$"))
async def set_default_text_position(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Set default text position (start or end of post)."""
    parts = callback.data.split(":")
    position = parts[3]  # "start" or "end"
    channel_id = int(parts[4])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    channel.default_post_text_position = position
    await session.commit()

    pos_label = "В начале" if position == "start" else "В конце"
    await callback.answer(f"✅ Позиция: {pos_label} поста")

    # Refresh view
    has_text = bool(channel.default_post_text)
    text = (
        "📌 <b>Текст по умолчанию в посты</b>\n\n"
        f"Позиция: <b>{pos_label} поста</b>\n\n"
        f"Текст:\n<code>{channel.default_post_text}</code>\n\n"
        "Этот текст будет автоматически добавлен во все публикуемые посты."
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=default_text_keyboard(channel.id, has_text),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.regexp(r"^channels:default_text:clear:(\d+)$"))
async def clear_default_text(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Clear default post text."""
    channel_id = int(callback.data.split(":")[-1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(
        select(ManagedChannel).where(
            ManagedChannel.id == channel_id,
            ManagedChannel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()

    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    channel.default_post_text = None
    await session.commit()

    await callback.answer("✅ Текст по умолчанию удалён")

    text = (
        "📌 <b>Текст по умолчанию в посты</b>\n\n"
        "Здесь можно задать текст (ссылки, подпись и т.д.), "
        "который будет автоматически добавляться в каждый пост.\n\n"
        "Текст сохраняется строго в том виде, как вы его написали."
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=default_text_keyboard(channel.id, has_text=False),
            parse_mode="HTML",
        )
    except Exception:
        pass
