import pytest
from pydantic import ValidationError

from bot.config import Settings
from bot.handlers import split_text


def test_access_is_public_by_default(settings):
    values = settings.model_dump(exclude={"allow_all_users", "allowed_user_ids"})
    public = Settings(_env_file=None, **values)
    assert public.allow_all_users
    assert not public.allowed_ids


def test_allowlist(settings):
    assert settings.allowed_ids == {101, 202}


@pytest.mark.parametrize("value", ["hello", "101,", "-42", "0", "1,2.5"])
def test_bad_allowlist_is_rejected(settings, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**settings.model_dump(), "allowed_user_ids": value})


def test_password_special_characters(settings):
    settings.postgres_password = type(settings.postgres_password)("a@b:/?#%")
    assert settings.database_url.password == "a@b:/?#%"
    assert "a@b" not in str(settings.database_url)


@pytest.mark.parametrize(
    "value", ["ftp://example.com", "https://key@example.com", "https://a/?key=x"]
)
def test_bad_base_url(settings, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**settings.model_dump(), "ai_base_url": value})


@pytest.mark.parametrize("value", ["x" * 9000, "😀" * 5000, "Текст <>&\n" * 1000])
def test_long_answers_preserve_content_and_fit_telegram(value):
    parts = list(split_text(value))
    assert "".join(parts) == value
    assert all(0 < len(part.encode("utf-16-le")) // 2 <= 4000 for part in parts)
