"""Start command and main menu handler."""

from __future__ import annotations

import logging
from typing import Union

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BotCommand
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, ManagedChannel
from app.handlers.keyboards import main_menu_keyboard, back_to_menu_keyboard
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="start")


def get_welcome_text(user: User) -> str:
    """Generate the main welcome / menu text."""
    return (
        f"👋 Привет, {user.first_name or 'друг'}!\n\n"
        "Я — <b>Content Zavod</b>, твой помощник в создании контента для Telegram-каналов.\n\n"
        "🔹 Добавь свой канал\n"
        "🔹 Укажи источники вдохновения (доноры)\n"
        "🔹 Генерируй идеи и посты с помощью AI\n"
        "🔹 Планируй публикации\n\n"
        "Начни с добавления канала 👇"
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle /start command."""
    # Set persistent menu command
    await message.bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
    ])

    # Clear any existing state
    await state.clear()

    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    # Find or create user
    result = await session.execute(
        select(User).where(User.tg_user_id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            tg_user_id=user_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
        await session.commit()
        logger.info(f"New user registered: {user_id} (@{username})")
    elif user.username != username:
        # Update username if changed
        user.username = username
        await session.commit()

    await message.answer(
        get_welcome_text(user),
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:main")
async def show_main_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Show main menu."""
    await state.clear()

    # Need to fetch user for correct info
    result = await session.execute(
        select(User).where(User.tg_user_id == callback.from_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return

    # Get current channel name if any
    channel_name = None
    if user.current_channel_id:
        result = await session.execute(
            select(ManagedChannel.title).where(ManagedChannel.id == user.current_channel_id)
        )
        channel_name = result.scalar_one_or_none()

    await answer_nav(
        callback=callback,
        label="⬅️ Главное меню",
        new_text=get_welcome_text(user),
        reply_markup=main_menu_keyboard(channel_name),
    )


@router.message(F.text == "/guide")
@router.callback_query(F.data == "menu:guide")
async def show_guide(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
) -> None:
    """Show quick guide with bot capabilities."""
    await state.clear()

    guide_text = (
        "📖 <b>Краткий гайд по возможностям</b>\n\n"
        "<b>📢 Каналы</b>\n"
        "Добавьте бота админом канала, затем привяжите его в боте.\n\n"
        "<b>📚 Доноры</b>\n"
        "Укажите каналы-конкуренты. Бот проанализирует их лучшие посты.\n\n"
        "<b>💡 Идеи с доноров</b>\n"
        "AI выбирает топовые темы из постов доноров и предлагает их вам.\n\n"
        "<b>🧠 Свои идеи</b>\n"
        "Отправьте свои материалы (текст, голосовые, фото) — бот превратит их в идеи.\n\n"
        "<b>📁 Пространства</b>\n"
        "Создавайте тематические папки для материалов. Загружайте Word, Excel, PDF, ссылки, аудио.\n\n"
        "<b>📅 Контент-план</b>\n"
        "Генерация плана публикаций на неделю с автоматическим планированием.\n\n"
        "<b>✏️ Черновики</b>\n"
        "Редактируйте посты вручную или с помощью AI. Добавляйте фото.\n\n"
        "<b>🗓 Расписание</b>\n"
        "Планируйте публикации на любое время. Бот опубликует автоматически.\n\n"
        "<b>📊 Аналитика</b>\n"
        "Отслеживайте изменение подписчиков канала.\n\n"
        "<b>🎨 AI Фото</b>\n"
        "Генерируйте картинки для постов через нейросеть.\n\n"
        "💡 <i>Совет: начните с добавления канала и пары доноров!</i>"
    )

    if isinstance(event, Message):
        await event.answer(guide_text, reply_markup=back_to_menu_keyboard(), parse_mode="HTML")
    else:
        await answer_nav(
            callback=event,
            label="📖 Гайд",
            new_text=guide_text,
            reply_markup=back_to_menu_keyboard(),
        )


@router.message(F.text == "/help")
@router.callback_query(F.data == "menu:help")
async def show_help(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
) -> None:
    """Show help message."""
    await state.clear()

    help_text = (
        "📚 <b>Как пользоваться ботом</b>\n\n"
        "<b>1. Начало работы</b>\n"
        "Сначала добавь свой канал:\n"
        "• Добавь бота в администраторы канала\n"
        "• В боте нажми «Выбрать канал» → «Добавить канал»\n"
        "• Пришли ссылку на канал (например @mychannel)\n\n"
        "<b>2. Источники идей (Доноры)</b>\n"
        "Чтобы бот генерировал крутые идеи:\n"
        "• Зайди в «Доноры» → «Добавить донора»\n"
        "• Пришли ссылку на популярный канал в твоей нише\n"
        "• Бот проанализирует их лучшие посты\n\n"
        "<b>3. Создание контента</b>\n"
        "• <b>Идеи:</b> Жми «Идеи» → «Сгенерировать». Выбери лучшую и жми «Написать пост».\n"
        "• <b>Черновики:</b> Можно писать посты вручную или редактировать сгенерированные.\n"
        "• <b>AI Редактор:</b> В черновике жми «AI редактирование» и попроси бота: «сделай короче», «добавь юмора».\n\n"
        "<b>4. Картинки</b>\n"
        "В меню черновика жми «AI Фото», чтобы создать картинку через нейросеть, или «Добавить фото», чтобы загрузить свою.\n\n"
        "<b>5. Публикация</b>\n"
        "• «Опубликовать» — пост выйдет сразу.\n"
        "• «Запланировать» — выбери время, и бот сам опубликует пост."
    )

    if isinstance(event, Message):
        await event.answer(help_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    else:
        await answer_nav(
            callback=event,
            label="ℹ️ Помощь",
            new_text=help_text,
            reply_markup=main_menu_keyboard(),
        )
