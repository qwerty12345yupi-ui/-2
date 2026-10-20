const MAX_REQUEST_BYTES = 24_000;
const RATE_LIMIT = 5;
const RATE_LIMIT_WINDOW_SECONDS = 600;
const IDEMPOTENCY_RETENTION_SECONDS = 7 * 24 * 60 * 60;
const DELIVERY_PRICE = 149;
const FREE_DELIVERY_FROM = 1200;
const PROMO_CODE = "ПЕРВЫЙ";
const DELIVERY_TIMES = new Set([
  "Как можно скорее (30–45 минут)",
  "К точному времени",
]);
const ITEMS = {
  margherita: ["Маргарита", 590],
  pepperoni: ["Пепперони", 690],
  "four-cheese": ["Четыре сыра", 740],
  mushroom: ["Грибная", 650],
  bbq: ["Барбекю", 760],
  veggie: ["Зелёный сад", 640],
  salami: ["Салями & мёд", 780],
  truffle: ["Трюфельная", 890],
  hawaiian: ["Гавайская", 680],
};
const SIZES = {
  small: ["25 см", 0.78],
  medium: ["30 см", 1],
  large: ["35 см", 1.27],
};

class OrderValidationError extends Error {}

class TelegramDeliveryError extends Error {}

function jsonResponse(body, status = 200, origin = null) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  if (origin) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
    headers.set("Access-Control-Allow-Headers", "Content-Type");
    headers.set("Vary", "Origin");
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function text(value, field, limit) {
  if (typeof value !== "string") {
    throw new OrderValidationError(`Поле «${field}» заполнено неверно.`);
  }
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized || normalized.length > limit || /[\u0000-\u001f]/.test(normalized)) {
    throw new OrderValidationError(`Поле «${field}» заполнено неверно.`);
  }
  return normalized;
}

export function validateOrder(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new OrderValidationError("Неверный формат заказа.");
  }

  const requestId = payload.requestId;
  if (typeof requestId !== "string" || !/^[A-Za-z0-9-]{16,64}$/.test(requestId)) {
    throw new OrderValidationError("Не удалось проверить повтор заказа. Обнови страницу.");
  }

  if (!payload.customer || typeof payload.customer !== "object" || Array.isArray(payload.customer)) {
    throw new OrderValidationError("Не заполнены данные покупателя.");
  }
  const name = text(payload.customer.name, "имя", 80);
  const phone = text(payload.customer.phone, "телефон", 20);
  const digitCount = (phone.match(/\d/g) || []).length;
  if (!/^[0-9+()\s-]{7,20}$/.test(phone) || digitCount < 7 || digitCount > 15) {
    throw new OrderValidationError("Укажи корректный номер телефона.");
  }

  const fulfillment = payload.fulfillment;
  if (fulfillment !== "delivery" && fulfillment !== "pickup") {
    throw new OrderValidationError("Выбери доставку или самовывоз.");
  }
  const address = fulfillment === "delivery"
    ? text(payload.customer.address, "адрес", 250)
    : "";

  if (typeof payload.deliveryTime !== "string" || !DELIVERY_TIMES.has(payload.deliveryTime)) {
    throw new OrderValidationError("Выбери время получения заказа.");
  }
  if (!Array.isArray(payload.items) || payload.items.length < 1 || payload.items.length > 20) {
    throw new OrderValidationError("В заказе должны быть товары.");
  }

  const items = [];
  let subtotal = 0;
  let itemCount = 0;
  for (const rawItem of payload.items) {
    if (!rawItem || typeof rawItem !== "object" || Array.isArray(rawItem)) {
      throw new OrderValidationError("Не удалось проверить состав заказа.");
    }
    if (typeof rawItem.id !== "string" || typeof rawItem.size !== "string"
      || !Object.hasOwn(ITEMS, rawItem.id) || !Object.hasOwn(SIZES, rawItem.size)) {
      throw new OrderValidationError("В заказе есть неизвестная позиция.");
    }
    if (!Number.isInteger(rawItem.quantity) || rawItem.quantity < 1 || rawItem.quantity > 30) {
      throw new OrderValidationError("Проверь количество товаров.");
    }
    itemCount += rawItem.quantity;
    if (itemCount > 50) {
      throw new OrderValidationError("В заказе слишком много товаров.");
    }

    const [itemName, basePrice] = ITEMS[rawItem.id];
    const [sizeName, factor] = SIZES[rawItem.size];
    const unitPrice = Math.floor(basePrice * factor + 0.5);
    const lineTotal = unitPrice * rawItem.quantity;
    subtotal += lineTotal;
    items.push({
      id: rawItem.id,
      name: itemName,
      size: sizeName,
      quantity: rawItem.quantity,
      unitPrice,
      lineTotal,
    });
  }

  if (subtotal > 100_000) {
    throw new OrderValidationError("Сумма заказа превышает допустимую.");
  }
  if (typeof payload.promoCode !== "string") {
    throw new OrderValidationError("Не удалось проверить промокод.");
  }
  const hasPromo = payload.promoCode.trim().toUpperCase() === PROMO_CODE;
  if (payload.promoCode.trim() && !hasPromo) {
    throw new OrderValidationError("Промокод больше не действует. Обнови корзину.");
  }
  if (hasPromo && subtotal < 900) {
    throw new OrderValidationError("Для промокода сумма заказа должна быть от 900 ₽.");
  }

  const discount = hasPromo ? Math.floor(subtotal * 0.1 + 0.5) : 0;
  const delivery = fulfillment === "delivery" && subtotal < FREE_DELIVERY_FROM ? DELIVERY_PRICE : 0;
  return {
    requestId,
    name,
    phone,
    fulfillment,
    address,
    deliveryTime: payload.deliveryTime,
    items,
    itemCount,
    subtotal,
    discount,
    delivery,
    total: subtotal - discount + delivery,
    promoCode: hasPromo ? PROMO_CODE : "",
  };
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
  })[character]);
}

