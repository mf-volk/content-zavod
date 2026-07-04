"""YouTube ideas handler - generate ideas from YouTube video transcripts."""

from __future__ import annotations

import re
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ManagedChannel, Idea, User
from app.handlers.keyboards import main_menu_keyboard, ideas_list_keyboard
from app.handlers.states import YouTubeStates
from app.llm_client import llm_client
from app.services.document_processor import fetch_youtube_transcript

logger = logging.getLogger(__name__)
router = Router(name="youtube")

# Regex to extract YouTube video ID from various URL formats
YOUTUBE_URL_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})'
)


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    match = YOUTUBE_URL_PATTERN.search(url)
    return match.group(1) if match else None


async def fetch_transcript(video_url: str) -> str | None:
    """Fetch a YouTube video transcript via the free youtube-transcript-api.

    Delegates to the shared document processor, which pulls public captions
    (no API key required). Returns the transcript text or None if unavailable.
    """
    text, error = await fetch_youtube_transcript(video_url)
    if error:
        logger.warning(f"Transcript unavailable for {video_url}: {error}")
        return None
    return text or None


@router.callback_query(F.data == "youtube:start")
async def start_youtube(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Start YouTube ideas collection mode."""
    user_id = callback.from_user.id

    await state.clear()

    # Check if user has selected channel
    result = await session.execute(
        select(ManagedChannel)
        .join(User, User.current_channel_id == ManagedChannel.id)
        .where(User.tg_user_id == user_id)
    )
    channel = result.scalars().first()

    if not channel:
        await callback.answer("⚠️ Сначала выберите канал в меню!", show_alert=True)
        return

    await state.update_data(
        channel_id=channel.id,
        channel_title=channel.title,
        links=[],
    )
    await state.set_state(YouTubeStates.waiting_for_links)

    text = (
        "▶️ <b>Идеи из YouTube</b>\n\n"
        "Отправьте ссылку на YouTube-видео — "
        "я извлеку содержимое и сгенерирую идеи для постов "
        "в стиле вашего канала.\n\n"
        "Можно отправить до 5 ссылок.\n\n"
        "Когда закончите — <b>/done</b>\n"
        "Для отмены — <b>/cancel</b>"
    )

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@router.message(YouTubeStates.waiting_for_links, Command("cancel"))
async def cancel_youtube(message: Message, state: FSMContext):
    """Cancel YouTube mode."""
    await state.clear()
    await message.answer(
        "✅ Режим «YouTube» отменён.\n\nВозвращаюсь в главное меню.",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(YouTubeStates.waiting_for_links, Command("done"))
async def finish_youtube(message: Message, state: FSMContext, session: AsyncSession):
    """Process all YouTube links and generate ideas."""
    data = await state.get_data()
    links = data.get("links", [])
    channel_id = data.get("channel_id")
    channel_title = data.get("channel_title")

    if not links:
        await message.answer(
            "⚠️ Вы не отправили ни одной ссылки.\n"
            "Отправьте YouTube-ссылку или нажмите /cancel для выхода."
        )
        return

    status_msg = await message.answer(
        f"⏳ <b>Получаю транскрипцию {len(links)} видео и генерирую идеи...</b>\n"
        "Это может занять пару минут — зависит от длины видео.",
        parse_mode="HTML",
    )

    try:
        # Fetch transcripts for all videos
        inputs = []
        failed = []

        for link in links:
            transcript = await fetch_transcript(link)
            if transcript:
                # Truncate very long transcripts (keep first ~4000 words)
                words = transcript.split()
                if len(words) > 4000:
                    transcript = " ".join(words[:4000])
                inputs.append({"type": "text", "content": f"[YouTube видео]: {transcript}"})
            else:
                failed.append(link)

        if not inputs:
            fail_text = "\n".join(f"• {l}" for l in failed)
            await status_msg.edit_text(
                "⚠️ Не удалось получить транскрипцию ни одного видео.\n\n"
                f"Не распознаны:\n{fail_text}\n\n"
                "Возможные причины:\n"
                "• У видео нет субтитров\n"
                "• Видео приватное или удалено\n"
                "• Прямая трансляция (стрим) — субтитры недоступны\n"
                "• Неверная ссылка",
                parse_mode="HTML",
            )
            await state.clear()
            return

        # Generate ideas using LLM with YouTube-specific prompt
        generated_ideas = await llm_client.generate_ideas_from_inputs(
            inputs=inputs,
            channel_title=channel_title,
            source_type="youtube",
        )

        if not generated_ideas:
            await status_msg.edit_text(
                "⚠️ Не удалось сгенерировать идеи из видео. Попробуйте другое видео."
            )
            await state.clear()
            return

        # Save to DB
        saved_ideas = []
        for idea_dto in generated_ideas:
            new_idea = Idea(
                managed_channel_id=channel_id,
                title=idea_dto.title,
                description=idea_dto.description,
                source="youtube",
            )
            session.add(new_idea)
            await session.flush()
            saved_ideas.append(new_idea)

        await session.commit()

        # Format response
        ideas_list = [(idea.id, idx + 1) for idx, idea in enumerate(saved_ideas)]

        full_message_text = (
            f"✅ <b>Готово! Вот идеи из YouTube:</b>\n\n"
            f"Канал: {channel_title}\n\n"
            + ("_" * 30) + "\n\n"
        )

        for idx, idea in enumerate(saved_ideas, 1):
            full_message_text += (
                f"<b>Идея {idx}:</b>\n"
                f"⚡ {idea.title}\n\n"
                f"{idea.description}\n\n"
                f"{'_' * 30}\n\n"
            )

        if failed:
            full_message_text += f"⚠️ Не удалось обработать: {len(failed)} видео\n\n"

        full_message_text += "👇 <b>Выбери для создания поста:</b>"

        await status_msg.edit_text(
            full_message_text,
            reply_markup=ideas_list_keyboard(ideas_list, source="youtube"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        # Save cached inputs for "generate more" feature
        await state.clear()
        await state.update_data(
            cached_inputs=inputs,
            channel_id=channel_id,
            channel_title=channel_title,
        )

    except Exception as e:
        logger.error(f"Error processing YouTube ideas: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {e}")
        await state.clear()


@router.message(YouTubeStates.waiting_for_links)
async def collect_youtube_links(message: Message, state: FSMContext):
    """Collect YouTube links from user."""
    if not message.text:
        await message.answer(
            "▶️ Отправьте ссылку на YouTube-видео.",
            parse_mode="HTML",
        )
        return

    text = message.text.strip()

    # Try to extract video ID
    video_id = extract_video_id(text)

    if not video_id:
        await message.answer(
            "❌ Не удалось распознать YouTube-ссылку.\n\n"
            "Поддерживаемые форматы:\n"
            "• https://youtube.com/watch?v=...\n"
            "• https://youtu.be/...\n"
            "• https://youtube.com/shorts/...",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    links = data.get("links", [])

    if len(links) >= 5:
        await message.answer(
            "⚠️ Достигнут лимит (5 видео).\n"
            "Нажмите /done для генерации идей.",
            parse_mode="HTML",
        )
        return

    # Normalize URL
    normalized_url = f"https://www.youtube.com/watch?v={video_id}"
    links.append(normalized_url)
    count = len(links)
    await state.update_data(links=links)

    await message.answer(
        f"✅ <b>Видео принято!</b> ({count}/5)\n\n"
        "Можете отправить ещё ссылку или нажмите /done",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "youtube:more")
async def generate_more_youtube(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Generate more ideas from the same cached YouTube transcripts."""
    user_id = callback.from_user.id

    data = await state.get_data()
    cached_inputs = data.get("cached_inputs")
    channel_id = data.get("channel_id")
    channel_title = data.get("channel_title")

    if not cached_inputs or not channel_id:
        # State expired, redirect to start
        await callback.answer("⏳ Сессия истекла. Отправьте новые ссылки.", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    status_msg = await callback.message.edit_text(
        "⏳ <b>Генерирую ещё идеи из тех же видео...</b>",
        parse_mode="HTML",
    )

    try:
        # Generate more ideas using cached transcripts
        generated_ideas = await llm_client.generate_ideas_from_inputs(
            inputs=cached_inputs,
            channel_title=channel_title,
            source_type="youtube",
        )

        if not generated_ideas:
            await status_msg.edit_text(
                "⚠️ Не удалось сгенерировать новые идеи. Попробуйте отправить другие видео.",
                reply_markup=main_menu_keyboard(),
            )
            return

        # Save to DB
        saved_ideas = []
        for idea_dto in generated_ideas:
            new_idea = Idea(
                managed_channel_id=channel_id,
                title=idea_dto.title,
                description=idea_dto.description,
                source="youtube",
            )
            session.add(new_idea)
            await session.flush()
            saved_ideas.append(new_idea)

        await session.commit()

        # Format response
        ideas_list = [(idea.id, idx + 1) for idx, idea in enumerate(saved_ideas)]

        full_message_text = (
            f"✅ <b>Ещё идеи из YouTube:</b>\n\n"
            f"Канал: {channel_title}\n\n"
            + ("_" * 30) + "\n\n"
        )

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
            reply_markup=ideas_list_keyboard(ideas_list, source="youtube"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"Error generating more YouTube ideas: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {e}")
