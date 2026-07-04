"""LLM client for content generation using OpenAI API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings

import base64
import os

logger = logging.getLogger(__name__)


@dataclass
class IdeaDTO:
    """Data transfer object for generated idea."""

    title: str
    description: str
    why_relevant: str  # New: Why this topic is relevant
    source_post_id: Optional[int] = None  # New: ID of the donor post used
    source: str = "llm"


@dataclass
class DraftTextDTO:
    """Data transfer object for generated draft text."""

    title: str
    content: str


class LLMClient:
    """Client for LLM operations (idea generation, draft writing, rewriting)."""

    def __init__(self) -> None:
        """Initialize OpenAI client."""
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url
        )
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self.temperature = settings.llm_temperature

    async def _complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> str:
        """Make completion request to LLM."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=self.max_tokens,
                temperature=temperature or self.temperature,
            )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"LLM completion error: {e}")
            raise

    def _build_tone_prompt(
        self,
        tone_of_voice: Optional[str],
        language: str = "ru",
    ) -> str:
        """Build tone of voice instruction for system prompt."""
        base = f"Пиши на {'русском' if language == 'ru' else 'английском'} языке."

        if tone_of_voice:
            return f"{base}\n\nТон и стиль:\n{tone_of_voice}"

        return base

        return "\n".join(summaries)

    def _summarize_donor_posts(
        self,
        donor_posts: list[dict],
        max_posts: int = 40, 
    ) -> str:
        """Summarize donor posts for context."""
        if not donor_posts:
            return "Нет данных от донорских каналов."

        # Group by source for structured context
        posts_by_source = {}
        for post in donor_posts[:max_posts]:
            source = post.get("source_name", "Unknown Source")
            if source not in posts_by_source:
                posts_by_source[source] = []
            posts_by_source[source].append(post)

        summaries = []
        
        for source, posts in posts_by_source.items():
            summaries.append(f"\n--- ИСТОЧНИК: @{source} ---")
            for post in posts:
                p_id = post.get("id")
                title = post.get("title", "")
                if title == "Без заголовка":
                    title = "" 
                
                text = post.get("text", "")
                text = text.replace("\n", " ").strip()[:400]
                
                if not title and not text:
                    text = "[Пост-картинка или видео без описания]"

                views = post.get("views", 0)
                reactions = post.get("reactions", 0)
                
                # Format: [ID 123] Title? - Text... (Metrics)
                content_part = f"{title}: {text}" if title else text
                date_str = ""
                post_date = post.get("date")
                if post_date and hasattr(post_date, "strftime"):
                    date_str = f", 📅 {post_date.strftime('%d.%m.%Y')}"
                summaries.append(f"[ID {p_id}] {content_part} (👁 {views}, ❤️ {reactions}{date_str})")

        return "\n".join(summaries)

    async def infer_tone_of_voice_from_posts(
        self,
        channel_title: str,
        posts: list[dict],
        language: str = "ru",
    ) -> Optional[str]:
        """Infer channel tone of voice from recent posts."""

        if not posts:
            return None

        formatted_posts = []
        for post in posts[:15]:
            text = (post.get("text") or post.get("title") or "").strip()
            if not text:
                continue

            snippet = text.replace("\n", " ")[:400]
            views = post.get("views")
            reactions = post.get("reactions")
            metrics = []
            if reactions:
                metrics.append(f"❤️ {reactions}")
            if views:
                metrics.append(f"👁 {views}")

            metrics_str = f" ({', '.join(metrics)})" if metrics else ""
            formatted_posts.append(f"• {snippet}{metrics_str}")

        if not formatted_posts:
            return None

        system_prompt = (
            "Ты — редактор бренда. По примерам постов опиши тон и стиль канала кратко,"
            " в 5–7 маркерах: лексика, эмоциональность, длина фраз, формат (факты/"
            "истории/CTA), обращение к читателю, эмодзи/пунктуация, обязательные"
            " элементы. Ответь сжато, без лишних пояснений."
        )

        user_prompt = (
            f"Канал: {channel_title}. Язык: {'русский' if language == 'ru' else 'английский'}.\n"
            "Примеры постов:\n"
            + "\n".join(formatted_posts)
            + "\n\nДай чеклист тона (маркированные пункты)."
        )

        try:
            response = await self._complete(
                system_prompt,
                user_prompt,
                temperature=0.3,
            )
            return response.strip()
        except Exception as e:
            logger.error(f"Failed to infer tone of voice: {e}")
            return None

    async def generate_ideas(
        self,
        tone_of_voice: Optional[str],
        donor_posts: list[dict],
        count: int = 3,
        language: str = "ru",
        topic: Optional[str] = None,
    ) -> list[IdeaDTO]:
        """Generate curated content ideas based on donor posts."""
        
        topic_instruction = ""
        if topic:
            topic_instruction = f"\n⚠️ ФОКУС: Выбирай только посты, связанные с темой: '{topic}'."

        # Analyze available sources for strict diversity rules
        unique_sources = set()
        for p in donor_posts:
            if p.get("source_name"):
                unique_sources.add(p["source_name"])
        
        diversity_instruction = ""
        if len(unique_sources) >= 3:
            diversity_instruction = (
                "\n4. СТРОГОЕ ПРАВИЛО: Доступно 3+ источника. Ты ОБЯЗАН выбрать 3 идеи из ТРЕХ РАЗНЫХ источников. "
                "Запрещено брать две идеи от одного и того же канала."
            )
        elif len(unique_sources) == 2:
            diversity_instruction = (
                "\n4. СТРОГОЕ ПРАВИЛО: Доступно 2 источника. Ты ОБЯЗАН взять хотя бы по одной идее от каждого источника."
            )
        else:
            diversity_instruction = "\n4. ИСТОЧНИКИ: Работаем с тем, что есть."

        system_prompt = f"""Ты — опытный куратор контента (Content Curator) для Telegram-каналов.
Твоя задача — проанализировать посты конкурентов, отобрать {count} самых виральных (популярных) и подготовить их краткий пересказ.

{self._build_tone_prompt(tone_of_voice, language)}{topic_instruction}

ТВОЯ ЦЕЛЬ:
Не придумывать новые темы "из головы", а найти "золотые" посты среди предложенных и упаковать их для принятия решения.

КРИТЕРИИ ОТБОРА:
1. Используй только предоставленный контекст.
2. Выбирай посты с наибольшей значимостью и интересом. ОТДАВАЙ ПРЕДПОЧТЕНИЕ СВЕЖИМ постам (по дате).
3. РАЗНООБРАЗИЕ: Старайся выбрать посты от РАЗНЫХ источников (Source).{diversity_instruction}

ФОРМАТ ОТВЕТА (JSON массив):
[
  {{
    "source_post_id": 123, // ВАЖНО: ID поста из переданного списка!
    "title": "Цепляющий заголовок новости (Clickbait в меру)",
    "description": "Краткий пересказ сути поста (Recap). О чем там речь? (2-3 предложения)",
    "why_relevant": "Аналитика: почему этот пост взлетел? (например: 'Острый инфоповод', 'Полезная инструкция')"
  }}
]
"""

        donor_context = self._summarize_donor_posts(donor_posts)
        
        logger.info(f"Generating ideas with {len(donor_posts)} donor posts. Context preview: {donor_context[:200]}")

        user_prompt = f"""Вот список популярных постов конкурентов.
Выбери топ-{count} самых виральных постов, которые стоит взять к себе в канал.

Список постов:
{donor_context}

Верни JSON массив с {count} элементами.
Убедись, что 'source_post_id' соответствует ID поста из списка.
"""

        try:
            response = await self._complete(system_prompt, user_prompt)

            # Parse JSON
            import json
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("\n", 1)[0]
            if response.startswith("json"):
                response = response[4:].strip()

            ideas_data = json.loads(response)

            results = []
            for idea in ideas_data:
                # Sanitize content
                title = idea.get("title", "").replace("<br>", "").replace("<br/>", "").strip()
                desc = idea.get("description", "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n").strip()
                why = idea.get("why_relevant", "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n").strip()
                
                results.append(IdeaDTO(
                    title=title,
                    description=desc,
                    why_relevant=why,
                    source_post_id=idea.get("source_post_id"),
                    source="donor_curation"
                ))
            
            return results

        except Exception as e:
            logger.error(f"Failed to generate ideas: {e}")
            return []

    async def generate_draft(
        self,
        idea_title: str,
        idea_description: str,
        tone_of_voice: Optional[str],
        extra_instructions: Optional[str] = None,
        language: str = "ru",
    ) -> Optional[DraftTextDTO]:
        """Generate full post draft from idea.

        Args:
            idea_title: Idea title
            idea_description: Idea description
            tone_of_voice: Channel's tone of voice
            extra_instructions: Additional user instructions
            language: Output language

        Returns:
            Generated draft or None if failed
        """
        system_prompt = f"""Ты — профессиональный копирайтер для Telegram-каналов.
Твоя задача — написать полноценный пост по заданной идее.

{self._build_tone_prompt(tone_of_voice, language)}

ТРЕБОВАНИЯ К ПОСТУ:
- Пост должен быть интересным и вовлекающим
- Используй короткие абзацы (1-3 предложения)
- Можно использовать эмодзи для акцентов
- Длина: до 950 символов (строго!)
- Пост должен быть готов к публикации без изменений
- Формат: HTML (только поддерживаемые Telegram теги: <b>, <i>, <a href="...">, <u>, <s>, <code>)
- СТРОГО: Всегда закрывай теги! <b>Текст</b>, а не <b>Текст
- Запрещено: markdown (**, ##), code brackets (```), теги <br> (используй переносы строк)

Формат ответа: JSON с полями "title" (заголовок для превью, до 100 символов) и "content" (полный текст поста)
"""

        instructions = f"Дополнительные указания: {extra_instructions}" if extra_instructions else ""

        user_prompt = f"""Напиши пост на тему:

Заголовок идеи: {idea_title}
Описание: {idea_description}
{instructions}

Верни ответ строго в формате JSON:
{{"title": "Краткий заголовок", "content": "Полный текст поста с HTML разметкой"}}
"""

        try:
            response = await self._complete(system_prompt, user_prompt, temperature=0.8)

            # Parse JSON response
            import json

            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("\n", 1)[0]
            if response.startswith("json"):
                response = response[4:].strip()

            data = json.loads(response)

            return DraftTextDTO(
                title=data.get("title", ""),
                content=data.get("content", ""),
            )

        except Exception as e:
            logger.error(f"Failed to generate draft: {e}")
            return None

    async def rewrite_text(
        self,
        original_text: str,
        user_instructions: str,
        tone_of_voice: Optional[str],
        language: str = "ru",
    ) -> Optional[DraftTextDTO]:
        """Rewrite/edit text based on user instructions.

        Args:
            original_text: Original post text
            user_instructions: What to change
            tone_of_voice: Channel's tone of voice
            language: Output language

        Returns:
            Rewritten draft or None if failed
        """
        system_prompt = f"""Ты — редактор текстов для Telegram-каналов.
Твоя задача — отредактировать пост по указаниям пользователя.

{self._build_tone_prompt(tone_of_voice, language)}

ВАЖНО:
- Сохраняй общий смысл и структуру, если не указано иное
- Длина: до 950 символов (строго!)
- Применяй HTML форматирование только поддерживаемыми тегами (<b>, <i>, <a>, <u>, <s>, <code>)
- СТРОГО: Следи за закрытием тегов. Если открыл <b>, обязан закрыть </b>.
- СТРОГО: Не используй Markdown (**жирный**, *курсив*), используй только HTML.
- Формат ответа: JSON с полями "title" и "content"
"""

        user_prompt = f"""Отредактируй этот пост:

{original_text}

Указания по редактированию: {user_instructions}

Верни ответ строго в формате JSON:
{{"title": "Краткий заголовок", "content": "Отредактированный текст поста"}}
"""

        try:
            response = await self._complete(system_prompt, user_prompt, temperature=0.7)

            import json

            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("\n", 1)[0]
            if response.startswith("json"):
                response = response[4:].strip()

            data = json.loads(response)

            return DraftTextDTO(
                title=data.get("title", ""),
                content=data.get("content", ""),
            )

        except Exception as e:
            logger.error(f"Failed to rewrite text: {e}")
            return None

    async def generate_image_prompt(
        self,
        text_content: str,
        idea_context: Optional[str] = None,
        language: str = "ru",
    ) -> Optional[str]:
        """Generate a DALL-E 3 image prompt based on text/idea.

        Args:
            text_content: The draft text
            idea_context: Optional idea description
            language: Language for the output prompt (usually English for DALL-E, or Russian if preferred)
                      DALL-E 3 understands Russian well, but English is often more precise.
                      We will ask to generate in English or Russian based on request.

        Returns:
            Generated prompt string or None
        """
        # The user specifically requested prompts in Russian and better quality.
        # We will instruct the LLM to write the prompt in the requested language (Russian).
        
        lang_instruction = "Russian" if language == "ru" else "English"

        system_prompt = f"""You are an expert prompt engineer for AI image generation (Stable Diffusion/Flux).
Your task is to create a CONCISE, EFFECTIVE, and OPTIMIZED prompt.

GUIDELINES:
1. OUTPUT LANGUAGE: {lang_instruction} ONLY.
2. LENGTH: SHORT. Max 40-50 words. No long sentences.
3. STRUCTURE: [Subject], [Action/Context], [Art Style], [Lighting].
4. STYLE: Comma-separated tags are preferred over long descriptions.
5. NO NEGATIVES: Do not describe what NOT to include.
6. AESTHETICS: Use 2-3 high-impact keywords.
        
Example:
"Киберпанк продавец уличной еды, неоновый дождь, отражения в лужах, кинематографичное освещение, высокий контраст, 8k, фотореализм"

Goal: Create a lightweight prompt that generates a high-quality image."""

        user_content = f"Post text:\n{text_content}"
        if idea_context:
            user_content += f"\n\nContext/Idea:\n{idea_context}"

        user_prompt = f"""Create an image generation prompt for this post.
The prompt MUST be in {lang_instruction}.

Input Data:
{user_content}

Just return the prompt text, nothing else.
"""

        try:
            prompt = await self._complete(system_prompt, user_prompt, temperature=0.7)
            return prompt.strip()

        except Exception as e:
            logger.error(f"Failed to generate image prompt: {e}")
            return None


    async def transcribe_audio(self, file_path: str) -> str:
        """Transcribe audio file using Whisper."""
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(
                    model=settings.openai_transcribe_model,
                    file=audio_file
                )
            return transcript.text
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return "[Не удалось распознать аудио]"

    async def describe_image(self, file_path: str) -> str:
        """Describe image using GPT-4 Vision."""
        try:
            with open(file_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
            response = await self.client.chat.completions.create(
                model=settings.openai_vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image in detail for content creation context."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500
            )
            return response.choices[0].message.content or "[no description]"
        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")
            return "[Не удалось проанализировать изображение]"

    async def generate_ideas_from_inputs(
        self,
        inputs: list[dict],
        channel_title: str,
        count: int = 3,
        language: str = "ru",
        source_type: str = "user_input",
    ) -> list[IdeaDTO]:
        """Generate ideas from mixed user inputs (voice, text, photo, youtube)."""

        # 1. Process all inputs
        processed_texts = []

        for inp in inputs:
            inp_type = inp.get("type")

            if inp_type == "text":
                processed_texts.append(f"[Текст]: {inp.get('content')}")

            elif inp_type == "voice":
                path = inp.get("path")
                if path and os.path.exists(path):
                    text = await self.transcribe_audio(path)
                    processed_texts.append(f"[Голосовое]: {text}")

            elif inp_type == "image":
                path = inp.get("path")
                if path and os.path.exists(path):
                    desc = await self.describe_image(path)
                    processed_texts.append(f"[Описание фото]: {desc}")

        full_context = "\n\n".join(processed_texts)

        if not full_context.strip():
            return []

        # 2. Generate Ideas - prompt depends on source type
        if source_type == "youtube":
            system_prompt = f"""Ты — креативный помощник для автора Telegram-канала "{channel_title}".
Твоя задача — проанализировать транскрипции YouTube-видео и предложить {count} уникальных идей для постов, вдохновлённых содержанием видео.

ВАЖНЫЕ ПРАВИЛА:
- Материал может быть на ЛЮБОМ языке — генерируй идеи ТОЛЬКО на русском языке.
- НЕ копируй содержимое видео дословно. Переосмысли, адаптируй под формат Telegram-канала.
- Если видео несколько — можешь комбинировать идеи из разных видео или брать лучшее из каждого.
- Каждая идея должна быть самостоятельной и ценной для аудитории канала.

{self._build_tone_prompt(None, language)}

ФОРМАТ ОТВЕТА (JSON массив):
[
  {{
    "title": "Цепляющий заголовок для идеи",
    "description": "О чем писать? Раскрой суть идеи, адаптированной из видео. (2-3 предложения)",
    "why_relevant": "Почему это актуально для аудитории канала?"
  }}
]
"""
            user_prompt = f"""Вот транскрипции YouTube-видео:

{full_context}

Предложи {count} идей постов, вдохновлённых этими видео.
Верни JSON массив.
"""
        else:
            system_prompt = f"""Ты — креативный помощник для автора Telegram-канала "{channel_title}".
Твоя задача — превратить "сырые" мысли, заметки, голосовые и фото автора в {count} четких тем для постов.

{self._build_tone_prompt(None, language)}

ФОРМАТ ОТВЕТА (JSON массив):
[
  {{
    "title": "Цепляющий заголовок для идеи",
    "description": "О чем писать? Раскрой суть идеи на основе материалов автора. (2-3 предложения)",
    "why_relevant": "Почему это круто? (например: 'Личная история', 'Инсайт', 'Ответ на вопрос')"
  }}
]
"""
            user_prompt = f"""Вот мои материалы (заметки, мысли, фото):

{full_context}

Предложи {count} идеи постов на основе этого.
Верни JSON массив.
"""
        
        try:
            response = await self._complete(system_prompt, user_prompt)
            
            # Parse JSON
            import json
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("\n", 1)[0]
            if response.startswith("json"):
                response = response[4:].strip()

            ideas_data = json.loads(response)

            results = []
            for idea in ideas_data:
                results.append(IdeaDTO(
                    title=idea.get("title", ""),
                    description=idea.get("description", ""),
                    why_relevant=idea.get("why_relevant", ""),
                    source="user_input"
                ))
            
            return results

        except Exception as e:
            logger.error(f"Failed to generate ideas from input: {e}")
            return []

    async def generate_ideas_from_space(
        self,
        materials_text: str,
        space_title: str,
        channel_title: str,
        tone_of_voice: Optional[str] = None,
        count: int = 5,
        language: str = "ru",
        materials_count: int = 1,
    ) -> dict:
        """Generate ideas from space materials.

        Args:
            materials_text: Combined processed text from all materials
            space_title: Space/topic title
            channel_title: Channel title
            tone_of_voice: Channel's tone of voice
            count: Number of ideas to generate
            language: Output language
            materials_count: Number of materials being processed

        Returns:
            Dict with 'summary' and 'ideas' list
        """
        # Different prompts for single vs multiple materials
        if materials_count > 1:
            cross_reference_instruction = f"""
КРИТИЧЕСКИ ВАЖНО — У тебя {materials_count} РАЗНЫХ материалов:
- Каждая идея ОБЯЗАТЕЛЬНО должна ОБЪЕДИНЯТЬ информацию минимум из 2-х материалов
- Ищи связи, пересечения и общие темы между РАЗНЫМИ источниками
- НЕ генерируй идеи только по одному материалу — ВСЕГДА синтезируй несколько
- В описании каждой идеи укажи, какие материалы она объединяет
- Показывай, как разные источники дополняют и усиливают друг друга
"""
        else:
            cross_reference_instruction = """
Проанализируй материал и предложи разные углы подачи информации.
"""

        system_prompt = f"""Ты — контент-аналитик для Telegram-канала "{channel_title}".
Твоя задача — проанализировать предоставленные материалы и сгенерировать идеи для постов.

{self._build_tone_prompt(tone_of_voice, language)}
{cross_reference_instruction}

ЗАДАЧИ:
1. Сделай общее резюме материалов (3-5 предложений){" — покажи связь между ними" if materials_count > 1 else ""}
2. Предложи {count} идей для постов{" где каждая идея СИНТЕЗИРУЕТ информацию из разных материалов" if materials_count > 1 else ""}

ФОРМАТ ОТВЕТА (JSON):
{{
  "summary": "Общее резюме{", показывающее связь между материалами" if materials_count > 1 else ""}",
  "ideas": [
    {{
      "title": "Цепляющий заголовок",
      "description": "О чем пост?{" Какие материалы объединяет?" if materials_count > 1 else ""} 2-3 предложения.",
      "key_points": ["Ключевая мысль 1", "Ключевая мысль 2"]
    }}
  ]
}}
"""

        # Limit materials text
        materials_text = materials_text[:15000]

        user_prompt = f"""Тема пространства: "{space_title}"
Количество материалов: {materials_count}

{materials_text}

Сгенерируй {count} идей для постов.{f" Каждая идея должна объединять информацию из разных материалов!" if materials_count > 1 else ""}
Верни JSON.
"""

        try:
            response = await self._complete(system_prompt, user_prompt, temperature=0.7)

            if not response:
                logger.error("Empty response from LLM")
                return {"summary": "Ошибка: пустой ответ от LLM", "ideas": []}

            import json
            import re

            response = response.strip()

            # Remove markdown code blocks
            if response.startswith("```"):
                lines = response.split("\n")
                # Remove first line (```json or ```)
                lines = lines[1:]
                # Remove last line if it's ```
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                response = "\n".join(lines)

            # Remove leading "json" if present
            if response.lower().startswith("json"):
                response = response[4:].strip()

            # Try to find JSON object in response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group()

            logger.debug(f"Parsing JSON response: {response[:200]}...")

            data = json.loads(response)
            return {
                "summary": data.get("summary", ""),
                "ideas": data.get("ideas", []),
            }

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM: {e}. Response: {response[:500] if response else 'empty'}")
            return {"summary": "Ошибка парсинга ответа LLM", "ideas": []}
        except Exception as e:
            logger.error(f"Failed to generate ideas from space: {e}")
            return {"summary": f"Ошибка: {str(e)}", "ideas": []}

    async def generate_content_plan(
        self,
        channel_title: str,
        tone_of_voice: Optional[str],
        topic_preferences: Optional[str] = None,
        posts_per_day: int = 1,
        language: str = "ru",
    ) -> list[dict]:
        """Generate a weekly content plan.

        Args:
            channel_title: Channel title
            tone_of_voice: Channel's tone of voice
            topic_preferences: User's topic preferences
            posts_per_day: Number of posts per day
            language: Output language

        Returns:
            List of content plan slots
        """
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

        topic_instruction = ""
        if topic_preferences:
            topic_instruction = f"\nПРЕДПОЧТЕНИЯ ПО ТЕМАМ: {topic_preferences}"

        system_prompt = f"""Ты — контент-стратег для Telegram-канала "{channel_title}".
Твоя задача — создать контент-план на неделю.

{self._build_tone_prompt(tone_of_voice, language)}{topic_instruction}

ТРЕБОВАНИЯ:
- Темы должны быть разнообразными
- Учитывай лучшее время для публикации (утро: 9-11, обед: 13-14, вечер: 19-21)
- Чередуй форматы: советы, истории, новости, обзоры, вопросы
- {posts_per_day} пост(ов) в день

ФОРМАТ ОТВЕТА (JSON массив):
[
  {{
    "day_of_week": 0,  // 0=Пн, 1=Вт, ..., 6=Вс
    "time": "10:00",
    "topic": "Тема поста",
    "description": "Краткое описание содержания",
    "format": "совет/история/новость/обзор/вопрос"
  }}
]
"""

        user_prompt = f"""Создай контент-план на 7 дней ({posts_per_day} пост(ов)/день).
Дни: {', '.join(days)}

Верни JSON массив с {7 * posts_per_day} элементами.
"""

        try:
            response = await self._complete(system_prompt, user_prompt, temperature=0.8)

            import json
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("\n", 1)[0]
            if response.startswith("json"):
                response = response[4:].strip()

            return json.loads(response)

        except Exception as e:
            logger.error(f"Failed to generate content plan: {e}")
            return []


# Global client instance
llm_client = LLMClient()