function money(amount) {
  return `${amount.toLocaleString("ru-RU")} ₽`;
}

function formatOrderMessage(order, orderId) {
  const items = order.items.map(item =>
    `• ${escapeHtml(item.name)}, ${escapeHtml(item.size)} × ${item.quantity} — <b>${money(item.lineTotal)}</b>`
  ).join("\n");
  const fulfillment = order.fulfillment === "pickup" ? "Самовывоз" : "Доставка";
  const address = order.fulfillment === "pickup" ? "ул. Тестовая, 1" : escapeHtml(order.address);
  const lines = [
    `🍕 <b>ТЕСТОВЫЙ ЗАКАЗ №${escapeHtml(orderId)}</b>`,
    "",
    items,
    "",
    `Покупатель: ${escapeHtml(order.name)}`,
    `Телефон: ${escapeHtml(order.phone)}`,
    `Получение: ${fulfillment}`,
    `Адрес: ${address}`,
    `Время: ${escapeHtml(order.deliveryTime)}`,
    `Сумма: ${money(order.subtotal)}`,
  ];
  if (order.discount) {
    lines.push(`Промокод ${escapeHtml(order.promoCode)}: −${money(order.discount)}`);
  }
  lines.push(
    `Доставка: ${order.delivery ? money(order.delivery) : "Бесплатно"}`,
    `<b>Итого: ${money(order.total)}</b>`,
    "",
    "Учебный макет: заказ не является реальной продажей.",
  );
  return lines.join("\n");
}

async function telegramCall(env, method, payload) {
  const response = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  let result;
  try {
    result = await response.json();
  } catch {
    throw new TelegramDeliveryError("Telegram returned an invalid response.");
  }
  if (!response.ok || !result?.ok) {
    throw new TelegramDeliveryError("Telegram rejected the request.");
  }
  return result;
}

