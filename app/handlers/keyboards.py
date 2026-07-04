"""Keyboard layouts for bot."""

from __future__ import annotations

from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ============================================================
# MAIN MENU
# ============================================================


def main_menu_keyboard(channel_name: Optional[str] = None) -> InlineKeyboardMarkup:
    """Main menu keyboard."""
    channel_text = f"📢 {channel_name}" if channel_name else "📢 Выбрать канал"

    rows = [
        # Row 1: Channel selector (full width)
        [InlineKeyboardButton(text=channel_text, callback_data="channels:list")],
        # Row 2: Donors | Ideas from donors
        [
            InlineKeyboardButton(text="📚 Доноры", callback_data="donors:list"),
            InlineKeyboardButton(text="💡 Идеи с доноров", callback_data="ideas:list"),
        ],
        # Row 3: My docs | Voice ideas | YouTube ideas
        [
            InlineKeyboardButton(text="📁 Мои папки", callback_data="spaces:list"),
            InlineKeyboardButton(text="🎤 Голосовые", callback_data="my_ideas:start"),
            InlineKeyboardButton(text="📺 YouTube", callback_data="youtube:start"),
        ],
        # Row 4: Content plan | Drafts
        [
            InlineKeyboardButton(text="📅 Контент-план", callback_data="content_plan:list"),
            InlineKeyboardButton(text="✏️ Черновики", callback_data="drafts:list"),
        ],
        # Row 5: Schedule | Analytics
        [
            InlineKeyboardButton(text="🗓 Расписание", callback_data="schedule:list"),
            InlineKeyboardButton(text="📊 Аналитика", callback_data="analytics:view"),
        ],
        # Row 6: Guide
        [InlineKeyboardButton(text="📖 Гайд", callback_data="menu:guide")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Back to main menu button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


def back_to_draft_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Back to draft button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к черновику", callback_data=f"drafts:view:{draft_id}")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


# ============================================================
# CHANNELS
# ============================================================


def channels_list_keyboard(
    channels: list[tuple[int, str]],
    current_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Channel list keyboard.

    Args:
        channels: List of (id, title) tuples
        current_id: Currently selected channel ID
    """
    buttons = []

    for channel_id, title in channels:
        marker = "✅ " if channel_id == current_id else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{marker}{title}",
                callback_data=f"channels:select:{channel_id}",
            )
        ])

    if current_id:
        buttons.append([
            InlineKeyboardButton(
                text="⚙️ Настроить канал",
                callback_data=f"channels:settings:{current_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить канал", callback_data="channels:add"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def channel_settings_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    """Channel settings keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🎨 Настроить стиль (ToV)",
                callback_data=f"channels:tov:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="🌍 Часовой пояс",
                callback_data=f"channels:timezone:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="📌 Текст по умолчанию в посты",
                callback_data=f"channels:default_text:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="🗑 Удалить канал",
                callback_data=f"channels:delete:{channel_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="channels:list")],
        ]
    )


def default_text_keyboard(channel_id: int, has_text: bool = False) -> InlineKeyboardMarkup:
    """Default post text settings keyboard."""
    buttons = []

    if has_text:
        buttons.append([InlineKeyboardButton(
            text="✏️ Изменить текст",
            callback_data=f"channels:default_text:edit:{channel_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="📍 В начало поста",
            callback_data=f"channels:default_text:pos:start:{channel_id}",
        ), InlineKeyboardButton(
            text="📍 В конец поста",
            callback_data=f"channels:default_text:pos:end:{channel_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="🗑 Удалить текст по умолчанию",
            callback_data=f"channels:default_text:clear:{channel_id}",
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="➕ Добавить текст по умолчанию",
            callback_data=f"channels:default_text:edit:{channel_id}",
        )])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"channels:settings:{channel_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tov_options_keyboard(channel_id: int, waiting_for_forwards: bool = False) -> InlineKeyboardMarkup:
    """Tone of voice onboarding options."""
    if waiting_for_forwards:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Готово (Проанализировать)",
                    callback_data="channels:tov:forwards_done",
                )],
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"channels:settings:{channel_id}")],
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🤖 Сгенерировать из постов",
                callback_data=f"channels:tov:auto:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="✏️ Ввести вручную",
                callback_data=f"channels:tov:manual:{channel_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"channels:settings:{channel_id}")],
        ]
    )


# ============================================================
# DONORS
# ============================================================


