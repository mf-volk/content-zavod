"""Database models for Content Zavod Bot."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DraftStatus(str, enum.Enum):
    """Draft status enumeration."""

    DRAFT = "draft"
    EDITING = "editing"
    READY = "ready"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"


class ScheduledPostStatus(str, enum.Enum):
    """Scheduled post status enumeration."""

    PLANNED = "planned"
    SENT = "sent"
    ERROR = "error"


class IdeaSourceType(str, enum.Enum):
    """Idea source type for filtering."""

    RECENT = "recent"
    ARCHIVE = "archive"
    MIXED = "mixed"


class IdeaStatus(str, enum.Enum):
    """Idea status enumeration."""

    NEW = "new"
    USED = "used"
    ARCHIVED = "archived"
    SKIPPED = "skipped"


class MediaType(str, enum.Enum):
    """Media type enumeration."""

    PHOTO = "photo"
    ALBUM = "album"


class DonorStatus(str, enum.Enum):
    """Donor channel status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class SpaceMaterialType(str, enum.Enum):
    """Space material type enumeration."""

    TEXT = "text"
    VOICE = "voice"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT_WORD = "document_word"
    DOCUMENT_EXCEL = "document_excel"
    DOCUMENT_PDF = "document_pdf"
    LINK = "link"
    VIDEO = "video"
    YOUTUBE = "youtube"  # YouTube video transcript


class SpaceStatus(str, enum.Enum):
    """Space processing status enumeration."""

    COLLECTING = "collecting"  # User is adding materials
    PROCESSING = "processing"  # Processing documents
    READY = "ready"  # Ideas generated
    ERROR = "error"


class ContentPlanStatus(str, enum.Enum):
    """Content plan status enumeration."""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"


# ============================================================
# MODELS
# ============================================================


class User(Base, TimestampMixin):
    """Telegram user model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Current selected channel for context
    current_channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    managed_channels: Mapped[list["ManagedChannel"]] = relationship(
        back_populates="owner",
        foreign_keys="ManagedChannel.owner_id",
    )
    current_channel: Mapped[Optional["ManagedChannel"]] = relationship(
        foreign_keys=[current_channel_id],
    )


class ManagedChannel(Base, TimestampMixin):
    """Managed Telegram channel model."""

    __tablename__ = "managed_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Owner
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Settings
    tone_of_voice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")
    language: Mapped[str] = mapped_column(String(10), default="ru")
    
    # Idea Generation Settings
    idea_topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idea_source_type: Mapped[IdeaSourceType] = mapped_column(
        Enum(IdeaSourceType, values_callable=lambda obj: [e.value for e in obj]),
        default=IdeaSourceType.RECENT
    )
    
    # Default post text (inserted at beginning or end of every post)
    default_post_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_post_text_position: Mapped[str] = mapped_column(String(10), default="end")  # "start" or "end"

    # Diversity Rotation State
    next_top_rank: Mapped[int] = mapped_column(default=0)
    used_random_donor_ids: Mapped[Optional[str]] = mapped_column(Text, default="")

    # Relationships
    owner: Mapped["User"] = relationship(
        back_populates="managed_channels",
        foreign_keys=[owner_id],
    )
    donors: Mapped[list["DonorChannel"]] = relationship(
        back_populates="managed_channel",
        cascade="all, delete-orphan",
    )
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="managed_channel",
        cascade="all, delete-orphan",
    )
    ideas: Mapped[list["Idea"]] = relationship(
        back_populates="managed_channel",
        cascade="all, delete-orphan",
    )


class DonorChannel(Base, TimestampMixin):
    """Donor channel for content inspiration."""

    __tablename__ = "donor_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscribers_count: Mapped[Optional[int]] = mapped_column(nullable=True)

    # Relation to managed channel
    managed_channel_id: Mapped[int] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="CASCADE")
    )

    status: Mapped[DonorStatus] = mapped_column(
        Enum(DonorStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=DonorStatus.ACTIVE
    )
    last_parsed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    managed_channel: Mapped["ManagedChannel"] = relationship(back_populates="donors")
    posts: Mapped[list["DonorPost"]] = relationship(
        back_populates="donor",
        cascade="all, delete-orphan",
    )


class DonorPost(Base, TimestampMixin):
    """Parsed post from donor channel."""

    __tablename__ = "donor_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    donor_id: Mapped[int] = mapped_column(
        ForeignKey("donor_channels.id", ondelete="CASCADE")
    )
    post_id: Mapped[int] = mapped_column()  # Telegram post ID
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    views: Mapped[int] = mapped_column(default=0)
    reactions: Mapped[int] = mapped_column(default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    donor: Mapped["DonorChannel"] = relationship(back_populates="posts")

    # Unique constraint
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )


class Idea(Base, TimestampMixin):
    """Generated idea for content."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True)
    managed_channel_id: Mapped[int] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="CASCADE")
    )

    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="llm")  # llm, donor, trend, user_input
    status: Mapped[IdeaStatus] = mapped_column(
        Enum(IdeaStatus, values_callable=lambda obj: [e.value for e in obj]), 
        default=IdeaStatus.NEW
    )

    # Relationships
    managed_channel: Mapped["ManagedChannel"] = relationship(back_populates="ideas")


