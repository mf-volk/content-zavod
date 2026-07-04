"""HTTP parser for public Telegram channels via t.me/s/ interface."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)


_parser_executor = None

@dataclass
class ParsedPost:
    """Parsed post data from donor channel."""

    post_id: int
    text: str
    title: str
    views: int = 0
    reactions: int = 0
    images: list[str] = field(default_factory=list)
    published_at: Optional[datetime] = None


@dataclass
class ParsedChannel:
    """Parsed channel data."""

    username: str
    subscribers_count: Optional[int] = None
    posts: list[ParsedPost] = field(default_factory=list)


def parse_count(text: str) -> int:
    """Parse count string with K/M suffixes to integer.

    Examples:
        "1.2K" -> 1200
        "3.5M" -> 3500000
        "500" -> 500
    """
    if not text:
        return 0

    text = text.strip().upper()

    try:
        if "K" in text:
            return int(float(text.replace("K", "").replace(",", ".")) * 1000)
        elif "M" in text:
            return int(float(text.replace("M", "").replace(",", ".")) * 1000000)
        else:
            return int(re.sub(r"[^\d]", "", text) or 0)
    except (ValueError, TypeError):
        return 0


def extract_post_id(html: str) -> Optional[int]:
    """Extract post ID from footer link."""
    match = re.search(
        r'<a class="tgme_widget_message_date" href="https://t\.me/[^/]+/(\d+)"',
        html,
    )
    if match:
        return int(match.group(1))
    return None


def extract_text(html: str) -> str:
    """Extract and clean text from post HTML."""
    match = re.search(
        r'<div class="tgme_widget_message_text[^>]*>([\s\S]*?)</div>',
        html,
    )
    if not match:
        return ""

    text = match.group(1)

    # Replace HTML entities and tags
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')

    return text.strip()


def extract_title(text: str, max_length: int = 100) -> str:
    """Extract title from text (first N characters)."""
    if not text:
        return ""

    if len(text) <= max_length:
        return text

    title = text[:max_length]
    last_space = title.rfind(" ")

    if last_space > 50:
        title = title[:last_space]

    return title + "..."


def extract_views(html: str) -> int:
    """Extract views count from post HTML."""
    match = re.search(
        r'<span class="tgme_widget_message_views">([^<]+)</span>',
        html,
    )
    if match:
        return parse_count(match.group(1))
    return 0


def extract_reactions(html: str) -> int:
    """Extract total reactions count from post HTML."""
    total = 0

    # Find all reaction spans
    reaction_matches = re.findall(
        r'<span class="tgme_reaction[^"]*">([\s\S]*?)</span>',
        html,
    )

    for reaction_html in reaction_matches:
        count = 0

        # Type 1: Telegram Stars
        if "telegram-stars" in reaction_html:
            star_match = re.search(r"</i>(\d+)", reaction_html)
            if star_match:
                count = int(star_match.group(1))

        # Type 2: Custom emoji
        elif "<tg-emoji" in reaction_html:
            emoji_match = re.search(r"</tg-emoji>([^<]+)", reaction_html)
            if emoji_match:
                count = parse_count(emoji_match.group(1))

        # Type 3: Regular emoji
        else:
            normal_match = re.search(r"</i>([^<]+)", reaction_html)
            if normal_match:
                count = parse_count(normal_match.group(1))

        total += count

    return total


def extract_images(html: str) -> list[str]:
    """Extract image URLs from post HTML."""
    images = []

    # Find background-image URLs
    matches = re.findall(r"background-image:\s*url\(['\"]([^'\"]+)['\"]\)", html)

    for url in matches:
        # Only include telesco.pe images (actual post images)
        if "telesco.pe" in url and "telegram.org" not in url:
            images.append(url)

    return images


def extract_published_at(html: str) -> Optional[datetime]:
    """Extract publication datetime from post HTML."""
    match = re.search(r'datetime="([^"]+)"', html)
    if match:
        try:
            return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def extract_subscribers(html: str) -> Optional[int]:
    """Extract subscribers count from channel info."""
    match = re.search(
        r'<span class="tgme_channel_info_counter">.*?(\d[\d\s.,KM]*)\s*subscribers?',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return parse_count(match.group(1))
    return None


def parse_post_html(html: str) -> Optional[ParsedPost]:
    """Parse single post HTML to ParsedPost dataclass."""
    post_id = extract_post_id(html)
    if not post_id:
        return None

    text = extract_text(html)
    
    # Fallback for media-only posts
    if not text:
        if 'tgme_widget_message_video_player' in html:
            text = "[Видео]"
        elif 'tgme_widget_message_roundvideo_player' in html:
            text = "[Видео-кружок]"
        elif 'tgme_widget_message_voice_player' in html:
            text = "[Голосовое сообщение]"
        elif 'tgme_widget_message_photo_wrap' in html:
            if 'background-image' in html:
                text = "[Фото]"
            else:
                text = "[Изображение]"
        elif 'tgme_widget_message_document' in html:
            text = "[Файл/Документ]"
        elif 'tgme_widget_message_sticker' in html:
            text = "[Стикер]"
        elif 'tgme_widget_message_location' in html:
            text = "[Локация]"
        elif 'tgme_widget_message_poll' in html:
            text = "[Опрос]"

    title = extract_title(text)

    return ParsedPost(
        post_id=post_id,
        text=text,
        title=title,
        views=extract_views(html),
        reactions=extract_reactions(html),
        images=extract_images(html),
        published_at=extract_published_at(html),
    )


def _fetch_channel_html_sync(username: str) -> Optional[str]:
    """Synchronous function to fetch channel HTML."""
    import requests

    url = f"https://t.me/s/{username}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }

        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            return response.text
        else:
            logger.warning(f"Failed to fetch channel {username}: HTTP {response.status_code}")
            return None

    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching channel {username}")
        return None
    except Exception as e:
        logger.error(f"Error fetching channel {username}: {e}")
        return None


def _fetch_page_html_sync(url: str) -> Optional[str]:
    """Synchronous function to fetch a page HTML by full URL."""
    import requests

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }

        response = requests.get(url, headers=headers, timeout=20)

        if response.status_code == 200:
            return response.text
        return None

    except Exception:
        return None


async def fetch_channel_html(username: str) -> Optional[str]:
    """Fetch HTML from public Telegram channel page.

    Args:
        username: Channel username without @

    Returns:
        HTML content or None if failed
    """
    # Clean username
    username = username.lstrip("@").strip()

    # Handle full URLs
    if "/" in username:
        username = username.rstrip("/").split("/")[-1]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_channel_html_sync, username)



def _parse_html_content_sync(html: str) -> list[ParsedPost]:
    """Synchronous function to parse HTML and extract posts (CPU bound)."""
    soup = BeautifulSoup(html, "lxml")
    post_elements = soup.select(".tgme_widget_message")
    
    results = []
    if not post_elements:
        return results

    for element in post_elements:
        post_html = str(element)
        parsed = parse_post_html(post_html)
        if parsed:
            results.append(parsed)
    
    return results

async def parse_channel(username: str) -> Optional[ParsedChannel]:
    """Parse public Telegram channel and return posts.

    Args:
        username: Channel username

    Returns:
        ParsedChannel with posts or None if failed
    """
    html = await fetch_channel_html(username)
    if not html:
        return None

    # dedicated executor for parser to allow clean shutdown
    global _parser_executor
    if _parser_executor is None:
        # Lazy init
        import concurrent.futures
        _parser_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="parser_worker")

    loop = asyncio.get_running_loop()

    # Offload initial parsing to executor
    # We need to extract subscribers first (simple regex)
    # Even this regex can block if html is huge, so we offload it.
    try:
        subscribers = await loop.run_in_executor(_parser_executor, extract_subscribers, html)
        posts = await loop.run_in_executor(_parser_executor, _parse_html_content_sync, html)
    except Exception:
        # If executor is closed or other error
        return None
    
    # Pagination loop    
    # Pagination loop
    max_pages = 4
    current_page = 1
    
    while current_page <= max_pages:
        # If no posts on this page, stop
        if not posts and current_page == 1:
             break
        
        # Find oldest post ID for next page
        # Usually checking the first post in the list (which is the oldest on page)
        # However, for pagination we need the oldest from the *last batch* we parsed.
        # But here we extend 'posts' list. 
        # Actually, Telegram returns posts in chronological order (oldest top, newest bottom) on the page?
        # No, t.me/s/ view is like a chat history. Top is older, Bottom is newer.
        # Wait, repeated 'posts' extension might act weird if we don't track batches.
        # Use the *first* post of the *current page* batch as 'oldest' for 'before=' param?
        # Actually, we need to inspect the HTML structure logic again, but assuming logic was correct before:
        # We need the ID of the top-most post on the page to fetch 'before' it.
        
        # We need to filter 'posts' to find the batch from current page? 
        # Or just trust that we appended them.
        # Let's clean up logic:
        
        batch_posts = posts[-20:] # Approximation, just take last added
        if not batch_posts:
            break

        oldest_post_id = batch_posts[0].post_id # Assuming first in list is oldest
        if not oldest_post_id:
            break

        if current_page >= max_pages:
             break

        # Fetch next page
        next_url = f"https://t.me/s/{username}?before={oldest_post_id}"

        try:
            # Add delay
            await asyncio.sleep(1.0)

            # Fetch page using sync requests in executor
            page_html = await loop.run_in_executor(
                None, _fetch_page_html_sync, next_url
            )

            if not page_html:
                break

            # Offload parsing of new page
            try:
                new_posts = await loop.run_in_executor(_parser_executor, _parse_html_content_sync, page_html)
            except Exception:
                break

            if not new_posts:
                break

            posts.extend(new_posts)
            current_page += 1

        except Exception:
            break 

    logger.info(
        f"Parsed channel @{username}: {len(posts)} posts (depth: {current_page} pages), "
        f"{subscribers or 'unknown'} subscribers"
    )

    return ParsedChannel(
        username=username,
        subscribers_count=subscribers,
        posts=posts,
    )


async def parse_multiple_channels(
    usernames: list[str],
    delay: Optional[float] = None,
) -> list[ParsedChannel]:
    """Parse multiple channels with delay between requests.

    Args:
        usernames: List of channel usernames
        delay: Delay between requests in seconds (default from settings)

    Returns:
        List of parsed channels
    """
    if delay is None:
        delay = settings.parser_delay

    results = []

    for i, username in enumerate(usernames):
        logger.info(f"Processing donor {i+1}/{len(usernames)}: @{username}")
        result = await parse_channel(username)
        if result:
            results.append(result)

        # Add delay between requests (except after last)
        if i < len(usernames) - 1:
            await asyncio.sleep(delay)

    return results

def shutdown_parser_executor() -> None:
    """Shutdown the parser executor."""
    global _parser_executor
    if _parser_executor:
        logger.info("Shutting down parser executor...")
        _parser_executor.shutdown(wait=False, cancel_futures=True)
        _parser_executor = None