def donors_list_keyboard(
    donors: list[tuple[int, str, str]],
) -> InlineKeyboardMarkup:
    """Donor list keyboard.

    Args:
        donors: List of (id, username, status) tuples
    """
    buttons = []

    for donor_id, username, status in donors:
        status_emoji = "✅" if status == "active" else "⚠️"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} @{username}",
                callback_data=f"donors:view:{donor_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить донора", callback_data="donors:add"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить все", callback_data="donors:parse_all"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def donor_view_keyboard(donor_id: int) -> InlineKeyboardMarkup:
    """Single donor view keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🔄 Обновить посты",
                callback_data=f"donors:parse:{donor_id}",
            )],
            [InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=f"donors:delete:{donor_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="donors:list")],
        ]
    )


# ============================================================
# IDEAS
# ============================================================


def ideas_list_keyboard(ideas: list[tuple[int, int]], source: str = "donors") -> InlineKeyboardMarkup:
    """
    Keyboard for ideas list selection.

    Args:
        ideas: List of (idea_id, number) tuples.
        source: Where ideas came from ("donors", "youtube", "voice").
    """
    # Create number buttons row
    number_buttons = [
        InlineKeyboardButton(
            text=f"📝 {num}",
            callback_data=f"ideas:select:{idea_id}"
        )
        for idea_id, num in ideas
    ]

    # "Generate more" button depends on source
    if source == "youtube":
        more_btn = InlineKeyboardButton(text="📺 Ещё идеи", callback_data="youtube:more")
    elif source == "voice":
        more_btn = InlineKeyboardButton(text="🎤 Ещё из голосовых", callback_data="my_ideas:start")
    else:
        more_btn = InlineKeyboardButton(text="🔄 Сгенерировать ещё", callback_data="ideas:generate")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            number_buttons,
            [more_btn],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


def ideas_settings_keyboard(
    current_topic: Optional[str],
    is_archive: bool, 
) -> InlineKeyboardMarkup:
    """Idea generation settings keyboard."""
    
    archive_status = "✅ Вкл" if is_archive else "❌ Выкл"
    topic_text = f"Тема: {current_topic}" if current_topic else "Тема: Любая (авто)"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📂 Архивный поиск: {archive_status}", 
                callback_data="ideas:settings:toggle_archive"
            )],
            [InlineKeyboardButton(
                text=f"🏷 {topic_text}", 
                callback_data="ideas:settings:set_topic"
            )],
            [InlineKeyboardButton(text="⬅️ К идеям", callback_data="ideas:list")],
        ]
    )


def idea_selected_keyboard(idea_id: int) -> InlineKeyboardMarkup:
    """Selected idea keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Написать пост",
                callback_data=f"ideas:create_draft:{idea_id}",
            )],
            [InlineKeyboardButton(
                text="🗑 Удалить идею",
                callback_data=f"ideas:delete:{idea_id}",
            )],
            [InlineKeyboardButton(text="⬅️ К списку идей", callback_data="ideas:list")],
        ]
    )


# ============================================================
# DRAFTS
# ============================================================