async function hashRateLimitKey(ip, salt) {
  const data = new TextEncoder().encode(`${salt}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function consumeRateLimit(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = await hashRateLimitKey(ip, env.RATE_LIMIT_SALT);
  const now = Math.floor(Date.now() / 1000);
  const windowStart = Math.floor(now / RATE_LIMIT_WINDOW_SECONDS) * RATE_LIMIT_WINDOW_SECONDS;
  const result = await env.DB.prepare(`
    INSERT INTO rate_limits (ip_hash, window_start, request_count)
    VALUES (?, ?, 1)
    ON CONFLICT(ip_hash, window_start) DO UPDATE
      SET request_count = request_count + 1
      WHERE request_count < ?
    RETURNING request_count
  `).bind(key, windowStart, RATE_LIMIT).first();
  return result !== null;
}

function isAllowedOrigin(origin, env) {
  return typeof origin === "string" && origin === env.ALLOWED_ORIGIN;
}

async function handleOrder(request, env, origin) {
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
      },
    });
  }
  if (request.method !== "POST") {
    return jsonResponse({ error: "Метод не поддерживается." }, 405, origin);
  }
  if (!env.DB || !env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_OWNER_CHAT_ID || !env.RATE_LIMIT_SALT) {
    return jsonResponse({ error: "Сервис заказов ещё не настроен." }, 503, origin);
  }
  if (request.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") {
    return jsonResponse({ error: "Ожидается JSON-заказ." }, 415, origin);
  }
  const declaredLength = Number(request.headers.get("Content-Length") || "0");
  if (declaredLength > MAX_REQUEST_BYTES) {
    return jsonResponse({ error: "Заказ слишком большой." }, 413, origin);
  }
  const body = await request.arrayBuffer();
  if (body.byteLength < 1 || body.byteLength > MAX_REQUEST_BYTES) {
    return jsonResponse({ error: "Заказ слишком большой или пустой." }, 413, origin);
  }

  let payload;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return jsonResponse({ error: "Некорректный JSON-заказ." }, 400, origin);
  }
  let order;
  try {
    order = validateOrder(payload);
  } catch (error) {
    if (error instanceof OrderValidationError) {
      return jsonResponse({ error: error.message }, 400, origin);
    }
    throw error;
  }

  if (!await consumeRateLimit(request, env)) {
    return jsonResponse({ error: "Слишком много попыток. Попробуй через несколько минут." }, 429, origin);
  }

  const orderId = crypto.randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase();
  const now = Math.floor(Date.now() / 1000);
  const inserted = await env.DB.prepare(`
    INSERT OR IGNORE INTO order_requests (request_id, order_id, created_at)
    VALUES (?, ?, ?)
  `).bind(order.requestId, orderId, now).run();
  if (!inserted.meta?.changes) {
    const previous = await env.DB.prepare(
      "SELECT order_id FROM order_requests WHERE request_id = ?",
    ).bind(order.requestId).first();
    if (!previous) {
      return jsonResponse({ error: "Не удалось проверить повтор заказа. Попробуй ещё раз." }, 503, origin);
    }
    return jsonResponse({ ok: true, orderId: previous.order_id, duplicate: true }, 200, origin);
  }

  try {
    await telegramCall(env, "sendMessage", {
      chat_id: env.TELEGRAM_OWNER_CHAT_ID,
      text: formatOrderMessage(order, orderId),
      parse_mode: "HTML",
      disable_web_page_preview: true,
    });
  } catch (error) {
    await env.DB.prepare("DELETE FROM order_requests WHERE request_id = ?")
      .bind(order.requestId).run();
    if (error instanceof TelegramDeliveryError) {
      console.error("Telegram did not accept test order", orderId);
      return jsonResponse({ error: "Не удалось отправить заказ в Telegram. Попробуй ещё раз." }, 503, origin);
    }
    throw error;
  }

  console.info("Delivered demo order", orderId, "with", order.itemCount, "items.");
  return jsonResponse({ ok: true, orderId }, 201, origin);
}

function constantTimeEquals(left, right) {
  const encoder = new TextEncoder();
  const a = encoder.encode(left);
  const b = encoder.encode(right);
  let difference = a.length ^ b.length;
  const maxLength = Math.max(a.length, b.length);
  for (let index = 0; index < maxLength; index += 1) {
    difference |= (a[index] || 0) ^ (b[index] || 0);
  }
  return difference === 0;
}

async function handleWebhook(request, env) {
  const suppliedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
  if (!env.TELEGRAM_WEBHOOK_SECRET
    || !constantTimeEquals(suppliedSecret, env.TELEGRAM_WEBHOOK_SECRET)) {
    return jsonResponse({ error: "Forbidden." }, 403);
  }
  if (request.method !== "POST" || !env.TELEGRAM_BOT_TOKEN) {
    return jsonResponse({ error: "Not configured." }, 405);
  }
  const body = await request.arrayBuffer();
  if (body.byteLength < 1 || body.byteLength > MAX_REQUEST_BYTES) {
    return jsonResponse({ error: "Invalid update." }, 413);
  }
  let update;
  try {
    update = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return jsonResponse({ error: "Invalid update." }, 400);
  }
  const message = update?.message;
  const chatId = message?.chat?.id;
  const messageText = typeof message?.text === "string" ? message.text.trim() : "";
  if (!chatId || !messageText.startsWith("/")) {
    return jsonResponse({ ok: true });
  }
  if (messageText === "/whoami" || messageText.startsWith("/whoami@")) {
    await telegramCall(env, "sendMessage", {
      chat_id: chatId,
      text: `Твой Telegram chat ID: ${chatId}.`,
    });
  } else if (messageText.startsWith("/start") || messageText.startsWith("/help")) {
    await telegramCall(env, "sendMessage", {
      chat_id: chatId,
      text: "Бот готов принимать тестовые заказы с сайта «Тесто & Пламя». "
        + "Новые заявки владельцу приходят сюда. Команда /whoami покажет ID этого чата.",
    });
  }
  return jsonResponse({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health" && request.method === "GET") {
      return jsonResponse({
        ok: true,
        mode: "demo",
        telegramConfigured: Boolean(
          env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_OWNER_CHAT_ID && env.TELEGRAM_WEBHOOK_SECRET,
        ),
      });
    }
    if (url.pathname === "/api/orders") {
      const origin = request.headers.get("Origin");
      if (!isAllowedOrigin(origin, env)) {
        return jsonResponse({ error: "Источник запроса не разрешён." }, 403);
      }
      return handleOrder(request, env, origin);
    }
    if (url.pathname === "/telegram/webhook") {
      return handleWebhook(request, env);
    }
    return jsonResponse({ error: "Адрес не найден." }, 404);
  },

  async scheduled(_controller, env) {
    const cutoff = Math.floor(Date.now() / 1000) - 60 * 60;
    const idempotencyCutoff = Math.floor(Date.now() / 1000) - IDEMPOTENCY_RETENTION_SECONDS;
    await Promise.all([
      env.DB.prepare("DELETE FROM rate_limits WHERE window_start < ?").bind(cutoff).run(),
      env.DB.prepare("DELETE FROM order_requests WHERE created_at < ?").bind(idempotencyCutoff).run(),
    ]);
  },
};
