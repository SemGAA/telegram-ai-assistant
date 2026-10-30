import asyncio
import json

import httpx
import pytest

from bot.ai import AIError, build_messages, generate_answer
from bot.db import Turn


async def test_request_contains_model_history_and_auth(settings):
    def respond(request):
        assert request.url.path == "/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-api-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["max_completion_tokens"] == 1500
        assert payload["messages"][-1] == {"role": "user", "content": "Hello"}
        return httpx.Response(200, json={"choices": [{"message": {"content": " Answer "}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await generate_answer(client, settings, build_messages(settings, [], "Hello"))
    assert result == "Answer"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "access_denied"),
        (402, "access_denied"),
        (403, "access_denied"),
        (429, "rate_limit"),
        (500, "http_error"),
    ],
)
async def test_http_errors(settings, status, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        with pytest.raises(AIError) as error:
            await generate_answer(client, settings, [])
    assert error.value.code == code


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": " "}}]},
    ],
)
async def test_invalid_payload(settings, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(AIError, match="invalid_response"):
            await generate_answer(client, settings, [])


async def test_response_body_limit(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 256001))
    ) as client:
        with pytest.raises(AIError, match="invalid_response"):
            await generate_answer(client, settings, [])


@pytest.mark.parametrize(
    "exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error")]
)
async def test_network_failures(settings, exception, code):
    def respond(request):
        raise exception("sensitive upstream details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AIError) as error:
            await generate_answer(client, settings, [])
    assert error.value.code == code
    assert "sensitive" not in str(error.value)


async def test_total_deadline(settings):
    settings.ai_timeout_seconds = 0.01

    async def respond(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AIError, match="timeout"):
            await generate_answer(client, settings, [])


def test_context_keeps_recent_complete_turns(settings):
    settings.system_prompt = "System"
    settings.context_max_chars = 18
    turns = [Turn(question="old", answer="old"), Turn(question="new", answer="reply")]
    messages = build_messages(settings, turns, "Next")
    assert [message["content"] for message in messages] == ["System", "new", "reply", "Next"]


def test_failed_turn_is_not_sent_to_model(settings):
    messages = build_messages(settings, [Turn(question="failed", answer=None)], "Next")
    assert len(messages) == 2
