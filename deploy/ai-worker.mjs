const MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const MAX_BODY_BYTES = 128000;

export async function apiKey(token) {
  const bytes = new TextEncoder().encode("assistant-ai:" + token);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

async function readBody(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("empty_body");
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      throw new Error("body_too_large");
    }
    chunks.push(value);
  }
  return JSON.parse(await new Blob(chunks).text());
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && path === "/health") {
      const ready = Boolean(env.AI && env.TELEGRAM_TOKEN);
      return Response.json({ status: ready ? "ok" : "not_configured", service: "ai-api" },
        { status: ready ? 200 : 503 });
    }
    if (request.method !== "POST" || path !== "/v1/chat/completions") {
      return new Response("Not found", { status: 404 });
    }
    if (!env.AI || !env.TELEGRAM_TOKEN) return new Response("Unavailable", { status: 503 });
    if (request.headers.get("Authorization") !== "Bearer " + await apiKey(env.TELEGRAM_TOKEN)) {
      return new Response("Unauthorized", { status: 401 });
    }
    let body;
    try {
      body = await readBody(request);
      if (body?.model !== MODEL || body.stream === true || !Array.isArray(body.messages) ||
          body.messages.length < 1 || body.messages.length > 102 ||
          body.messages.some(message => !["system", "user", "assistant"].includes(message?.role) ||
            typeof message.content !== "string") ||
          body.messages.reduce((size, message) => size + message.content.length, 0) > 32000 ||
          !Number.isInteger(body.max_completion_tokens) || body.max_completion_tokens < 16 ||
          body.max_completion_tokens > 1500) {
        throw new Error("invalid_request");
      }
    } catch (error) {
      return new Response("Invalid request", { status: error.message === "body_too_large" ? 413 : 400 });
    }
    let timer;
    try {
      const result = await Promise.race([
        env.AI.run(MODEL, { messages: body.messages, max_tokens: body.max_completion_tokens }),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("timeout")), 30000);
        }),
      ]);
      if (typeof result?.response !== "string" || !result.response.trim() || result.response.length > 64000) {
        throw new Error("invalid_response");
      }
      return Response.json({ choices: [{ message: { role: "assistant", content: result.response } }] });
    } catch {
      return new Response("AI unavailable", { status: 502 });
    } finally {
      clearTimeout(timer);
    }
  },
};
