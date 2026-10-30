# Workers AI

Необязательный адаптер для уже подключённого Workers AI. Основное приложение
работает в Docker с PostgreSQL; Worker только вызывает модель и возвращает ответ.
История и Telegram ID в Worker не сохраняются.

В панели Cloudflare создайте Worker, добавьте привязку Workers AI с именем `AI`
и secret `TELEGRAM_TOKEN`. Разверните содержимое `ai-worker.mjs`.
Ключ для вызова API вычисляется как SHA-256 от `assistant-ai:` и токена бота.
Это позволяет использовать существующий secret, не отправляя сам токен в запросах.
Ключ даёт доступ к генерации ответов, его тоже нельзя публиковать.

В `.env` Python-приложения:

```dotenv
AI_BASE_URL=https://YOUR-WORKER.workers.dev/v1
AI_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast
AI_API_KEY=YOUR_DERIVED_KEY
AI_MAX_TOKENS=900
```

`GET /health` проверяет наличие настроек, но не выполняет генерацию.
`POST /v1/chat/completions` требует `Authorization: Bearer <AI_API_KEY>`.
Лимиты: 128 000 байт тела, 32 000 символов контекста, до 1500 токенов ответа,
30 секунд ожидания модели. При исчерпании лимита Cloudflare генерация недоступна.
Повторных вызовов модели адаптер не делает.

Модель: [Llama 3.3 70B](https://developers.cloudflare.com/workers-ai/models/llama-3.3-70b-instruct-fp8-fast/).