class Draft(Base, TimestampMixin):
    """Post draft."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    managed_channel_id: Mapped[int] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="CASCADE")
    )

    # Content
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text)

    # Source idea (optional)
    idea_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ideas.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Status
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=DraftStatus.DRAFT
    )
    media_position: Mapped[str] = mapped_column(String(20), default="top")  # top, bottom

    # AI image generation context
    last_image_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    temp_image_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    managed_channel: Mapped["ManagedChannel"] = relationship(back_populates="drafts")
    idea: Mapped[Optional["Idea"]] = relationship()
    media: Mapped[list["DraftMedia"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )
    scheduled_post: Mapped[Optional["ScheduledPost"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )


class DraftMedia(Base, TimestampMixin):
    """Media attached to draft."""

    __tablename__ = "draft_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE")
    )

    file_id: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, values_callable=lambda obj: [e.value for e in obj]),
        default=MediaType.PHOTO
    )
    position: Mapped[int] = mapped_column(default=0)  # Order in album

    # Relationships
    draft: Mapped["Draft"] = relationship(back_populates="media")


class ScheduledPost(Base, TimestampMixin):
    """Scheduled post for publication."""

    __tablename__ = "scheduled_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"),
        unique=True,
    )

    scheduled_at: Mapped[datetime] = mapped_column()
    sent_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    status: Mapped[ScheduledPostStatus] = mapped_column(
        Enum(ScheduledPostStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=ScheduledPostStatus.PLANNED
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0)

    # Relationships
    draft: Mapped["Draft"] = relationship(back_populates="scheduled_post")


# ============================================================
# SPACES (Пространства для материалов)
# ============================================================


class Space(Base, TimestampMixin):
    """Space for collecting materials on a topic."""

    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[SpaceStatus] = mapped_column(
        Enum(SpaceStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=SpaceStatus.COLLECTING,
    )

    # Generated content from materials
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_ideas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of ideas

    # Relationships
    user: Mapped["User"] = relationship()
    channel: Mapped[Optional["ManagedChannel"]] = relationship()
    materials: Mapped[list["SpaceMaterial"]] = relationship(
        back_populates="space",
        cascade="all, delete-orphan",
    )


class SpaceMaterial(Base, TimestampMixin):
    """Material in a space (document, audio, link, etc.)."""

    __tablename__ = "space_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE")
    )

    material_type: Mapped[SpaceMaterialType] = mapped_column(
        Enum(SpaceMaterialType, values_callable=lambda obj: [e.value for e in obj]),
    )

    # File storage (for uploaded files)
    file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Content
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Original text or URL
    processed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Extracted/transcribed text

    # Metadata
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="ru")
    is_processed: Mapped[bool] = mapped_column(default=False)
    is_selected: Mapped[bool] = mapped_column(default=True)  # Selected for idea generation
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    space: Mapped["Space"] = relationship(back_populates="materials")


# ============================================================
# CONTENT PLAN (Контент-план)
# ============================================================


class ContentPlan(Base, TimestampMixin):
    """Weekly content plan for a channel."""

    __tablename__ = "content_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="CASCADE")
    )

    week_start: Mapped[datetime] = mapped_column()  # Monday of the week
    status: Mapped[ContentPlanStatus] = mapped_column(
        Enum(ContentPlanStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=ContentPlanStatus.DRAFT,
    )

    # Relationships
    channel: Mapped["ManagedChannel"] = relationship()
    slots: Mapped[list["ContentPlanSlot"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class ContentPlanSlot(Base, TimestampMixin):
    """Single slot in a content plan."""

    __tablename__ = "content_plan_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("content_plans.id", ondelete="CASCADE")
    )

    day_of_week: Mapped[int] = mapped_column()  # 0=Monday, 6=Sunday
    time: Mapped[str] = mapped_column(String(5))  # "HH:MM" format
    topic: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Link to created draft (optional)
    draft_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("drafts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    plan: Mapped["ContentPlan"] = relationship(back_populates="slots")
    draft: Mapped[Optional["Draft"]] = relationship()


# ============================================================
# ANALYTICS (Аналитика канала)
# ============================================================


class ChannelStats(Base, TimestampMixin):
    """Daily channel statistics snapshot."""

    __tablename__ = "channel_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("managed_channels.id", ondelete="CASCADE")
    )

    date: Mapped[datetime] = mapped_column()  # Date of the snapshot
    subscribers_count: Mapped[int] = mapped_column()

    # Relationships
    channel: Mapped["ManagedChannel"] = relationship()

    __table_args__ = (
        # Unique constraint: one stats entry per channel per day
        {"sqlite_autoincrement": True},
    )
