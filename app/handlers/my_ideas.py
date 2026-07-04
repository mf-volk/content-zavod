
from __future__ import annotations

import logging
import os
from typing import List

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ContentType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ManagedChannel, Idea, User
from app.handlers.keyboards import main_menu_keyboard, ideas_list_keyboard
from app.llm_client import llm_client
from app.config import settings

logger = logging.getLogger(__name__)
router = Router(name="my_ideas")

class MyIdeasInput(StatesGroup):
    waiting_for_input = State()

@router.callback_query(F.data == "my_ideas:start")
async def start_my_ideas(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Start 'My Ideas' collection mode."""
    await state.clear()
    
    # Check if user has selected channel
    user_id = callback.from_user.id
    result = await session.execute(
        select(ManagedChannel)
        .join(User, User.current_channel_id == ManagedChannel.id)
        .where(User.tg_user_id == user_id)
    )
    channel = result.scalars().first()
    
    if not channel:
        await callback.answer("⚠️ Сначала выберите канал в меню!", show_alert=True)
        return

    await state.update_data(channel_id=channel.id, inputs=[], channel_title=channel.title)
    await state.set_state(MyIdeasInput.waiting_for_input)
    
    text = (
        "🎤 <b>Голосовые идеи</b>\n\n"
        "Запишите голосовое сообщение с вашей идеей — "
        "я превращу её в готовую тему для поста.\n\n"
        "Можно отправить до 5 голосовых подряд.\n\n"
        "Когда закончите — <b>/done</b>\n"
        "Для отмены — <b>/cancel</b>"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML")


@router.message(MyIdeasInput.waiting_for_input, Command("cancel"))
async def cancel_my_ideas(message: Message, state: FSMContext):
    """Cancel 'My Ideas' mode and return to main menu."""
    data = await state.get_data()
    inputs = data.get("inputs", [])

    # Cleanup files
    for inp in inputs:
        if "path" in inp and os.path.exists(inp["path"]):
            try:
                os.remove(inp["path"])
            except:
                pass

    await state.clear()

    await message.answer(
        "✅ Режим «Свои идеи» отменён.\n\nВозвращаюсь в главное меню.",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(MyIdeasInput.waiting_for_input, Command("done"))
async def finish_collection(message: Message, state: FSMContext, session: AsyncSession):
    """Process all inputs and generate ideas."""
    data = await state.get_data()
    inputs = data.get("inputs", [])
    channel_id = data.get("channel_id")
    channel_title = data.get("channel_title")
    
    if not inputs:
        await message.answer("⚠️ Вы ничего не отправили. Пришлите материалы или нажмите /cancel для выхода.")
        return

    status_msg = await message.answer("⏳ <b>Обрабатываю материалы и генерирую идеи...</b>\nЭто может занять минуту.")
    
    try:
        # Process inputs using LLM Client
        generated_ideas = await llm_client.generate_ideas_from_inputs(
             inputs=inputs, 
             channel_title=channel_title
        )
        
        if not generated_ideas:
            await status_msg.edit_text("⚠️ Не удалось извлечь идеи из материалов. Попробуйте снова или добавьте деталей.")
            await state.clear()
            return

        # Save to DB
        saved_ideas = []
        for idea_dto in generated_ideas:
            new_idea = Idea(
                managed_channel_id=channel_id,
                title=idea_dto.title,
                description=idea_dto.description,
                source="user_input",
            )
            session.add(new_idea)
            await session.flush()
            saved_ideas.append(new_idea)
            
        await session.commit()
        
        # Prepare response - match ideas.py format
        ideas_list = [(idea.id, idx + 1) for idx, idea in enumerate(saved_ideas)]

        # Format like ideas:list handler
        full_message_text = f"✅ <b>Готово! Вот ваши идеи:</b>\n\nКанал: {channel_title}\n\n" + ("_" * 30) + "\n\n"

        for idx, idea in enumerate(saved_ideas, 1):
            full_message_text += (
                f"<b>Идея {idx}:</b>\n"
                f"⚡ {idea.title}\n\n"
                f"{idea.description}\n\n"
                f"{'_' * 30}\n\n"
            )

        full_message_text += "👇 <b>Выбери для создания поста:</b>"

        await status_msg.edit_text(
            full_message_text,
            reply_markup=ideas_list_keyboard(ideas_list, source="voice"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        
    except Exception as e:
        logger.error(f"Error processing my ideas: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ Произошла ошибка при обработке: {e}")
    finally:
        await state.clear()
        
        # Cleanup files
        for inp in inputs:
            if "path" in inp and os.path.exists(inp["path"]):
                try:
                    os.remove(inp["path"])
                except:
                    pass


@router.message(MyIdeasInput.waiting_for_input, F.content_type == ContentType.VOICE)
async def collect_voice_input(message: Message, state: FSMContext, bot: Bot):
    """Collect voice messages only."""
    data = await state.get_data()
    inputs = data.get("inputs", [])

    # Check limit
    if len(inputs) >= 5:
        await message.answer(
            "⚠️ Достигнут лимит (5 голосовых).\n"
            "Нажмите /done для генерации идей.",
            parse_mode="HTML"
        )
        return

    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path

    local_path = f"downloads/voice_{file_id}.ogg"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file_path, local_path)

    inputs.append({"type": "voice", "path": local_path})
    count = len(inputs)
    await state.update_data(inputs=inputs)

    await message.answer(
        f"✅ <b>Голосовое принято!</b> ({count}/5)\n\n"
        "Можете записать ещё или нажмите /done",
        parse_mode="HTML"
    )


@router.message(MyIdeasInput.waiting_for_input)
async def reject_non_voice(message: Message):
    """Reject non-voice messages in voice mode."""
    if message.text and message.text.startswith("/"):
        return  # Let command handlers process this

    await message.answer(
        "🎤 В этом режиме принимаю только голосовые сообщения.\n\n"
        "Для документов используйте «📁 Мои документы».",
        parse_mode="HTML"
    )
