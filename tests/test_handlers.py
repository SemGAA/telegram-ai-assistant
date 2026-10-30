import asyncio
import json

import httpx
from sqlalchemy import select

from bot import db


def answer_response(content="Hello"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_full_conversation_and_clear(telegram, sessions):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return answer_response("Remembered")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        send, messages = telegram(client)
        await send("/start")
        await send("My name is Semyon")
        await send("What is my name?")
        assert requests[-1]["messages"][1:3] == [
            {"role": "user", "content": "My name is Semyon"},
            {"role": "assistant", "content": "Remembered"},
        ]
        assert len(await db.recent_turns(sessions, 101, 10)) == 2
        await send("/clear")
        await send("New topic")
        assert len(requests[-1]["messages"]) == 2
        assert messages[-1].text == "Remembered"


async def test_unauthorized_and_group_messages_never_call_ai(telegram, sessions):
    def unexpected(request):
        raise AssertionError("AI must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        send, messages = telegram(client)
        await send("Hello", user_id=999)
        assert "999" in messages[-1].text
        assert "Доступ закрыт" in messages[-1].text
        await send("Hello", chat_type="group")
        assert len(messages) == 1
    async with sessions() as session:
        assert (await session.scalars(select(db.User))).all() == []


async def test_errors_are_recorded_and_do_not_pollute_context(telegram, sessions):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429))
    ) as client:
        send, messages = telegram(client)
        await send("Question")
    assert "лимит" in messages[-1].text
    assert await db.recent_turns(sessions, 101, 10) == []
    async with sessions() as session:
        turn = await session.scalar(select(db.Turn))
        assert turn.question == "Question"
        assert turn.answer is None
        assert turn.error == "rate_limit"


async def test_same_user_cannot_race_clear_but_other_user_can_proceed(telegram, sessions):
    started, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if json.loads(request.content)["messages"][-1]["content"] == "First":
            started.set()
            await release.wait()
        return answer_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        send, messages = telegram(client)
        task = asyncio.create_task(send("First"))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            await send("/clear")
            assert "ещё выполняется" in messages[-1].text
            await send("Other user", user_id=202)
            assert messages[-1].text == "Hello"
        finally:
            release.set()
            await task
    assert len(await db.recent_turns(sessions, 101, 10)) == 1
    assert len(await db.recent_turns(sessions, 202, 10)) == 1


async def test_invalid_input_and_commands_do_not_call_ai(telegram):
    def unexpected(request):
        raise AssertionError("AI must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        send, messages = telegram(client)
        await send("x" * 4001)
        assert "4000" in messages[-1].text
        await send("/unknown")
        assert "Неизвестная команда" in messages[-1].text
        await send("   ")
        assert "Напишите" in messages[-1].text
        await send(None)
        assert "текстовые" in messages[-1].text
        await send("/id")
        assert "101" in messages[-1].text


async def test_cooldown_is_enforced(telegram, settings):
    settings.request_interval_seconds = 60
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return answer_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        send, messages = telegram(client)
        await send("One")
        await send("Two")
    assert count == 1
    assert "частые запросы" in messages[-1].text


async def test_long_response_is_split_and_saved_intact(telegram, sessions):
    answer = "Пример <code> & текст\n" * 400
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: answer_response(answer))
    ) as client:
        send, messages = telegram(client)
        await send("Question")
    assert "".join(message.text for message in messages) == answer.strip()
    assert (await db.recent_turns(sessions, 101, 1))[0].answer == answer.strip()


async def test_public_access_registers_new_users_and_keeps_history_private(
    telegram, sessions, settings
):
    settings.allow_all_users = True
    settings.allowed_user_ids = ""
    requests = []

    def respond(request):
        requests.append(json.loads(request.content)["messages"])
        return answer_response("Saved")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        send, messages = telegram(client)
        await send("My private note", user_id=7_000_000_001)
        await send("My other note", user_id=7_000_000_002)
        assert len(requests[-1]) == 2
        assert "My private note" not in str(requests[-1])
        await send("What did I say?", user_id=7_000_000_001)
        assert requests[-1][1]["content"] == "My private note"
        await send("/clear", user_id=7_000_000_001)
        assert await db.recent_turns(sessions, 7_000_000_001, 10) == []
        assert len(await db.recent_turns(sessions, 7_000_000_002, 10)) == 1
        await send("Group text", user_id=7_000_000_002, chat_type="group")
        assert len(requests) == 3
        assert "История удалена" in messages[-1].text


async def test_new_dispatcher_restores_history_from_database(telegram):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content)["messages"])
        return answer_response("Remembered")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        send, _ = telegram(client)
        await send("Remember this between restarts")
        send_after_restart, _ = telegram(client)
        await send_after_restart("What did I say?")
    assert requests[-1][1]["content"] == "Remember this between restarts"
