import asyncio
import json

import httpx

from bot.config import Settings
from bot.db import Turn


class AIError(Exception):
    def __init__(self, code: str, user_message: str):
        super().__init__(code)
        self.code = code
        self.user_message = user_message


def build_messages(settings: Settings, turns: list[Turn], question: str) -> list[dict[str, str]]:
    remaining = settings.context_max_chars - len(settings.system_prompt) - len(question)
    history: list[dict[str, str]] = []
    for turn in reversed(turns):
        if turn.answer is None:
            continue
        size = len(turn.question) + len(turn.answer)
        if size > remaining:
            break
        history[0:0] = [
            {"role": "user", "content": turn.question},
            {"role": "assistant", "content": turn.answer},
        ]
        remaining -= size
    return [
        {"role": "system", "content": settings.system_prompt},
        *history,
        {"role": "user", "content": question},
    ]


async def generate_answer(
    client: httpx.AsyncClient, settings: Settings, messages: list[dict[str, str]]
) -> str:
    try:
        # A total deadline also covers servers that keep sending partial data.
        async with asyncio.timeout(settings.ai_timeout_seconds):
            async with client.stream(
                "POST",
                f"{settings.ai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ai_api_key.get_secret_value()}"},
                json={
                    "model": settings.ai_model,
                    "messages": messages,
                    "max_completion_tokens": settings.ai_max_tokens,
                    "stream": False,
                },
            ) as response:
                if response.status_code == 429:
                    raise AIError(
                        "rate_limit", "У сервиса AI сейчас лимит запросов. Попробуйте позже."
                    )
                if response.status_code in {401, 402, 403}:
                    raise AIError(
                        "access_denied",
                        "Сервис AI недоступен. Администратору нужно проверить доступ.",
                    )
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 256_000:
                        raise AIError("invalid_response", "Сервис AI вернул слишком большой ответ.")
                data = json.loads(body)
                answer = data["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Empty answer")
                return answer.strip()
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise AIError("timeout", "Сервис AI не успел ответить. Попробуйте ещё раз.") from exc
    except httpx.HTTPStatusError as exc:
        raise AIError("http_error", "Сервис AI временно недоступен. Попробуйте позже.") from exc
    except httpx.RequestError as exc:
        raise AIError(
            "network_error", "Не удалось связаться с сервисом AI. Попробуйте позже."
        ) from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIError(
            "invalid_response", "Сервис AI вернул некорректный ответ. Попробуйте ещё раз."
        ) from exc
