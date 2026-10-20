import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import worker, { validateOrder } from "./index.mjs";

const ORDER = {
  requestId: "12345678-1234-4123-8123-123456789abc",
  customer: {
    name: "Анна",
    phone: "+7 900 000-00-00",
    address: "Тестовая улица, дом 1",
  },
  fulfillment: "delivery",
  deliveryTime: "Как можно скорее (30–45 минут)",
  promoCode: "",
  items: [{ id: "margherita", size: "medium", quantity: 1 }],
};

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

class FakeDatabase {
  orders = new Map();
  rateLimits = new Map();

  prepare(sql) {
    return {
      bind: (...params) => ({
        first: async () => {
          if (sql.includes("INSERT INTO rate_limits")) {
            const [ipHash, windowStart, limit] = params;
            const key = `${ipHash}:${windowStart}`;
            const count = this.rateLimits.get(key) || 0;
            if (count >= limit) return null;
            this.rateLimits.set(key, count + 1);
            return { request_count: count + 1 };
          }
          if (sql.includes("SELECT order_id")) {
            const [requestId] = params;
            const order = this.orders.get(requestId);
            return order ? { order_id: order.order_id } : null;
          }
          return null;
        },
        run: async () => {
          if (sql.includes("INSERT OR IGNORE INTO order_requests")) {
            const [requestId, orderId, createdAt] = params;
            if (this.orders.has(requestId)) return { meta: { changes: 0 } };
            this.orders.set(requestId, { order_id: orderId, created_at: createdAt });
            return { meta: { changes: 1 } };
          }
          if (sql.includes("DELETE FROM order_requests WHERE request_id")) {
            this.orders.delete(params[0]);
          }
          return { meta: { changes: 1 } };
        },
      }),
    };
  }
}

function createEnv() {
  return {
    DB: new FakeDatabase(),
    ALLOWED_ORIGIN: "https://qwerty12345yupi-ui.github.io",
    RATE_LIMIT_SALT: "test-only-salt",
    TELEGRAM_BOT_TOKEN: "test-token",
    TELEGRAM_OWNER_CHAT_ID: "12345",
    TELEGRAM_WEBHOOK_SECRET: "test-webhook-secret",
  };
}

function orderRequest(payload = ORDER, origin = "https://qwerty12345yupi-ui.github.io") {
  return new Request("https://worker.example/api/orders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": String(new TextEncoder().encode(JSON.stringify(payload)).length),
      "CF-Connecting-IP": "192.0.2.10",
      Origin: origin,
    },
    body: JSON.stringify(payload),
  });
}

test("server calculates prices and rejects client-supplied totals", () => {
  const order = validateOrder(ORDER);
  assert.equal(order.subtotal, 590);
  assert.equal(order.delivery, 149);
  assert.equal(order.total, 739);

  const promo = validateOrder({
    ...ORDER,
    fulfillment: "pickup",
    promoCode: "первый",
    items: [{ id: "bbq", size: "large", quantity: 2 }],
    total: 1,
  });
  assert.equal(promo.subtotal, 1930);
  assert.equal(promo.discount, 193);
  assert.equal(promo.total, 1737);
});

test("order validation rejects unknown items and malformed phone numbers", () => {
  assert.throws(
    () => validateOrder({ ...ORDER, items: [{ id: "free-pizza", size: "medium", quantity: 1 }] }),
    /неизвестн/,
  );
  assert.throws(
    () => validateOrder({ ...ORDER, customer: { ...ORDER.customer, phone: "bad" } }),
    /корректный номер/,
  );
});

test("order endpoint forwards valid orders once and returns the same id on retry", async () => {
  const env = createEnv();
  let telegramMessages = 0;
  globalThis.fetch = async () => {
    telegramMessages += 1;
    return Response.json({ ok: true, result: { message_id: telegramMessages } });
  };

  const first = await worker.fetch(orderRequest(), env);
  const firstBody = await first.json();
  assert.equal(first.status, 201);
  assert.equal(first.headers.get("Access-Control-Allow-Origin"), env.ALLOWED_ORIGIN);

  const retry = await worker.fetch(orderRequest(), env);
  const retryBody = await retry.json();
  assert.equal(retry.status, 200);
  assert.equal(retryBody.orderId, firstBody.orderId);
  assert.equal(retryBody.duplicate, true);
  assert.equal(telegramMessages, 1);
});

test("order endpoint rejects unapproved origins", async () => {
  const response = await worker.fetch(orderRequest(ORDER, "https://untrusted.example"), createEnv());
  assert.equal(response.status, 403);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
});

test("order endpoint rate-limits a client after five requests in a window", async () => {
  const env = createEnv();
  globalThis.fetch = async () => Response.json({ ok: true });
  let lastResponse;
  for (let index = 0; index < 6; index += 1) {
    const order = {
      ...ORDER,
      requestId: `12345678-1234-4123-8123-1234567890${index}`,
    };
    lastResponse = await worker.fetch(orderRequest(order), env);
  }
  assert.equal(lastResponse.status, 429);
});

test("webhook checks Telegram secret and answers /whoami", async () => {
  const env = createEnv();
  let sentPayload;
  globalThis.fetch = async (_url, options) => {
    sentPayload = JSON.parse(options.body);
    return Response.json({ ok: true });
  };
  const request = new Request("https://worker.example/telegram/webhook", {
    method: "POST",
    headers: { "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET },
    body: JSON.stringify({ message: { text: "/whoami", chat: { id: 456 } } }),
  });
  const response = await worker.fetch(request, env);
  assert.equal(response.status, 200);
  assert.equal(sentPayload.chat_id, 456);
  assert.match(sentPayload.text, /456/);

  const forbidden = await worker.fetch(
    new Request("https://worker.example/telegram/webhook", { method: "POST" }),
    env,
  );
  assert.equal(forbidden.status, 403);
});
