"""Background scheduler for publishing scheduled posts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    ScheduledPost,
    ScheduledPostStatus,
    Draft,
    DraftStatus,
    DonorChannel,
    DonorStatus,
    DonorPost,
    ManagedChannel,
    ChannelStats,
)
from app.db.session import async_session_factory
from app.donor_parser import parse_channel
from app.services.publisher import publish_content

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

async def run_background_parser(bot: Bot | None = None) -> None:
    """Background task to parse donor channels periodically."""
    logger.info("Background parser started")
    
    # Initial delay to let bot start up and not block immediate operations
    await asyncio.sleep(60)

    while True:
        try:
            logger.info("Starting scheduled donor parsing cycle...")
            logger.info("Starting scheduled donor parsing cycle...")
            
            # 1. Fetch all donor IDs first (Quick Read)
            donor_ids = []
            async with async_session_factory() as session:
                result = await session.execute(
                    select(DonorChannel.id).where(DonorChannel.status != DonorStatus.ERROR)
                )
                donor_ids = result.scalars().all()
            
            logger.info(f"Found {len(donor_ids)} donors to check.")
            
            # 2. Process each donor in separate session
            for d_id in donor_ids:
                try:
                    async with async_session_factory() as session:
                        # Re-fetch donor to attach to session
                        donor = await session.get(DonorChannel, d_id)
                        if not donor:
                            continue

                        # Log progress to make it visible
                        logger.info(f"Background parser: processing @{donor.username}")

                        # Parse (HTTP + CPU offloaded)
                        parsed = await parse_channel(donor.username)
                        
                        if not parsed:
                            logger.warning(f"Failed to parse @{donor.username} in background")
                            continue
                            
                        # Update stats
                        donor.subscribers_count = parsed.subscribers_count
                        donor.last_parsed_at = datetime.utcnow()
                        
                        # Get existing IDs
                        res_ids = await session.execute(
                            select(DonorPost.post_id).where(DonorPost.donor_id == donor.id)
                        )
                        existing_ids = {row[0] for row in res_ids.fetchall()}

                        # Get existing posts with NULL published_at for date backfill
                        null_date_result = await session.execute(
                            select(DonorPost).where(
                                DonorPost.donor_id == donor.id,
                                DonorPost.published_at.is_(None)
                            )
                        )
                        null_date_posts = {dp.post_id: dp for dp in null_date_result.scalars().all()}

                        new_count = 0
                        updated_count = 0
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
                            elif post.post_id in null_date_posts and post.published_at:
                                # Backfill published_at for existing posts
                                null_date_posts[post.post_id].published_at = post.published_at
                                updated_count += 1

                        await session.commit()
                        if new_count > 0 or updated_count > 0:
                            logger.info(f"Updated @{donor.username}: +{new_count} new, {updated_count} dates filled")
                        
                    # Polite delay between donors (Session is CLOSED here)
                    await asyncio.sleep(settings.parser_delay)
                        
                except Exception as e:
                    logger.error(f"Error processing donor {d_id}: {e}")
                    continue
                        
            logger.info("Donor parsing cycle completed.")
            
        except Exception as e:
            logger.error(f"Background parser loop error: {e}")

        # Wait 1 hour before next cycle
        await asyncio.sleep(3600)


async def _process_scheduled_post(
    bot: Bot, session: AsyncSession, post: ScheduledPost
) -> None:
    """Process a single scheduled post."""

    draft = post.draft
    managed_channel = draft.managed_channel if draft else None

    if not draft:
        post.status = ScheduledPostStatus.ERROR
        post.error_message = "Draft not found"
        await session.commit()
        return

    if not managed_channel:
        post.status = ScheduledPostStatus.ERROR
        post.error_message = "Managed channel not found"
        await session.commit()
        return

    try:
        await publish_content(bot, chat_id=managed_channel.tg_channel_id, draft=draft)
    except Exception as exc:  # noqa: BLE001 - we want to capture and store any error
        post.retry_count += 1
        post.error_message = str(exc)

        if post.retry_count >= MAX_RETRIES:
            post.status = ScheduledPostStatus.ERROR

        await session.commit()
        logger.error(
            "Failed to publish scheduled post %s (attempt %s/%s): %s",
            post.id,
            post.retry_count,
            MAX_RETRIES,
            exc,
        )
        return

    post.status = ScheduledPostStatus.SENT
    post.sent_at = datetime.utcnow()
    post.error_message = None
    draft.status = DraftStatus.PUBLISHED
    await session.commit()
    logger.info("Published scheduled post %s to channel %s", post.id, managed_channel.tg_channel_id)


async def run_scheduler(bot: Bot) -> None:
    """Check pending scheduled posts and publish them."""

    logger.info("Scheduler started [v2-Optimized]")

    while True:
        logger.debug("Scheduler: Loop tick start")
        try:
            logger.info("Scheduler: Checking for pending posts...")
            now = datetime.utcnow()
            async with async_session_factory() as session:
                logger.debug("Scheduler: session acquired")
                
                # Check connection
                try:
                     await session.execute(select(1))
                except Exception as e:
                     logger.error(f"Scheduler: DB connection failed: {e}")
                     raise e

                result = await session.execute(
                    select(ScheduledPost)
                    .where(ScheduledPost.status == ScheduledPostStatus.PLANNED)
                    .where(ScheduledPost.scheduled_at <= now)
                    .options(
                        selectinload(ScheduledPost.draft)
                        .selectinload(Draft.media),
                        selectinload(ScheduledPost.draft)
                        .selectinload(Draft.managed_channel),
                    )
                )

                posts = result.scalars().all()
                logger.debug(f"Scheduler: Found {len(posts)} candidates (raw)")

                if posts:
                    logger.info("Found %s scheduled posts ready to send", len(posts))
                else:
                    # Check for future posts to reassure user
                    future_result = await session.execute(
                        select(ScheduledPost)
                        .where(ScheduledPost.status == ScheduledPostStatus.PLANNED)
                        .order_by(ScheduledPost.scheduled_at)
                        .limit(1)
                    )
                    next_post = future_result.scalar_one_or_none()
                    if next_post:
                        time_until = next_post.scheduled_at - now
                        logger.info(
                            f"No posts ready. Next post ID {next_post.id} scheduled at {next_post.scheduled_at} UTC "
                            f"(in {time_until.total_seconds() / 60:.1f} min)"
                        )
                    else:
                        logger.info("No scheduled posts pending.")

                for post in posts:
                    logger.info(f"Scheduler: Processing post {post.id}...")
                    await _process_scheduled_post(bot, session, post)
                    logger.info(f"Scheduler: processed post {post.id}")

        except Exception as exc:  # noqa: BLE001 - log and continue the loop
            logger.error("Scheduler loop error: %s", exc, exc_info=True)

        logger.debug("Scheduler: Sleeping...")
        # Drift correction: Sleep only remaining time
        elapsed = (datetime.utcnow() - now).total_seconds()
        sleep_time = max(1.0, settings.scheduler_interval - elapsed)
        
        logger.debug(f"Scheduler: Sleeping {sleep_time:.1f}s (checking drift)")
        await asyncio.sleep(sleep_time)


async def collect_channel_stats(bot: Bot) -> None:
    """Daily task to collect subscriber stats for all managed channels."""
    logger.info("Channel stats collector started")

    # Initial delay
    await asyncio.sleep(120)

    while True:
        try:
            logger.info("Starting channel stats collection...")

            async with async_session_factory() as session:
                # Get all managed channels
                result = await session.execute(select(ManagedChannel))
                channels = result.scalars().all()

                collected = 0
                errors = 0

                for channel in channels:
                    try:
                        count = await bot.get_chat_member_count(channel.tg_channel_id)

                        today = datetime.utcnow().date()
                        today_datetime = datetime.combine(today, datetime.min.time())

                        # Check existing
                        from sqlalchemy import func
                        existing_result = await session.execute(
                            select(ChannelStats).where(
                                ChannelStats.channel_id == channel.id,
                                func.date(ChannelStats.date) == today,
                            )
                        )
                        existing = existing_result.scalar_one_or_none()

                        if existing:
                            existing.subscribers_count = count
                        else:
                            stats = ChannelStats(
                                channel_id=channel.id,
                                date=today_datetime,
                                subscribers_count=count,
                            )
                            session.add(stats)

                        collected += 1
                        await asyncio.sleep(0.5)  # Rate limiting

                    except Exception as e:
                        logger.error(f"Failed to collect stats for channel {channel.id}: {e}")
                        errors += 1

                await session.commit()
                logger.info(f"Channel stats collected: {collected} ok, {errors} errors")

        except Exception as e:
            logger.error(f"Channel stats collector error: {e}")

        # Run once per day (every 24 hours)
        await asyncio.sleep(24 * 3600)


def start_scheduler(bot: Bot) -> tuple[asyncio.Task, ...]:
    """Start scheduler, parser and stats collector as background tasks.

    Args:
        bot: Bot instance

    Returns:
        Tuple of background tasks.
    """

    scheduler_task = asyncio.create_task(run_scheduler(bot))
    parser_task = asyncio.create_task(run_background_parser(bot))
    stats_task = asyncio.create_task(collect_channel_stats(bot))

    return scheduler_task, parser_task, stats_task