def drafts_list_keyboard(
    drafts: list[tuple[int, str, str]],
) -> InlineKeyboardMarkup:
    """Drafts list keyboard.

    Args:
        drafts: List of (id, title, status) tuples
    """
    buttons = []

    status_emoji = {
        "draft": "📝",
        "editing": "✏️",
        "ready": "✅",
        "scheduled": "📅",
        "published": "📤",
    }

    for draft_id, title, status in drafts:
        emoji = status_emoji.get(status, "📝")
        short_title = title[:35] + "..." if len(title) > 35 else title
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {short_title}",
                callback_data=f"drafts:view:{draft_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Новый черновик", callback_data="drafts:new"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def draft_edit_keyboard(
    draft_id: int, 
    has_media: bool = False, 
    media_position: str = "top",
    content_length: int = 0
) -> InlineKeyboardMarkup:
    """Draft editing keyboard."""

    buttons = [
        [InlineKeyboardButton(
            text="✏️ Редактировать текст",
            callback_data=f"drafts:edit_text:{draft_id}",
        )],
        [InlineKeyboardButton(
            text="🤖 AI редактирование",
            callback_data=f"drafts:ai_edit:{draft_id}",
        )],
        [
            InlineKeyboardButton(
                text="🎨 AI Фото",
                callback_data=f"media:ai_gen:{draft_id}",
            ),
            InlineKeyboardButton(
                text="📸 Добавить фото",
                callback_data=f"media:add:{draft_id}",
            ),
        ],
    ]

    if has_media:
        # Режим расположения фото — две строки по 2 и 1 кнопки
        # Отмечаем текущий режим галочкой
        def _mark(label: str, mode: str) -> str:
            return f"✅ {label}" if media_position == mode else label

        buttons.append([
            InlineKeyboardButton(
                text=_mark("🖼 Фото сверху", "top"),
                callback_data=f"drafts:pos:top:{draft_id}",
            ),
            InlineKeyboardButton(
                text=_mark("📝 Фото снизу", "text_top"),
                callback_data=f"drafts:pos:text_top:{draft_id}",
            ),
        ])

        # Warning for long text with "top" mode
        if media_position == "top" and content_length > 1000:
            buttons.append([InlineKeyboardButton(
                text="⚠️ Текст >1024 — будет 2 сообщения",
                callback_data="noop",
            )])

        buttons.append([
            InlineKeyboardButton(
                text="🗑 Убрать фото",
                callback_data=f"media:clear:{draft_id}",
            ),
        ])

    buttons.extend([
        [
            InlineKeyboardButton(
                text="📅 Запланировать",
                callback_data=f"schedule:draft:{draft_id}",
            ),
            InlineKeyboardButton(
                text="🚀 Опубликовать",
                callback_data=f"drafts:publish:{draft_id}",
            ),
        ],
        [InlineKeyboardButton(
            text="👁 Превью",
            callback_data=f"drafts:preview:{draft_id}",
        )],
        [InlineKeyboardButton(
            text="🗑 Удалить черновик",
            callback_data=f"drafts:delete:{draft_id}",
        )],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="drafts:list")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
# MEDIA (AI Image)
# ============================================================


def ai_image_result_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """AI image generation result keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Сохранить к посту",
                callback_data=f"media:ai_save:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="🔄 Перегенерировать",
                callback_data=f"media:ai_regen:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"drafts:view:{draft_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


def ai_prompt_selection_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """AI image prompt selection keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🪄 Предложить промт",
                callback_data=f"media:suggest_prompt:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"drafts:view:{draft_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )

def ai_prompt_suggestion_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """AI image prompt suggestion keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                 text="🎨 По предложенному промту",
                 callback_data=f"media:select_size:{draft_id}",
            )],
            [InlineKeyboardButton(
                 text="❌ Отменить",
                 callback_data=f"drafts:view:{draft_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


def ai_image_size_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Keyboard for selecting image size/aspect ratio."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📱 Вертикальный (9:16)",
                callback_data=f"media:ai_gen_size:1024x1792:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="⬜ Квадратный (1:1)",
                callback_data=f"media:ai_gen_size:1024x1024:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="🖼 Горизонтальный (16:9)",
                callback_data=f"media:ai_gen_size:1792x1024:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"drafts:view:{draft_id}",
            )],
        ]
    )
# ============================================================
# SCHEDULE
# ============================================================


def schedule_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Scheduling options keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏰ Через 1 час",
                    callback_data=f"schedule:quick:1:{draft_id}",
                ),
                InlineKeyboardButton(
                    text="⏰ Через 3 часа",
                    callback_data=f"schedule:quick:3:{draft_id}",
                ),
            ],
            [InlineKeyboardButton(
                text="📆 Выбрать дату и время",
                callback_data=f"schedule:custom:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="⬅️ Назад к черновику",
                callback_data=f"drafts:view:{draft_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


def scheduled_posts_keyboard(
    posts: list[tuple[int, str, str]],
) -> InlineKeyboardMarkup:
    """Scheduled posts list keyboard.

    Args:
        posts: List of (id, title, scheduled_time) tuples
    """
    buttons = []

    for post_id, title, scheduled_time in posts:
        short_title = title[:25] + "..." if len(title) > 25 else title
        buttons.append([
            InlineKeyboardButton(
                text=f"📅 {scheduled_time} — {short_title}",
                callback_data=f"schedule:view:{post_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def scheduled_post_view_keyboard(post_id: int, draft_id: int) -> InlineKeyboardMarkup:
    """Single scheduled post view keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Редактировать пост",
                callback_data=f"drafts:view:{draft_id}",
            )],
            [InlineKeyboardButton(
                text="🚀 Опубликовать сейчас",
                callback_data=f"schedule:publish_now:{post_id}",
            )],
            [InlineKeyboardButton(
                text="⏰ Изменить время",
                callback_data=f"schedule:reschedule:{post_id}",
            )],
            [InlineKeyboardButton(
                text="❌ Отменить публикацию",
                callback_data=f"schedule:cancel:{post_id}",
            )],
            [InlineKeyboardButton(text="⬅️ К расписанию", callback_data="schedule:list")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")],
        ]
    )


# ============================================================
# CONFIRMATION
# ============================================================


def confirm_keyboard(action: str, item_id: int) -> InlineKeyboardMarkup:
    """Generic confirmation keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"confirm:yes:{action}:{item_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"confirm:no:{action}:{item_id}",
                ),
            ],
        ]
    )
