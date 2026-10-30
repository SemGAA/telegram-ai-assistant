import assert from "node:assert/strict";
import test from "node:test";
import worker, { apiKey } from "./ai-worker.mjs";

const token = "test-token";
const body = {
  model: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  messages: [{ role: "user", content: "Hello" }],
  max_completion_tokens: 900,
  stream: false,
};

async function request(payload = body, key = null, path = "/v1/chat/completions") {
  return new Request("https://example.com" + path, {
    method: "POST",
    headers: { Authorization: "Bearer " + (key ?? await apiKey(token)) },
    body: JSON.stringify(payload),
  });
}

test("authenticated request returns a chat completion", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: { run: async (model, options) => {
    assert.equal(model, body.model);
    assert.deepEqual(options, { messages: body.messages, max_tokens: 900 });
    return { response: "Hello back" };
  } } };
  const response = await worker.fetch(await request(), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).choices[0].message.content, "Hello back");
});

test("unauthorized requests never reach AI", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: { run: () => assert.fail("unexpected AI call") } };
  assert.equal((await worker.fetch(await request(body, "wrong"), env)).status, 401);
  assert.notEqual(await apiKey(token), token);
});

for (const payload of [null, {}, { ...body, model: "another-model" },
  { ...body, messages: [{ role: "tool", content: "x" }] },
  { ...body, messages: [{ role: "user", content: "x".repeat(32001) }] },
  { ...body, max_completion_tokens: 9000 }, { ...body, stream: true }]) {
  test("rejects invalid payload " + JSON.stringify(payload).slice(0, 100), async () => {
    const env = { TELEGRAM_TOKEN: token, AI: { run: () => assert.fail("unexpected AI call") } };
    assert.equal((await worker.fetch(await request(payload), env)).status, 400);
  });
}

test("bounds request body size before parsing", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: {} };
  const response = await worker.fetch(await request({ data: "x".repeat(128001) }), env);
  assert.equal(response.status, 413);
});

test("upstream failure does not expose details", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: { run: async () => { throw new Error("private"); } } };
  const response = await worker.fetch(await request(), env);
  assert.equal(response.status, 502);
  assert.equal(await response.text(), "AI unavailable");
});

test("empty AI answer is an error", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: { run: async () => ({ response: " " }) } };
  assert.equal((await worker.fetch(await request(), env)).status, 502);
});

test("health does not call AI and old webhook is not served", async () => {
  const env = { TELEGRAM_TOKEN: token, AI: {} };
  const health = new Request("https://example.com/health");
  assert.equal((await worker.fetch(health, env)).status, 200);
  assert.equal((await worker.fetch(health, {})).status, 503);
  assert.equal((await worker.fetch(await request(body, "", "/telegram"), env)).status, 404);
});
