"""Content plan handler for weekly scheduling."""

from __future__ import annotations

import logging
import pytz
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    User, ManagedChannel, ContentPlan, ContentPlanSlot,
    ContentPlanStatus, Draft, DraftStatus,
    ScheduledPost, ScheduledPostStatus,
)
from app.handlers.states import ContentPlanStates
from app.handlers.keyboards import back_to_menu_keyboard, main_menu_keyboard
from app.llm_client import llm_client
from app.utils import answer_nav

logger = logging.getLogger(__name__)
router = Router(name="content_plan")

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAY_NAMES_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


# ============================================================
# KEYBOARDS
# ============================================================


def content_plans_list_keyboard(plans: list) -> InlineKeyboardMarkup:
    """Keyboard for content plans list."""
    buttons = []

    for plan in plans:
        status_icon = {
            ContentPlanStatus.DRAFT: "📝",
            ContentPlanStatus.ACTIVE: "✅",
            ContentPlanStatus.COMPLETED: "☑️",
        }.get(plan.status, "📅")

        week_str = plan.week_start.strftime("%d.%m")
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} Неделя с {week_str}",
                callback_data=f"content_plan:view:{plan.id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Создать план", callback_data="content_plan:create")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def content_plan_view_keyboard(plan: ContentPlan, has_drafts: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for viewing a content plan."""
    buttons = []

    # 1. Combined activate and schedule button for DRAFT plans without drafts
    if plan.status == ContentPlanStatus.DRAFT and not has_drafts:
        buttons.append([
            InlineKeyboardButton(
                text="✅ Активировать и запланировать посты",
                callback_data=f"content_plan:activate_and_schedule:{plan.id}"
            )
        ])
    # 2. Create drafts button for ACTIVE plans or if some slots don't have drafts
    elif plan.status in (ContentPlanStatus.DRAFT, ContentPlanStatus.ACTIVE) and not has_drafts:
        buttons.append([
            InlineKeyboardButton(text="📝 Создать черновики по плану", callback_data=f"content_plan:create_drafts:{plan.id}")
        ])

    # 3. Edit posts (only if drafts exist)
    if has_drafts:
        buttons.append([
            InlineKeyboardButton(text="✏️ Редактировать посты", callback_data=f"content_plan:edit_posts:{plan.id}")
        ])

    # 4. Regenerate (only for draft plans)
    if plan.status == ContentPlanStatus.DRAFT:
        buttons.append([
            InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"content_plan:regenerate:{plan.id}")
        ])

    # 5. Edit topics/ideas
    buttons.append([
        InlineKeyboardButton(text="✏️ Редактировать идеи постов", callback_data=f"content_plan:edit_slots:{plan.id}")
    ])

    buttons.append([
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"content_plan:delete:{plan.id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ К списку", callback_data="content_plan:list")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


def get_next_monday() -> datetime:
    """Get next Monday's date."""
    today = datetime.utcnow().date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # If today is Monday, get next Monday
    next_monday = today + timedelta(days=days_until_monday)
    return datetime.combine(next_monday, datetime.min.time())


def format_plan_text(plan: ContentPlan, slots: list[ContentPlanSlot], show_hint: bool = True) -> str:
    """Format content plan for display."""
    status_text = {
        ContentPlanStatus.DRAFT: "📝 Черновик",
        ContentPlanStatus.ACTIVE: "✅ Активен",
        ContentPlanStatus.COMPLETED: "☑️ Завершён",
    }.get(plan.status, "❓")

    week_str = plan.week_start.strftime("%d.%m.%Y")
    text = f"📅 <b>Контент-план на неделю с {week_str}</b>\n"
    text += f"Статус: {status_text}\n\n"

    # Group slots by day
    slots_by_day = {}
    for slot in slots:
        day = slot.day_of_week
        if day not in slots_by_day:
            slots_by_day[day] = []
        slots_by_day[day].append(slot)

    for day in range(7):
        day_name = DAY_NAMES_FULL[day]
        text += f"<b>{day_name}</b>\n"

        if day in slots_by_day:
            for slot in sorted(slots_by_day[day], key=lambda s: s.time):
                draft_status = ""
                if slot.draft_id:
                    draft_status = " ✅"

                text += f"  {slot.time} — {slot.topic}{draft_status}\n"
        else:
            text += "  <i>Нет постов</i>\n"

        text += "\n"

    # Add hint about workflow
    has_drafts = any(slot.draft_id for slot in slots)
    if show_hint:
        if not has_drafts:
            text += "💡 <i>Чтобы редактировать посты или добавить фото — сначала нажмите «Создать черновики по плану».</i>\n"
        else:
            text += "💡 <i>Нажмите «Редактировать посты» чтобы изменить текст или добавить фото.</i>\n"

    return text


# ============================================================
# HANDLERS
# ============================================================


@router.callback_query(F.data == "content_plan:list")
async def list_content_plans(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Show list of content plans."""
    await state.clear()

    user = await get_user_with_channel(session, callback.from_user.id)
    if not user or not user.current_channel:
        await callback.answer("⚠️ Сначала выберите канал", show_alert=True)
        return

    channel = user.current_channel

    # Get plans for channel
    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.channel_id == channel.id)
        .order_by(ContentPlan.week_start.desc())
        .limit(10)
    )
    plans = result.scalars().all()

    if not plans:
        text = (
            f"📅 <b>Контент-план для {channel.title}</b>\n\n"
            "У вас пока нет контент-планов.\n\n"
            "<i>Контент-план — это расписание публикаций на неделю.\n"
            "AI сгенерирует темы и время для постов.</i>"
        )
    else:
        text = (
            f"📅 <b>Контент-планы для {channel.title}</b>\n\n"
            f"Всего: {len(plans)}\n\n"
            "Выберите план:"
        )

    await answer_nav(
        callback=callback,
        label="📅 Контент-план",
        new_text=text,
        reply_markup=content_plans_list_keyboard(plans),
    )


@router.callback_query(F.data == "content_plan:create")
async def create_plan_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Start creating a new content plan."""
    user = await get_user_with_channel(session, callback.from_user.id)
    if not user or not user.current_channel:
        await callback.answer("⚠️ Сначала выберите канал", show_alert=True)
        return

    channel = user.current_channel

    await state.update_data(channel_id=channel.id, channel_title=channel.title)
    await state.set_state(ContentPlanStates.waiting_for_preferences)

    await callback.message.edit_text(
        f"📅 <b>Создание контент-плана для {channel.title}</b>\n\n"
        "Укажите ваши предпочтения по стилю и темам (опционально):\n\n"
        "<b>Примеры:</b>\n"
        "• <i>Больше пользы, меньше новостей</i>\n"
        "• <i>Больше кейсов/разборов, меньше «воды»</i>\n"
        "• <i>Больше вовлечения (вопросы/опросы), меньше лонгридов</i>\n"
        "• <i>Больше новостей и обзоров, меньше советов</i>\n"
        "• <i>Баланс: польза/вовлечение/доверие/продажи</i>\n"
        "• <i>Деловой и продающий стиль</i>\n"
        "• <i>Только смешной контент</i>\n"
        "• <i>Экспертный и глубокий</i>\n\n"
        "Отправьте текст или нажмите <b>Пропустить</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏩ Пропустить", callback_data="content_plan:skip_preferences")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="content_plan:list")],
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "content_plan:skip_preferences")
async def skip_preferences(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Skip preferences and generate plan."""
    await generate_plan(callback, session, state, preferences=None)


@router.message(ContentPlanStates.waiting_for_preferences, F.text)
async def receive_preferences(message: Message, session: AsyncSession, state: FSMContext):
    """Receive preferences and generate plan."""
    preferences = message.text.strip()

    if preferences.startswith("/"):
        await message.answer("⚠️ Введите предпочтения текстом или нажмите Пропустить.")
        return

    # Create a fake callback for unified handling
    class FakeCallback:
        def __init__(self, msg):
            self.message = msg
            self.from_user = msg.from_user

        async def answer(self, *args, **kwargs):
            pass

    await generate_plan(FakeCallback(message), session, state, preferences=preferences)


async def generate_plan(callback, session: AsyncSession, state: FSMContext, preferences: Optional[str]):
    """Generate content plan using AI."""
    data = await state.get_data()
    channel_id = data.get("channel_id")
    channel_title = data.get("channel_title")

    if not channel_id:
        await callback.message.answer("❌ Ошибка: канал не выбран.")
        await state.clear()
        return

    # Get channel for tone of voice
    result = await session.execute(
        select(ManagedChannel).where(ManagedChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    await state.clear()

    status_msg = await callback.message.answer(
        "⏳ <b>Генерирую контент-план...</b>\n\n"
        "Это может занять минуту.",
        parse_mode="HTML",
    )

    try:
        # Generate plan via AI
        plan_slots = await llm_client.generate_content_plan(
            channel_title=channel_title,
            tone_of_voice=channel.tone_of_voice if channel else None,
            topic_preferences=preferences,
            posts_per_day=1,
        )

        if not plan_slots:
            await status_msg.edit_text(
                "❌ Не удалось сгенерировать план. Попробуйте ещё раз.",
                reply_markup=back_to_menu_keyboard(),
            )
            return

        # Create plan in DB
        week_start = get_next_monday()

        plan = ContentPlan(
            channel_id=channel_id,
            week_start=week_start,
            status=ContentPlanStatus.DRAFT,
        )
        session.add(plan)
        await session.flush()

        # Create slots
        for slot_data in plan_slots:
            slot = ContentPlanSlot(
                plan_id=plan.id,
                day_of_week=slot_data.get("day_of_week", 0),
                time=slot_data.get("time", "12:00"),
                topic=slot_data.get("topic", "")[:500],
                description=slot_data.get("description", ""),
            )
            session.add(slot)

        await session.commit()
        await session.refresh(plan, ["slots"])

        # Show result
        text = format_plan_text(plan, plan.slots)
        text += "\n💡 <i>Нажмите «Активировать» чтобы начать работу с планом.</i>"

        has_drafts = any(slot.draft_id for slot in plan.slots)
        await status_msg.edit_text(
            text,
            reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error generating content plan: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=back_to_menu_keyboard(),
        )


@router.callback_query(F.data.startswith("content_plan:view:"))
async def view_plan(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """View content plan details."""
    await state.clear()
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    text = format_plan_text(plan, plan.slots)

    # Check if any slots have drafts
    has_drafts = any(slot.draft_id for slot in plan.slots)

    await callback.message.edit_text(
        text,
        reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("content_plan:activate:"))
async def activate_plan(callback: CallbackQuery, session: AsyncSession):
    """Activate content plan."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan).where(ContentPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    plan.status = ContentPlanStatus.ACTIVE
    await session.commit()

    await callback.answer("✅ План активирован!")

    # Refresh and show
    await session.refresh(plan, ["slots"])
    text = format_plan_text(plan, plan.slots)

    has_drafts = any(slot.draft_id for slot in plan.slots)
    await callback.message.edit_text(
        text,
        reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("content_plan:activate_and_schedule:"))
async def activate_and_schedule(callback: CallbackQuery, session: AsyncSession):
    """Activate plan and immediately create drafts/scheduled posts."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots), selectinload(ContentPlan.channel))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    # Change status to ACTIVE
    plan.status = ContentPlanStatus.ACTIVE
    await session.commit()

    # Now create drafts and schedule posts
    await callback.message.edit_text(
        "⏳ <b>Активирую план и создаю черновики...</b>\n\n"
        "Это займёт 30-60 секунд.",
        parse_mode="HTML",
    )

    # Reuse the draft creation logic
    slots_without_drafts = [s for s in plan.slots if not s.draft_id]

    if not slots_without_drafts:
        await callback.answer("✅ Все черновики уже созданы", show_alert=True)
        return

    created_count = 0
    scheduled_count = 0

    for i, slot in enumerate(slots_without_drafts):
        try:
            # Generate draft text
            draft_result = await llm_client.generate_draft(
                idea_title=slot.topic,
                idea_description=slot.description or "",
                tone_of_voice=plan.channel.tone_of_voice,
            )

            if draft_result:
                # Create draft with SCHEDULED status
                draft = Draft(
                    managed_channel_id=plan.channel_id,
                    title=draft_result.title or slot.topic[:100],
                    content=draft_result.content,
                    status=DraftStatus.SCHEDULED,
                )
                session.add(draft)
                await session.flush()

                # Link to slot
                slot.draft_id = draft.id
                created_count += 1

                # Calculate scheduled datetime with timezone conversion
                try:
                    hour, minute = map(int, slot.time.split(":"))

                    # Create naive datetime from plan.week_start + offset
                    scheduled_at_naive = plan.week_start + timedelta(days=slot.day_of_week)
                    scheduled_at_naive = scheduled_at_naive.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    # Interpret as channel's timezone and convert to UTC
                    channel_tz = pytz.timezone(plan.channel.timezone)
                    scheduled_at_local = channel_tz.localize(scheduled_at_naive)
                    scheduled_at_utc = scheduled_at_local.astimezone(pytz.UTC).replace(tzinfo=None)

                    # Create scheduled post
                    scheduled_post = ScheduledPost(
                        draft_id=draft.id,
                        scheduled_at=scheduled_at_utc,  # Store as UTC
                        status=ScheduledPostStatus.PLANNED,
                    )
                    session.add(scheduled_post)
                    scheduled_count += 1

                    logger.info(
                        f"Scheduled post {draft.id} for slot {slot.id}: "
                        f"{slot.time} {plan.channel.timezone} = {scheduled_at_utc.strftime('%H:%M')} UTC "
                        f"on {scheduled_at_utc.date()}"
                    )
                except Exception as time_err:
                    logger.error(f"Error parsing time for slot {slot.id}: {time_err}")

            # Update progress
            await callback.message.edit_text(
                f"⏳ <b>Создаю черновики и расписание...</b>\n\n"
                f"{i + 1}/{len(slots_without_drafts)} обработано\n"
                f"✅ Создано: {created_count}\n"
                f"📅 Запланировано: {scheduled_count}",
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error(f"Error creating draft for slot {slot.id}: {e}")

    await session.commit()
    await session.refresh(plan, ["slots"])

    # Calculate first and last post times for user feedback
    all_scheduled = await session.execute(
        select(ScheduledPost)
        .join(Draft)
        .where(Draft.managed_channel_id == plan.channel_id)
        .where(ScheduledPost.status == ScheduledPostStatus.PLANNED)
        .order_by(ScheduledPost.scheduled_at)
    )
    scheduled_posts = all_scheduled.scalars().all()

    text = format_plan_text(plan, plan.slots, show_hint=False)
    text += f"\n✅ <b>План активирован!</b>\n"
    text += f"📝 <b>Создано {created_count} черновиков</b>\n"
    text += f"📅 <b>Запланировано {scheduled_count} постов</b>\n\n"

    # Show first and last post times in channel's timezone
    if scheduled_posts:
        channel_tz = pytz.timezone(plan.channel.timezone)
        first_post_utc = scheduled_posts[0].scheduled_at
        last_post_utc = scheduled_posts[-1].scheduled_at

        first_post_local = pytz.UTC.localize(first_post_utc).astimezone(channel_tz)
        last_post_local = pytz.UTC.localize(last_post_utc).astimezone(channel_tz)

        # Format timezone name (Europe/Moscow -> MSK, Europe/London -> GMT)
        tz_abbr = first_post_local.strftime('%Z')  # e.g., "MSK", "GMT", "EST"

        text += f"🌍 <b>Часовой пояс:</b> {plan.channel.timezone} ({tz_abbr})\n"
        text += f"📅 <b>Первый пост:</b> {first_post_local.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"📅 <b>Последний пост:</b> {last_post_local.strftime('%d.%m.%Y %H:%M')}\n\n"

    text += f"💡 Посты выйдут автоматически по расписанию.\n\n"
    text += f"✏️ <b>Чтобы отредактировать или изменить время:</b>\n"
    text += f"  → Главное меню → 📅 Расписание\n"
    text += f"  → Там вы сможете изменить время любого поста\n\n"
    text += f"⚙️ <b>Изменить часовой пояс канала:</b>\n"
    text += f"  → Главное меню → Мои каналы → выбрать канал"

    has_drafts = any(slot.draft_id for slot in plan.slots)
    await callback.message.edit_text(
        text,
        reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("content_plan:regenerate:"))
async def regenerate_plan(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Regenerate content plan slots."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots), selectinload(ContentPlan.channel))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    await callback.message.edit_text(
        "⏳ <b>Перегенерирую план...</b>",
        parse_mode="HTML",
    )

    try:
        # Delete old slots
        for slot in plan.slots:
            await session.delete(slot)

        # Generate new
        plan_slots = await llm_client.generate_content_plan(
            channel_title=plan.channel.title,
            tone_of_voice=plan.channel.tone_of_voice,
            posts_per_day=1,
        )

        for slot_data in plan_slots:
            slot = ContentPlanSlot(
                plan_id=plan.id,
                day_of_week=slot_data.get("day_of_week", 0),
                time=slot_data.get("time", "12:00"),
                topic=slot_data.get("topic", "")[:500],
                description=slot_data.get("description", ""),
            )
            session.add(slot)

        await session.commit()
        await session.refresh(plan, ["slots"])

        text = format_plan_text(plan, plan.slots)
        has_drafts = any(slot.draft_id for slot in plan.slots)
        await callback.message.edit_text(
            text,
            reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error regenerating plan: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"content_plan:view:{plan_id}")]
            ]),
        )


@router.callback_query(F.data.startswith("content_plan:create_drafts:"))
async def create_drafts_from_plan(callback: CallbackQuery, session: AsyncSession):
    """Create drafts for all slots in plan and schedule them."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots), selectinload(ContentPlan.channel))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    slots_without_drafts = [s for s in plan.slots if not s.draft_id]

    if not slots_without_drafts:
        await callback.answer("✅ Все черновики уже созданы", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ <b>Создаю черновики и расписание...</b>\n\n"
        f"0/{len(slots_without_drafts)} готово",
        parse_mode="HTML",
    )

    created_count = 0
    scheduled_count = 0

    for i, slot in enumerate(slots_without_drafts):
        try:
            # Generate draft text
            draft_result = await llm_client.generate_draft(
                idea_title=slot.topic,
                idea_description=slot.description or "",
                tone_of_voice=plan.channel.tone_of_voice,
            )

            if draft_result:
                # Create draft with SCHEDULED status
                draft = Draft(
                    managed_channel_id=plan.channel_id,
                    title=draft_result.title or slot.topic[:100],
                    content=draft_result.content,
                    status=DraftStatus.SCHEDULED,
                )
                session.add(draft)
                await session.flush()

                # Link to slot
                slot.draft_id = draft.id
                created_count += 1

                # Calculate scheduled datetime with timezone conversion
                try:
                    hour, minute = map(int, slot.time.split(":"))

                    # Create naive datetime from plan.week_start + offset
                    scheduled_at_naive = plan.week_start + timedelta(days=slot.day_of_week)
                    scheduled_at_naive = scheduled_at_naive.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    # Interpret as channel's timezone and convert to UTC
                    channel_tz = pytz.timezone(plan.channel.timezone)
                    scheduled_at_local = channel_tz.localize(scheduled_at_naive)
                    scheduled_at_utc = scheduled_at_local.astimezone(pytz.UTC).replace(tzinfo=None)

                    # Create scheduled post
                    scheduled_post = ScheduledPost(
                        draft_id=draft.id,
                        scheduled_at=scheduled_at_utc,  # Store as UTC
                        status=ScheduledPostStatus.PLANNED,
                    )
                    session.add(scheduled_post)
                    scheduled_count += 1

                    logger.info(
                        f"Scheduled post {draft.id} for slot {slot.id}: "
                        f"{slot.time} {plan.channel.timezone} = {scheduled_at_utc.strftime('%H:%M')} UTC "
                        f"on {scheduled_at_utc.date()}"
                    )
                except Exception as time_err:
                    logger.error(f"Error parsing time for slot {slot.id}: {time_err}")

            # Update progress
            await callback.message.edit_text(
                f"⏳ <b>Создаю черновики и расписание...</b>\n\n"
                f"{i + 1}/{len(slots_without_drafts)} обработано\n"
                f"✅ Создано: {created_count}\n"
                f"📅 Запланировано: {scheduled_count}",
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error(f"Error creating draft for slot {slot.id}: {e}")

    await session.commit()
    await session.refresh(plan, ["slots"])

    text = format_plan_text(plan, plan.slots, show_hint=False)
    text += f"\n✅ <b>Создано {created_count} черновиков!</b>\n"
    text += f"📅 <b>Запланировано {scheduled_count} постов!</b>\n\n"
    text += f"💡 Посты выйдут автоматически по расписанию.\n"
    text += f"Редактировать можно через «Редактировать посты»."

    has_drafts = any(slot.draft_id for slot in plan.slots)
    await callback.message.edit_text(
        text,
        reply_markup=content_plan_view_keyboard(plan, has_drafts=has_drafts),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("content_plan:delete:"))
async def delete_plan(callback: CallbackQuery, session: AsyncSession):
    """Delete content plan."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan).where(ContentPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 <b>Удалить контент-план?</b>\n\n"
        f"Неделя с {plan.week_start.strftime('%d.%m.%Y')}\n\n"
        "⚠️ Черновики и расписание НЕ будут удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"content_plan:confirm_delete:{plan_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"content_plan:view:{plan_id}"),
            ]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("content_plan:confirm_delete:"))
async def confirm_delete_plan(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Confirm plan deletion."""
    plan_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(ContentPlan).where(ContentPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if plan:
        await session.delete(plan)
        await session.commit()
        await callback.answer("✅ План удалён")

    # Go back to list
    await list_content_plans(callback, session, state)


@router.callback_query(F.data.startswith("content_plan:edit_slots:"))
async def edit_plan_slots(callback: CallbackQuery, session: AsyncSession):
    """Show slots for editing with pagination (3 ideas per page)."""
    parts = callback.data.split(":")
    plan_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    IDEAS_PER_PAGE = 3

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    # Sort slots by day and time
    all_slots = sorted(plan.slots, key=lambda s: (s.day_of_week, s.time))
    total_slots = len(all_slots)
    total_pages = (total_slots + IDEAS_PER_PAGE - 1) // IDEAS_PER_PAGE if total_slots > 0 else 1

    # Get current page slots
    start_idx = page * IDEAS_PER_PAGE
    end_idx = min(start_idx + IDEAS_PER_PAGE, total_slots)
    page_slots = all_slots[start_idx:end_idx]

    # Build text with ideas
    text = f"✏️ <b>Редактирование идей постов</b>\n"
    text += f"📄 Страница {page + 1}/{total_pages}\n\n"

    for i, slot in enumerate(page_slots):
        idea_num = start_idx + i + 1
        day_name = DAY_NAMES_FULL[slot.day_of_week]
        text += f"<b>{idea_num}. {day_name}, {slot.time}</b>\n"
        text += f"{slot.topic}\n\n"

    # Build buttons
    buttons = []

    # Buttons for each idea on current page
    for i, slot in enumerate(page_slots):
        idea_num = start_idx + i + 1
        buttons.append([
            InlineKeyboardButton(
                text=f"✏️ Редактировать идею {idea_num}",
                callback_data=f"content_plan:edit_slot:{slot.id}:{page}"
            )
        ])

    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"content_plan:edit_slots:{plan_id}:{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ Далее", callback_data=f"content_plan:edit_slots:{plan_id}:{page + 1}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к плану", callback_data=f"content_plan:view:{plan_id}")
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("content_plan:edit_slot:"))
async def edit_single_slot(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Start editing a single slot topic."""
    parts = callback.data.split(":")
    slot_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    result = await session.execute(
        select(ContentPlanSlot)
        .where(ContentPlanSlot.id == slot_id)
        .options(selectinload(ContentPlanSlot.plan))
    )
    slot = result.scalar_one_or_none()

    if not slot:
        await callback.answer("❌ Слот не найден", show_alert=True)
        return

    day_name = DAY_NAMES_FULL[slot.day_of_week]

    await state.set_state(ContentPlanStates.editing_slot)
    await state.update_data(slot_id=slot_id, plan_id=slot.plan_id, page=page)

    await callback.message.edit_text(
        f"✏️ <b>Редактирование идеи</b>\n\n"
        f"<b>{day_name}, {slot.time}</b>\n\n"
        f"Текущая идея:\n<i>{slot.topic}</i>\n\n"
        f"Отправьте новую идею для этого дня или нажмите «Перегенерировать»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"content_plan:regen_slot:{slot_id}:{page}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"content_plan:edit_slots:{slot.plan_id}:{page}")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ContentPlanStates.editing_slot)
async def process_slot_edit(message: Message, session: AsyncSession, state: FSMContext):
    """Process new topic text for slot."""
    data = await state.get_data()
    slot_id = data.get("slot_id")
    plan_id = data.get("plan_id")
    page = data.get("page", 0)

    if not slot_id:
        await message.answer("❌ Ошибка: слот не найден")
        await state.clear()
        return

    result = await session.execute(
        select(ContentPlanSlot).where(ContentPlanSlot.id == slot_id)
    )
    slot = result.scalar_one_or_none()

    if not slot:
        await message.answer("❌ Слот не найден")
        await state.clear()
        return

    # Update topic
    slot.topic = message.text[:500]
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ Идея обновлена!\n\n"
        f"<b>Новая идея:</b> {slot.topic}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К редактированию", callback_data=f"content_plan:edit_slots:{plan_id}:{page}")],
            [InlineKeyboardButton(text="⬅️ К плану", callback_data=f"content_plan:view:{plan_id}")]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("content_plan:regen_slot:"))
async def regenerate_single_slot(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Regenerate topic for a single slot using AI."""
    parts = callback.data.split(":")
    slot_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    result = await session.execute(
        select(ContentPlanSlot)
        .where(ContentPlanSlot.id == slot_id)
        .options(selectinload(ContentPlanSlot.plan).selectinload(ContentPlan.channel))
    )
    slot = result.scalar_one_or_none()

    if not slot:
        await callback.answer("❌ Слот не найден", show_alert=True)
        return

    await callback.message.edit_text(
        "⏳ Генерирую новую идею...",
        parse_mode="HTML",
    )

    try:
        # Generate single topic
        channel = slot.plan.channel
        plan_slots = await llm_client.generate_content_plan(
            channel_title=channel.title,
            tone_of_voice=channel.tone_of_voice,
            posts_per_day=1,
        )

        if plan_slots:
            # Take first generated slot for this day
            day_slots = [s for s in plan_slots if s.get("day_of_week") == slot.day_of_week]
            if day_slots:
                new_data = day_slots[0]
            else:
                new_data = plan_slots[0]

            slot.topic = new_data.get("topic", slot.topic)[:500]
            slot.description = new_data.get("description", "")
            await session.commit()

        day_name = DAY_NAMES_FULL[slot.day_of_week]

        await callback.message.edit_text(
            f"✅ <b>Идея обновлена!</b>\n\n"
            f"<b>{day_name}, {slot.time}</b>\n\n"
            f"Новая идея:\n<i>{slot.topic}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Ещё раз", callback_data=f"content_plan:regen_slot:{slot_id}:{page}")],
                [InlineKeyboardButton(text="⬅️ К редактированию", callback_data=f"content_plan:edit_slots:{slot.plan_id}:{page}")]
            ]),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error regenerating slot: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка генерации: {str(e)[:100]}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"content_plan:edit_slots:{slot.plan_id}:{page}")]
            ]),
        )

    await state.clear()


