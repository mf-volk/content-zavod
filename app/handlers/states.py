"""FSM states for bot conversations."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ChannelStates(StatesGroup):
    """States for channel management."""

    waiting_for_channel = State()  # Waiting for channel forward/link
    waiting_for_tov = State()  # Waiting for tone of voice input
    waiting_for_tov_forwards = State()  # Waiting for forwarded posts for ToV
    waiting_for_default_text = State()  # Waiting for default post text input


class DonorStates(StatesGroup):
    """States for donor management."""

    waiting_for_donor_link = State()  # Waiting for donor channel link


class IdeaStates(StatesGroup):
    """States for idea generation."""

    generating = State()  # Currently generating ideas
    waiting_for_topic = State()  # Waiting for search topic text



class DraftStates(StatesGroup):
    """States for draft editing."""

    waiting_for_text = State()  # Waiting for new text
    waiting_for_ai_instructions = State()  # Waiting for AI edit instructions
    waiting_for_photo = State()  # Waiting for photo upload
    waiting_for_ai_prompt = State()  # Waiting for AI image prompt


class ScheduleStates(StatesGroup):
    """States for scheduling."""

    waiting_for_datetime = State()  # Waiting for custom datetime input


class SpaceStates(StatesGroup):
    """States for spaces management."""

    waiting_for_title = State()  # Waiting for space title
    waiting_for_materials = State()  # Waiting for materials upload
    processing = State()  # Processing materials


class ContentPlanStates(StatesGroup):
    """States for content plan management."""

    waiting_for_preferences = State()  # Waiting for user preferences
    reviewing = State()  # User reviewing generated plan
    editing_slot = State()  # Editing a single slot topic


class YouTubeStates(StatesGroup):
    """States for YouTube idea generation."""

    waiting_for_links = State()  # Waiting for YouTube video links
