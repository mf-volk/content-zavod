"""Ideas generation handlers."""

from __future__ import annotations

import logging
from typing import Optional
from datetime import datetime
import asyncio
import random
import html


from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import User, ManagedChannel, DonorChannel, DonorPost, Idea, IdeaStatus, Draft, DraftStatus
from app.llm_client import llm_client
from app.handlers.keyboards import (
    ideas_list_keyboard,
    idea_selected_keyboard,
    back_to_menu_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="ideas")


async def get_current_channel(session: AsyncSession, tg_user_id: int) -> Optional[ManagedChannel]:
    """Get user's current selected channel."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.current_channel))
    )
    user = result.scalar_one_or_none()
    return user.current_channel if user else None


from app.utils import answer_nav

@router.callback_query(F.data == "ideas:list")
async def list_ideas(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Show ideas list (recent 3) in card format."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.message.edit_text(
            "⚠️ Сначала выбери канал для работы.",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    # Get recent new ideas (limit 3 for valid layout)
    result = await session.execute(
        select(Idea)
        .where(
            Idea.managed_channel_id == channel.id,
            Idea.status == IdeaStatus.NEW,
        )
        .order_by(Idea.created_at.desc())
        .limit(3)
    )
    ideas = result.scalars().all()
    # Reverse to show chronological order or just keep recent? 
    # Usually "Ideas for today" implies a set. Let's keep them as is but formatted.
    # Note: If we just fetched 3 newest, maybe we want to show them 1-2-3 properly.
    
    if not ideas:
        text = (
            f"💡 <b>Идеи для канала {html.escape(channel.title)}</b>\n\n"
            "Актуальных идей нет.\n"
            "Нажми «Сгенерировать», чтобы получить новые."
        )
        # Empty list keyboard just with Generate button
        await answer_nav(
            callback=callback,
            label="💡 Идеи",
            new_text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Сгенерировать", callback_data="ideas:generate")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")]
            ]),
        )
        return

    # Format message
    full_message_text = f"💡 <b>Идеи для канала {html.escape(channel.title)}</b>\n\nДоступно идей: {len(ideas)}\n\n" + ("_" * 30) + "\n\n"
    
    for i, idea in enumerate(ideas, 1):
        full_message_text += (
            f"<b>Идея {i}:</b>\n"
            f"⚡ {html.escape(idea.title)}\n\n"
            f"{idea.description}\n\n"
            f"{'_' * 30}\n\n"
        )
    
    full_message_text += "👇 <b>Выбери для создания поста:</b>"
    
    buttons_data = [(idea.id, i) for i, idea in enumerate(ideas, 1)]
    
    await answer_nav(
        callback=callback,
        label="💡 Идеи",
        new_text=full_message_text,
        reply_markup=ideas_list_keyboard(buttons_data),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "ideas:generate")
async def generate_ideas(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Generate curated ideas using LLM."""
    channel = await get_current_channel(session, callback.from_user.id)

    if not channel:
        await callback.answer("⚠️ Канал не выбран", show_alert=True)
        return

    # 1. Clear buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    
    # 2. Echo
    topic_info = f" (Тема: {channel.idea_topic})" if channel.idea_topic else ""
    archive_info = " (Архив)" if channel.idea_source_type == "archive" else ""
    await callback.message.answer(f"🔄 Сгенерировать идеи{topic_info}{archive_info}")

    # 3. Progress
    progress_msg = await callback.message.answer(
        "🔄 <b>Анализирую ленту...</b>\n\n"
        "Читаю посты конкурентов, считаю Engagement Rate и отбираю лучшее.\n"
        "Жди, это магия ✨",
        parse_mode="HTML",
    )

    # Loop to allow auto-reset history if no posts found
    from sqlalchemy import delete

    # Initialize variables to prevent scope issues
    donor_posts = []
    top_posts = []

    for attempt in range(2):
        # 4. Mark previous NEW ideas as SKIPPED (only on first attempt or if we want to ensure clean state)
        if attempt == 0:
            await session.execute(
                update(Idea)
                .where(
                    Idea.managed_channel_id == channel.id, 
                    Idea.status == IdeaStatus.NEW
                )
                .values(status=IdeaStatus.SKIPPED)
            )
            await session.commit()

        # Get donor posts
        result = await session.execute(
            select(DonorChannel)
            .where(DonorChannel.managed_channel_id == channel.id)
            .options(selectinload(DonorChannel.posts))
        )
        donors = result.scalars().all()

        # Get already used post IDs (only USED, not SKIPPED) to avoid showing duplicates
        # SKIPPED ideas can be shown again after exhausting all other posts
        existing_ideas_result = await session.execute(
            select(Idea.source)
            .where(
                Idea.managed_channel_id == channel.id,
                Idea.source.like("donor_curation_%"),
                Idea.status.in_([IdeaStatus.USED, IdeaStatus.SKIPPED])
            )
        )
        
        used_post_ids = set()
        for source_val in existing_ideas_result.scalars().all():
            if source_val:
                try:
                    p_id = int(source_val.split("_")[-1])
                    used_post_ids.add(p_id)
                except (ValueError, IndexError):
                    continue

        # 5. Collect and Filter Posts
        from datetime import timedelta
        
        donor_posts = []
        fallback_posts = []
        posts_map = {}

        cutoff_date = datetime.utcnow() - timedelta(days=30)

        for donor in donors:
            for post in donor.posts:
                # If date is missing, treat as recent/valid
                # if not post.published_at: continue

                if post.id in used_post_ids:
                    continue

                # Filter out content-less posts (images/videos without description)
                # LLM cannot analyze them, leading to "No data" ideas.
                text_content = (post.text or "").strip()
                title_content = (post.title or "").strip()
                
                # Check if it's a generic title ("Без заголовка" or empty)
                is_generic_title = not title_content or title_content == "Без заголовка"
                
                # If text is very short AND title is generic -> Skip
                if len(text_content) < 5 and is_generic_title:
                    continue

                views = post.views if post.views > 0 else 1
                er_score = (post.reactions * 100) / views
                base_score = er_score + (post.reactions / 500)

                # Recency factor: fresh posts get a boost, old ones decay
                post_date = post.published_at or post.created_at
                if post_date:
                    days_old = (datetime.utcnow() - post_date).days
                    recency_factor = max(0.2, 1.0 - (days_old / 90))
                else:
                    recency_factor = 0.3  # No date at all

                score = base_score * recency_factor

                entry = {
                    "id": post.id,
                    "donor_id": post.donor_id, # Track donor ID for diversity
                    "source_name": donor.username, # Track source name for LLM context
                    "title": post.title or "Без заголовка",
                    # Clean tags like [Video] for LLM context if text is mixed? 
                    # Actually keeping them is fine if we passed filter, but we already filtered!
                    "text": post.text[:500] if post.text else "",
                    "views": post.views,
                    "reactions": post.reactions,
                    "score": score, 
                    "date": post.published_at,
                }

                # Date filter
                # If published_at is None, assume RECENT
                is_recent = True
                if post.published_at and post.published_at < cutoff_date:
                    is_recent = False
                
                if channel.idea_source_type == "recent":
                     if is_recent:
                         donor_posts.append(entry)
                     else:
                         fallback_posts.append(entry) # Archive posts
                elif channel.idea_source_type == "archive":
                     if not is_recent:
                         donor_posts.append(entry)
                     # In archive mode, we arguably don't need 'fallback' to recent? 
                     # Or maybe we do. Let's keep recent as fallback if archive empty?
                     # For now, standard logic.
                
                # Keep map for linking later
                posts_map.setdefault(post.id, {"obj": post, "donor_username": donor.username})

        # --- Post-Processing & Deep Search ---
        
        # 1. Enrichment: If in RECENT mode but result is sparse (< 5), 
        #    mix in best Archive posts (Deep Search without topic)
        if channel.idea_source_type == "recent" and len(donor_posts) < 5 and fallback_posts:
            # Sort fallback by score to get best archive
            fallback_posts.sort(key=lambda x: x["score"], reverse=True)
            # Append top 10 from archive
            donor_posts.extend(fallback_posts[:10])
        
        # 2. Topic Filtering (Smart)
        # DISABLED BY USER REQUEST
        # if channel.idea_topic:
        #     keywords = [k.strip().lower() for k in channel.idea_topic.split(",")]
        #     pk_posts = []
        #     
        #     # Check current donor_posts (which might include enriched archive now)
        #     for p in donor_posts:
        #         text_lower = (p["title"] + " " + p["text"]).lower()
        #         if any(k in text_lower for k in keywords):
        #             pk_posts.append(p)
        #     
        #     if pk_posts:
        #          donor_posts = pk_posts
        #     else:
        #          # 3. Deep Search (Strict): If no matches yet, scan FULL fallback/archive 
        #          # (in case enrichment didn't pick up the relevant ones)
        #          deep_matches = []
        #          for p in fallback_posts:
        #             text_lower = (p["title"] + " " + p["text"]).lower()
        #             if any(k in text_lower for k in keywords):
        #              if any(k in text_lower for k in keywords):
        #                  deep_matches.append(p)
        #          if deep_matches:
        #              donor_posts = deep_matches
        #          else:
        #              # No matches anywhere. 
        #              # Reset donor_posts to empty so we trigger "No posts found" error
        #              donor_posts = []

        # 3. Diversity Enrichment (Deep Search for Missing Sources)
        # If we have mainly one source but others exist in archive (fallback), pull them in.
        # Ideally we want at least 2-3 sources.
        if donor_posts: 
            present_donor_ids = {p["donor_id"] for p in donor_posts}
            if len(present_donor_ids) < len(donors) and fallback_posts:
                # We have missing donors. Try to find them in fallback (archive)
                missing_donors_ids = {d.id for d in donors} - present_donor_ids
                
                added_diversity = 0
                for d_id in missing_donors_ids:
                    # Find best posts for this missing donor from fallback
                    candidates = [p for p in fallback_posts if p["donor_id"] == d_id]
                    if candidates:
                        # Sort by score
                        candidates.sort(key=lambda x: x["score"], reverse=True)
                        # Take top 3 to mix in
                        donor_posts.extend(candidates[:3])
                        added_diversity += 1
                
                if added_diversity > 0:
                     logger.info(f"Diversity Enrichment: Added archive posts from {added_diversity} missing donors.")

        if donor_posts:
            # Found posts, break attempt loop and proceed to LLM
            break
        
        if attempt == 0:
             # Check if we have SKIPPED ideas (meaning we have used some before)
            skipped_check = await session.execute(
                select(Idea)
                .where(
                    Idea.managed_channel_id == channel.id,
                    Idea.status == IdeaStatus.SKIPPED
                )
                .limit(1)
            )
            # If we have skipped ideas, it means we might have missed something or user wants to review old ones.
            # But the user EXPLICITLY requested: "We looked at everything... Change donors".
            # So, instead of auto-resetting silently or just resetting, we should WARN the user.
            # BUT, if we don't reset, the user sees "No posts".
            # Let's do a ONE-TIME auto-reset if we haven't warned yet?
            # Actually, user logic: "Best -> All -> Archive -> Message".
            # My logic: "Recent (Best->All)" -> "Archive (Best->All)" -> "Message".
            # This matches.
            # The issue is `attempt=0` causing a reset loop or message.
            # If `attempt=0` logic was for "Auto Reset History", I should Change it to:
            # If we are here, it means we exhausted EVERYTHING (Recent & Archive) because "Deep Search" covers archive.
            
            # Use global `ideas_list_keyboard`
            
            # If we really found nothing even after deep search:
            msg_text = (
                "🏁 <b>Мы посмотрели все посты ваших доноров</b>\n"
                "(и свежие, и архивные)\n\n"
                "Если идей маловато — попробуйте:\n"
                "1. Добавить <b>новых доноров</b>\n"
                "2. Сменить или убрать тему\n"
                "3. Подождать новых публикаций\n\n"
                "<i>Мы работаем только с тем, что публикуют ваши доноры.</i>"
            )
            
            # We can offer to reset history manually just in case
            await progress_msg.delete()
            await callback.message.answer(
                msg_text,
                reply_markup=ideas_list_keyboard([]), # Shows Generate / Settings / Menu
                parse_mode="HTML",
            )
            return

    # 6. Sort & LLM
    donor_posts.sort(key=lambda x: x["score"], reverse=True)
    
    # Diversity Logic: Round Robin Selection
    # Group by donor
    posts_by_donor = {}
    for p in donor_posts:
        d_id = p.get("donor_id", 0)
        if d_id not in posts_by_donor:
            posts_by_donor[d_id] = []
        posts_by_donor[d_id].append(p)
    
    # Debug logging
    stats = {did: len(posts) for did, posts in posts_by_donor.items()}
    logger.info(f"Candidates distribution before RR: {stats}")

    top_posts = []
    donors_ids = list(posts_by_donor.keys())
    
    # Pick one from each donor until we have 20 or run out
    while len(top_posts) < 20 and donors_ids:
        for d_id in list(donors_ids): # Iterate copy to allow removal
            if posts_by_donor[d_id]:
                top_posts.append(posts_by_donor[d_id].pop(0))
                if len(top_posts) >= 20:
                    break
            else:
                donors_ids.remove(d_id)
                
    # Final shuffle isn't needed, LLM can handle list, but let's keep it ordered by score effectively (interleaved)

    try:
        # STRICT DIVERSITY LOGIC
        # If we have multiple donors, we want to force diversity by generating per-donor
        num_donors = len(posts_by_donor)
        ideas_data = []

        if num_donors >= 3:
            # Optimize: If we have many donors (e.g. 20), don't call ALL of them.
            # Pick top 5 donors based on their best post's score.
            
            # 1. Calc max score for each donor
            donor_scores = []
            for d_id, d_posts in posts_by_donor.items():
                if not d_posts:
                    continue
                best_score = max(p.get("score", 0) for p in d_posts)
                donor_scores.append((d_id, best_score))
            
            # 2. Sort available donors by quality (Best Score Descending)
            donor_scores.sort(key=lambda x: x[1], reverse=True)
            
            # --- STATEFUL ROTATION LOGIC ---
            
            # 1. Select TOP Donor (Rotating 1..10)
            top_index = channel.next_top_rank % min(10, len(donor_scores))
            # If index out of bounds (e.g. fewer than 10 donors), wrap around
            if top_index >= len(donor_scores):
                top_index = 0
                channel.next_top_rank = 0
            
            top_donor_tuple = donor_scores[top_index]
            selected_top_id = top_donor_tuple[0]
            
            # Update next rank
            channel.next_top_rank = (channel.next_top_rank + 1) % min(10, len(donor_scores))
            
            # 2. Select RANDOM Donors (Rotating through pool)
            # Parse used IDs from string "id1,id2,id3"
            used_ids_str = channel.used_random_donor_ids or ""
            used_ids = set()
            if used_ids_str:
                try:
                    used_ids = {int(x) for x in used_ids_str.split(",") if x.strip()}
                except:
                    used_ids = set()
            
            # Candidates = All donors EXCEPT current Top Donor
            # (We exclude current Top because we already picked it)
            # (We also exclude previously seen Randoms to ensure exhaustion)
            
            all_donor_ids = [d[0] for d in donor_scores]
            candidate_randoms = [did for did in all_donor_ids if did != selected_top_id]
            
            # Filter out used
            available_randoms = [did for did in candidate_randoms if did not in used_ids]
            
            picks_needed = 2
            selected_random_ids = []
            
            # If not enough unused randoms, reset usage history
            if len(available_randoms) < picks_needed:
                # Reset
                used_ids = set()
                available_randoms = candidate_randoms # refresh
                # Update DB state for reset
                channel.used_random_donor_ids = ""
            
            # Pick Randoms
            if available_randoms:
                # Shuffle available
                sample = random.sample(available_randoms, k=min(picks_needed, len(available_randoms)))
                selected_random_ids.extend(sample)
                # Add to used
                for rid in sample:
                    used_ids.add(rid)
            
            # Save new used state
            channel.used_random_donor_ids = ",".join(map(str, used_ids))
            
            # Commit state changes (rank and history)
            await session.commit()
            
            # Combine Final Selection
            final_donor_ids = [selected_top_id] + selected_random_ids
            
            # Extract IDs for Parallel Task
            top_donor_ids = final_donor_ids
            
            # Parallel generation: 1 idea from each of the Top 5 donors
            tasks = []
            # Release DB lock before parallel requests
            await session.commit()
            
            for d_id in top_donor_ids:
                d_posts = posts_by_donor[d_id]
                # Take top 5 posts from this donor as candidates
                candidates = d_posts[:5]
                tasks.append(llm_client.generate_ideas(
                    tone_of_voice=channel.tone_of_voice,
                    donor_posts=candidates,
                    count=1, # Strict 1 per donor
                    language=channel.language,
                    topic=channel.idea_topic,
                ))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    ideas_data.extend(res)
                else:
                    logger.error(f"Parallel idea generation failed for a donor: {res}")
            
        elif num_donors == 2:
            # Parallel generation: 2 ideas from each donor (total 4, pick 3)
            tasks = []
            # Release DB lock before parallel requests
            await session.commit()
            
            for d_id, d_posts in posts_by_donor.items():
                candidates = d_posts[:8]
                tasks.append(llm_client.generate_ideas(
                    tone_of_voice=channel.tone_of_voice,
                    donor_posts=candidates,
                    count=2, 
                    language=channel.language,
                    topic=channel.idea_topic,
                ))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    ideas_data.extend(res)

        else:
            # Single donor: Standard generation
            # Release DB lock
            await session.commit()
            
            ideas_data = await llm_client.generate_ideas(
                tone_of_voice=channel.tone_of_voice,
                donor_posts=top_posts,
                count=3,
                language=channel.language,
                topic=channel.idea_topic, 
            )

        # Post-Processing: Sort by Original Post Score
        # Ideas don't have scores, but they link to posts that do.
        # Post-Processing: Sort by Original Post Score
        # Ideas don't have scores, but they link to posts that do.
        if num_donors >= 3:
            # We already selected specific donors (1 Best + Randoms).
            # Do NOT re-sort by score, otherwise Big Channels will always float to top.
            # We want to preserve the "Best + Random" mix we intentionally built.
            # ideas_data is already [Best, R1, R2, R3, R4] roughly.
            # Just take top 3.
            ideas_data = ideas_data[:3]
            
        elif num_donors == 2:
            # We generated [A1, A2] and [B1, B2].
            # We want [A1, B1, BestRemaining].
            # Group by donor
            ideas_by_donor = {}
            for idea in ideas_data:
                # Find which donor this idea belongs to
                d_id = None
                if idea.source_post_id and idea.source_post_id in posts_map:
                    # Reverse lookup donor ID from post
                    post_obj = posts_map[idea.source_post_id]["obj"]
                    d_id = post_obj.donor_id
                
                if d_id:
                    ideas_by_donor.setdefault(d_id, []).append(idea)
            
            # Interleave
            final_list = []
            d_ids = list(ideas_by_donor.keys())
            
            # Round 1: Take 1 from each
            for did in d_ids:
                if ideas_by_donor[did]:
                    final_list.append(ideas_by_donor[did].pop(0))
            
            # Round 2: Take remaining (sorted by score?)
            remaining = []
            for did in d_ids:
                remaining.extend(ideas_by_donor[did])
            
            # Sort remaining by score
            def get_idea_score_simple(idea_dto):
                if idea_dto.source_post_id and idea_dto.source_post_id in posts_map:
                    return posts_map[idea_dto.source_post_id]["obj"].reactions
                return 0
            remaining.sort(key=get_idea_score_simple, reverse=True)
            
            final_list.extend(remaining)
            ideas_data = final_list[:3]

    except Exception as e:
        logger.error(f"Failed to generate ideas: {e}")
        await progress_msg.delete()
        await callback.message.answer(
            "❌ Ошибка API. Возможно, неверный ключ OpenAI.",
            reply_markup=ideas_list_keyboard([]),
        )
        return

    if not ideas_data:
        await progress_msg.delete()
        await callback.message.answer(
            "❌ Не удалось придумать идеи. Попробуй позже.",
            reply_markup=ideas_list_keyboard([]),
        )
        return

    # Check if generated ideas are from previously skipped posts
    source_post_ids = [dto.source_post_id for dto in ideas_data if dto.source_post_id]
    if source_post_ids:
        skipped_sources_check = await session.execute(
            select(Idea.source)
            .where(
                Idea.managed_channel_id == channel.id,
                Idea.status == IdeaStatus.SKIPPED,
                Idea.source.in_([f"donor_curation_{pid}" for pid in source_post_ids])
            )
        )
        skipped_sources = skipped_sources_check.scalars().all()
        showing_repeats = len(skipped_sources) > 0
    else:
        showing_repeats = False

    # Process and save ideas
    new_ideas = []

    # Add warning if showing previously skipped ideas
    if showing_repeats:
        full_message_text = (
            "♻️ <b>Идеи повторяются</b>\n\n"
            "Мы показали все новые посты ваших доноров.\n"
            "Сейчас предлагаем ранее отклонённые варианты — может, теперь подойдут?\n\n"
            "💡 <b>Для разнообразия:</b>\n"
            "• Добавьте новых доноров (📚 Доноры → ➕ Добавить)\n"
            "• Измените тему в настройках идей\n"
            "• Подождите новых публикаций от доноров\n\n"
            + ("_" * 30) + "\n\n"
        )
    else:
        full_message_text = "<b>Приветствую! 👋</b>\n\nВот три идеи для постов на сегодня:\n\n" + ("_" * 30) + "\n\n"

    for i, dto in enumerate(ideas_data, 1):
        extra_info = ""
        original_link = ""
        
        if dto.source_post_id and dto.source_post_id in posts_map:
            p_data = posts_map[dto.source_post_id]
            post = p_data["obj"]
            username = p_data["donor_username"]
            
            # Format date and stats
            stats_lines = []
            stats_lines.append(f"👁 Просмотры: {post.views:,}")
            stats_lines.append(f"❤️ Реакции: {post.reactions:,}")
            
            if post.published_at:
                stats_lines.append(f"📅 {post.published_at.strftime('%d.%m.%Y')}")
            
            stats_block = "\n".join(stats_lines)
            
            extra_info = (
                f"\n\n📊 <b>Статистика:</b>\n"
                f"{stats_block}"
            )
            original_link = f"\n\n🔗 <a href='https://t.me/{username}/{post.post_id}'>Оригинал</a>"

        # Rich description for DB
        # Escape LLM output to prevent HTML errors
        safe_desc = html.escape(dto.description or "")
        safe_why = html.escape(dto.why_relevant or "")
        
        rich_description = (
            f"{safe_desc}"
            f"{extra_info}\n\n"
            f"💬 <b>Почему стоит:</b> {safe_why}"
            f"{original_link}"
        )

        idea_display_text = (
            f"<b>Идея {i}:</b>\n"
            f"⚡ {html.escape(dto.title)}\n\n"
            f"{rich_description}"
        )

        full_message_text += idea_display_text + "\n\n" + ("_" * 30) + "\n\n"

        # Save to DB
        idea = Idea(
            managed_channel_id=channel.id,
            title=dto.title,
            description=rich_description,
            source=f"donor_curation_{dto.source_post_id}" if dto.source_post_id else "llm",
            status=IdeaStatus.NEW,
        )
        session.add(idea)
        new_ideas.append(idea)

    await session.commit()

    full_message_text += "👇 <b>Пишем пост на тему?</b>"

    buttons_data = [(idea.id, i) for i, idea in enumerate(new_ideas, 1)]
    
    await progress_msg.delete()
    await callback.message.answer(
        full_message_text,
        reply_markup=ideas_list_keyboard(buttons_data),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@router.callback_query(F.data.startswith("ideas:select:"))
async def select_idea(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """View selected idea details (pre-draft stage)."""
    idea_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Idea).where(Idea.id == idea_id)
    )
    idea = result.scalar_one_or_none()

    if not idea:
        await callback.answer("❌ Идея не найдена", show_alert=True)
        return

    # Show confirmation before generating draft
    await answer_nav(
        callback=callback,
        label=idea.title[:30] + "..." if len(idea.title) > 30 else idea.title,
        new_text=(
            f"⚡ <b>Выбрана идея:</b>\n{idea.title}\n\n"
            "Нажми «Написать пост», чтобы сгенерировать черновик."
        ),
        reply_markup=idea_selected_keyboard(idea.id),
    )


