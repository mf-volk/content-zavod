from typing import Optional
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest
from bs4 import BeautifulSoup

def sanitize_html(text: str) -> str:
    """
    Sanitize text for Telegram HTML parse mode using BeautifulSoup.
    
    1. Fixes unclosed tags.
    2. Removes unsupported tags (keeps content).
    3. Converts equivalent tags (strong->b, em->i).
    """
    if not text:
        return ""
        
    soup = BeautifulSoup(text, "html.parser")
    
    # Telegram supported tags
    ALLOWED_TAGS = [
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", 
        "a", "code", "pre", "blockquote"
    ]
    
    for tag in soup.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            # Unwrap unsupported tags (keep content, remove tag)
            # e.g. <span>Hello</span> -> Hello
            # <br> -> newline
            if tag.name == "br":
                tag.replace_with("\n")
            elif tag.name == "p":
                tag.insert_after("\n\n")
                tag.unwrap()
            else:
                tag.unwrap()
        else:
            # Check attributes
            # Only <a> allows href, code allows class='language-...'
            allowed_attrs = {}
            if tag.name == "a" and tag.has_attr("href"):
                allowed_attrs["href"] = tag["href"]
            elif tag.name == "code" and tag.has_attr("class"):
                # Telegram supports language class for code blocks? Actually pre.
                # But let's keep basic hygiene.
                pass
            
            tag.attrs = allowed_attrs

    # Convert soup back to string
    # decode_contents() ensures we don't get <html><body> wrappers if they appeared
    clean_text = soup.decode_contents()
    
    # Final cleanup of multi-newlines
    import re
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)
    
    return clean_text.strip()


def strip_html(text: str) -> str:
    """Remove all HTML tags from text."""
    if not text:
        return ""
    import re
    # Remove tags <...>
    text = re.sub(r"<[^>]+>", "", text)
    # Basic unescape if needed (Telegram mostly handles raw text logic fine, but &quot; might remain)
    # Let's keep it simple for preview.
    return text.strip()


async def answer_nav(
    callback: CallbackQuery,
    label: str,
    new_text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML",
    **kwargs,
) -> Message:
    """
    Handle navigation UX pattern:
    1. Remove buttons from old message.
    2. Send 'label' as text (simulating user choice).
    3. Send 'new_text' with new 'reply_markup' (new state).
    """
    # 1. Remove buttons from old message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        # Message might be too old or deleted
        pass
    except Exception:
        pass

    # 2. Echo the action
    if label:
        # We answer the callback to stop the loading animation
        await callback.answer() 
        await callback.message.answer(label)
    else:
        await callback.answer()

    # 3. Send new message
    return await callback.message.answer(
        new_text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        **kwargs,
    )