@router.callback_query(F.data.startswith("content_plan:edit_posts:"))
async def edit_plan_posts(callback: CallbackQuery, session: AsyncSession):
    """Show posts (drafts) for editing with pagination (3 per page)."""
    parts = callback.data.split(":")
    plan_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    POSTS_PER_PAGE = 3

    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.id == plan_id)
        .options(selectinload(ContentPlan.slots).selectinload(ContentPlanSlot.draft))
    )
    plan = result.scalar_one_or_none()

    if not plan:
        await callback.answer("❌ План не найден", show_alert=True)
        return

    # Get only slots with drafts
    slots_with_drafts = [s for s in plan.slots if s.draft_id]
    slots_with_drafts = sorted(slots_with_drafts, key=lambda s: (s.day_of_week, s.time))

    if not slots_with_drafts:
        await callback.answer("❌ Черновики ещё не созданы. Нажмите «Создать черновики по плану».", show_alert=True)
        return

    total_posts = len(slots_with_drafts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE if total_posts > 0 else 1

    # Get current page posts
    start_idx = page * POSTS_PER_PAGE
    end_idx = min(start_idx + POSTS_PER_PAGE, total_posts)
    page_slots = slots_with_drafts[start_idx:end_idx]

    # Build text with posts preview
    text = f"✏️ <b>Редактирование постов</b>\n"
    text += f"📄 Страница {page + 1}/{total_pages}\n\n"

    for i, slot in enumerate(page_slots):
        post_num = start_idx + i + 1
        day_name = DAY_NAMES_FULL[slot.day_of_week]
        # Show draft preview
        draft_preview = ""
        if slot.draft and slot.draft.content:
            draft_preview = slot.draft.content[:100]
            if len(slot.draft.content) > 100:
                draft_preview += "..."
        text += f"<b>{post_num}. {day_name}, {slot.time}</b>\n"
        text += f"<i>{draft_preview}</i>\n\n"

    # Build buttons
    buttons = []

    # Buttons for each post on current page
    for i, slot in enumerate(page_slots):
        post_num = start_idx + i + 1
        day_name = DAY_NAMES[slot.day_of_week]
        buttons.append([
            InlineKeyboardButton(
                text=f"✏️ Редактировать пост {post_num} ({day_name})",
                callback_data=f"drafts:view:{slot.draft_id}"
            )
        ])

    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"content_plan:edit_posts:{plan_id}:{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️ Далее", callback_data=f"content_plan:edit_posts:{plan_id}:{page + 1}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к плану", callback_data=f"content_plan:view:{plan_id}")
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()