@router.callback_query(F.data.startswith("ideas:create_draft:"))
async def create_draft_from_idea(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Create draft from selected idea."""
    idea_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Idea).where(Idea.id == idea_id)
    )
    idea = result.scalar_one_or_none()

    if not idea:
        await callback.answer("❌ Идея не найдена", show_alert=True)
        return

    channel = await get_current_channel(session, callback.from_user.id)
    if not channel:
        await callback.answer("⚠️ Канал не выбран", show_alert=True)
        return

    # Start progress
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    
    await callback.message.answer("✏️ Написать пост")
    # Show loading message with Cancel button
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data=f"ideas:cancel_gen:{idea.id}")]]
    )
    
    progress_msg = await callback.message.answer(
        f"⏳ <b>Создание поста...</b>\n\n"
        f"Идея: {idea.title}\n\n"
        f"Генерирую текст поста с учётом стиля канала.",
        reply_markup=cancel_keyboard,
        parse_mode="HTML",
    )

    # Clean description from technical info for LLM
    clean_description = idea.description or ""
    # Remove stats block and everything after (stats usually starts with 📊)
    if "📊" in clean_description:
        clean_description = clean_description.split("📊")[0].strip()
    
    # Just in case stats are missing but other blocks exist (why relevant starts with 💬)
    if "💬" in clean_description:
         clean_description = clean_description.split("💬")[0].strip()

    # Generate draft content
    try:
        # Release DB lock before LLM call
        await session.commit()
        
        draft_data = await llm_client.generate_draft(
            idea_title=idea.title,
            idea_description=clean_description,
            tone_of_voice=channel.tone_of_voice,
            language=channel.language,
        )
    except Exception as e:
        logger.error(f"Failed to generate draft: {e}")
        await progress_msg.delete()
        await callback.message.answer(
            "❌ Ошибка при генерации поста. Попробуй позже.",
            reply_markup=idea_selected_keyboard(idea.id),
        )
        return

    if not draft_data:
        await callback.message.edit_text(
            "❌ Не удалось сгенерировать текст. Попробуй позже.",
            reply_markup=idea_selected_keyboard(idea.id),
        )
        return

    # Create draft
    from app.utils import sanitize_html
    
    clean_title = sanitize_html(draft_data.title)
    clean_content = sanitize_html(draft_data.content)
    
    # Deduplicate: if content starts with title, remove it
    if clean_content.lower().startswith(clean_title.lower()):
        # Remove title and potential following newlines/tags
        clean_content = clean_content[len(clean_title):].strip()
        # Clean up leading <br> or newlines that might remain
        while clean_content.startswith("<br>") or clean_content.startswith("\n"):
             if clean_content.startswith("<br>"):
                  clean_content = clean_content[4:].strip()
             if clean_content.startswith("\n"):
                  clean_content = clean_content[1:].strip()

    draft = Draft(
        managed_channel_id=channel.id,
        title=clean_title,
        content=clean_content,
        idea_id=idea.id,
        status=DraftStatus.DRAFT,
    )
    session.add(draft)

    # Mark idea as used
    idea.status = IdeaStatus.USED

    await session.commit()

    # Import here to avoid circular imports
    from app.handlers.keyboards import draft_edit_keyboard
    from aiogram.exceptions import TelegramBadRequest

    try:
        await progress_msg.delete()
        await callback.message.answer(
            f"✅ <b>Черновик создан!</b>\n\n"
            f"{draft.content}",
            reply_markup=draft_edit_keyboard(draft.id, has_media=False),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.error(f"HTML parsing failed for draft {draft.id}: {e}")
        # Fallback: Send as plain text (HTML tags visible or stripped depending on intent, 
        # but here we just drop parse_mode to be safe, so user can at least see it)
        # Actually better to strip tags for the fallback view so it doesn't look like code.
        # let's just turn off parse_mode so it doesn't crash.
        await progress_msg.delete()
        await callback.message.answer(
            f"✅ <b>Черновик создан!</b> (Форматирование отключено из-за ошибки)\n\n"
            f"{draft.content}",
            reply_markup=draft_edit_keyboard(draft.id, has_media=False),
            parse_mode=None,
        )


@router.callback_query(F.data.startswith("ideas:delete:"))
async def delete_idea(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Delete idea."""
    idea_id = int(callback.data.split(":")[-1])

    result = await session.execute(
        select(Idea).where(Idea.id == idea_id)
    )
    idea = result.scalar_one_or_none()

    if not idea:
        await callback.answer("❌ Идея не найдена", show_alert=True)
        return

    await session.delete(idea)
    await session.commit()

    # Logic:
    # 1. Remove buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    # 2. Echo
    await callback.message.answer("🗑 Удалить идею")
    
    # 3. List
    await list_ideas(callback, session)
