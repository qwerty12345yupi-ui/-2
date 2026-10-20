"""Local test-order receiver for the Testo & Flame demo site."""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 24_000
FREE_DELIVERY_FROM = 1200
DELIVERY_PRICE = 149
PROMO_CODE = "ПЕРВЫЙ"
RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 600
ITEMS = {
    "margherita": ("Маргарита", 590, "margherita.jpg"),
    "pepperoni": ("Пепперони", 690, "pepperoni.jpg"),
    "four-cheese": ("Четыре сыра", 740, "four-cheese.jpg"),
    "mushroom": ("Грибная", 650, "mushroom.jpg"),
    "bbq": ("Барбекю", 760, "bbq.jpg"),
    "veggie": ("Зелёный сад", 640, "veggie.jpg"),
    "salami": ("Салями & мёд", 780, "salami.jpg"),
    "truffle": ("Трюфельная", 890, "truffle.jpg"),
    "hawaiian": ("Гавайская", 680, "hawaiian.jpg"),
}
SIZES = {"small": ("25 см", 0.78), "medium": ("30 см", 1), "large": ("35 см", 1.27)}
DELIVERY_TIMES = {"Как можно скорее (30–45 минут)", "К точному времени"}
PHONE_PATTERN = re.compile(r"^[0-9+()\s-]{7,20}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{16,64}$")
LOG = logging.getLogger("pizza_bot")


class OrderValidationError(ValueError):
    pass


class TelegramError(RuntimeError):
    pass


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def round_price(amount: float) -> int:
    return math.floor(amount + 0.5)


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise OrderValidationError(f"Поле «{field}» заполнено неверно.")
    value = " ".join(value.split())
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise OrderValidationError(f"Поле «{field}» заполнено неверно.")
    return value


def validate_order(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise OrderValidationError("Неверный формат заказа.")

    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise OrderValidationError("Не удалось проверить повтор заказа. Обнови страницу.")

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        raise OrderValidationError("Не заполнены данные покупателя.")
    name = _text(customer.get("name"), "имя", 80)
    phone = _text(customer.get("phone"), "телефон", 20)
    digits = sum(character.isdigit() for character in phone)
    if not PHONE_PATTERN.fullmatch(phone) or not 7 <= digits <= 15:
        raise OrderValidationError("Укажи корректный номер телефона.")

    fulfillment = payload.get("fulfillment")
    if not isinstance(fulfillment, str) or fulfillment not in {"delivery", "pickup"}:
        raise OrderValidationError("Выбери доставку или самовывоз.")
    address = ""
    if fulfillment == "delivery":
        address = _text(customer.get("address"), "адрес", 250)

    delivery_time = payload.get("deliveryTime")
    if not isinstance(delivery_time, str) or delivery_time not in DELIVERY_TIMES:
        raise OrderValidationError("Выбери время получения заказа.")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 20:
        raise OrderValidationError("В заказе должны быть товары.")

    items = []
    subtotal = 0
    item_count = 0
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise OrderValidationError("Не удалось проверить состав заказа.")
        product_id = raw_item.get("id")
        size_key = raw_item.get("size")
        quantity = raw_item.get("quantity")
        if (
            not isinstance(product_id, str)
            or not isinstance(size_key, str)
            or product_id not in ITEMS
            or size_key not in SIZES
        ):
            raise OrderValidationError("В заказе есть неизвестная позиция.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 30:
            raise OrderValidationError("Проверь количество товаров.")
        item_count += quantity
        if item_count > 50:
            raise OrderValidationError("В заказе слишком много товаров.")
        name_, base_price, image = ITEMS[product_id]
        size_name, factor = SIZES[size_key]
        unit_price = round_price(base_price * factor)
        line_total = unit_price * quantity
        subtotal += line_total
        items.append({
            "id": product_id,
            "name": name_,
            "image": image,
            "size": size_name,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    if subtotal > 100_000:
        raise OrderValidationError("Сумма заказа превышает допустимую.")
    promo_code = payload.get("promoCode", "")
    if not isinstance(promo_code, str):
        raise OrderValidationError("Не удалось проверить промокод.")
    if promo_code and promo_code.strip().upper() != PROMO_CODE:
        raise OrderValidationError("Промокод больше не действует. Обнови корзину.")
    if promo_code and subtotal < 900:
        raise OrderValidationError("Для промокода сумма заказа должна быть от 900 ₽.")
    discount = round_price(subtotal * 0.1) if promo_code else 0
    delivery = DELIVERY_PRICE if fulfillment == "delivery" and subtotal < FREE_DELIVERY_FROM else 0
    return {
        "request_id": request_id,
        "name": name,
        "phone": phone,
        "fulfillment": fulfillment,
        "address": address,
        "delivery_time": delivery_time,
        "items": items,
        "item_count": item_count,
        "subtotal": subtotal,
        "discount": discount,
        "delivery": delivery,
        "total": subtotal - discount + delivery,
        "promo_code": PROMO_CODE if promo_code else "",
    }


def format_order_message(order: dict[str, Any], order_id: str) -> str:
    items_text = "\n".join(
        f"• {html.escape(item['name'])}, {html.escape(item['size'])} × {item['quantity']} — "
        f"<b>{money(item['line_total'])}</b>"
        for item in order["items"]
    )
    fulfillment = "Самовывоз" if order["fulfillment"] == "pickup" else "Доставка"
    address = "ул. Тестовая, 1" if order["fulfillment"] == "pickup" else html.escape(order["address"])
    lines = [
        f"🍕 <b>ТЕСТОВЫЙ ЗАКАЗ №{html.escape(order_id)}</b>",
        "",
        items_text,
        "",
        f"Покупатель: {html.escape(order['name'])}",
        f"Телефон: {html.escape(order['phone'])}",
        f"Получение: {fulfillment}",
        f"Адрес: {address}",
        f"Время: {html.escape(order['delivery_time'])}",
        f"Сумма: {money(order['subtotal'])}",
    ]
    if order["discount"]:
        lines.append(f"Промокод {html.escape(order['promo_code'])}: −{money(order['discount'])}")
    lines.extend([
        f"Доставка: {money(order['delivery']) if order['delivery'] else 'Бесплатно'}",
        f"<b>Итого: {money(order['total'])}</b>",
        "",
        "Учебный макет: заказ не является реальной продажей.",
    ])
    return "\n".join(lines)


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT_REQUESTS, window: int = RATE_LIMIT_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            requests = self._requests[key]
            while requests and current - requests[0] >= self.window:
                requests.popleft()
            if len(requests) >= self.limit:
                return False
            requests.append(current)
            return True


class IdempotencyStore:
    def __init__(self, max_entries: int = 2000):
        self.max_entries = max_entries
        self._orders: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._orders.get(key)

    def add(self, key: str, order_id: str) -> None:
        with self._lock:
            self._orders[key] = order_id
            self._orders.move_to_end(key)
            while len(self._orders) > self.max_entries:
                self._orders.popitem(last=False)


class TelegramBot:
    def __init__(self, token: str, owner_chat_id: str):
        self.token = token
        self.owner_chat_id = owner_chat_id

    def api_call(self, method: str, data: dict[str, Any], timeout: int = 40) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read(1_000_000))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise TelegramError("Telegram временно недоступен.") from error
        if not isinstance(result, dict) or not result.get("ok"):
            raise TelegramError("Telegram отклонил запрос.")
        return result

    def send_order(self, order: dict[str, Any], order_id: str) -> None:
        if not self.owner_chat_id:
            raise TelegramError("Telegram owner chat is not configured.")
        self.api_call("sendMessage", {
            "chat_id": self.owner_chat_id,
            "text": format_order_message(order, order_id),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    def poll_commands(self) -> None:
        offset = 0
        while True:
            try:
                result = self.api_call("getUpdates", {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                })
                for update in result.get("result", []):
                    offset = max(offset, int(update["update_id"]) + 1)
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    text = message.get("text", "")
                    if text.startswith("/whoami"):
                        self.api_call("sendMessage", {
                            "chat_id": chat["id"],
                            "text": f"Твой Telegram chat ID: {chat['id']}. "
                                    "Добавь его в TELEGRAM_OWNER_CHAT_ID на сервере.",
                        })
                    elif text.startswith(("/start", "/help")):
                        self.api_call("sendMessage", {
                            "chat_id": chat["id"],
                            "text": "Бот готов принимать тестовые заказы с сайта «Тесто & Пламя». "
                                    "Новые заявки владельцу приходят сюда. Команда /whoami покажет ID этого чата.",
                        })
                time.sleep(0.2)
            except (TelegramError, KeyError, TypeError, ValueError):
                LOG.warning("Не удалось проверить обновления Telegram; повтор через 5 секунд.")
                time.sleep(5)


RATE_LIMITER = RateLimiter()
IDEMPOTENCY = IdempotencyStore()
ORDER_LOCK = threading.Lock()


class OrderRequestHandler(BaseHTTPRequestHandler):
    server_version = "PizzaOrderDemo/1.0"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        LOG.info("%s - %s", self.client_address[0], format_string % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        try:
            content = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        import mimetypes

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache" if path.suffix == ".html" else "public, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path in {"/", "/pizza-test.html"}:
            self._send_file(ROOT / "pizza-test.html")
        elif self.path == "/health":
            self._send_json(200, {
                "ok": True,
                "mode": "demo",
                "telegramConfigured": bool(self.server.telegram.owner_chat_id),
            })
        else:
            match = re.fullmatch(r"/assets/pizzas/([a-z0-9-]+\.jpg)", self.path)
            if not match:
                self.send_error(404)
                return
            self._send_file(ROOT / "assets" / "pizzas" / match.group(1))

    def do_POST(self) -> None:
        if self.path != "/api/orders":
            self._send_json(404, {"error": "Адрес не найден."})
            return

        allowed_origins = self.server.allowed_origins
        origin = self.headers.get("Origin")
        if not origin or origin not in allowed_origins:
            self._send_json(403, {"error": "Источник запроса не разрешён."})
            return
        if not self.server.telegram.owner_chat_id:
            self._send_json(503, {"error": "Бот ещё не настроен: укажи TELEGRAM_OWNER_CHAT_ID."})
            return

        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Ожидается JSON-заказ."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Некорректный размер запроса."})
            return
        if not 1 <= content_length <= MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Заказ слишком большой или пустой."})
            return
        if not RATE_LIMITER.allow(self.client_address[0]):
            self._send_json(429, {"error": "Слишком много попыток. Попробуй через несколько минут."})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            order = validate_order(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, OrderValidationError) as error:
            self._send_json(400, {"error": str(error)})
            return

        with ORDER_LOCK:
            previous_order = IDEMPOTENCY.get(order["request_id"])
            if previous_order:
                self._send_json(200, {"ok": True, "orderId": previous_order, "duplicate": True})
                return
            order_id = uuid.uuid4().hex[:8].upper()
            try:
                self.server.telegram.send_order(order, order_id)
            except TelegramError:
                LOG.error("Telegram delivery failed for test order %s.", order_id)
                self._send_json(503, {"error": "Не удалось отправить заказ в Telegram. Попробуй ещё раз."})
                return
            IDEMPOTENCY.add(order["request_id"], order_id)

        LOG.info("Delivered demo order %s (%s items).", order_id, order["item_count"])
        self._send_json(201, {"ok": True, "orderId": order_id})


def parse_origins(raw: str) -> set[str]:
    origins = {origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()}
    if not origins:
        raise ValueError("ALLOWED_ORIGINS must contain at least one origin.")
    return origins


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner_chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not token:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN in an environment variable. "
            "Never put the bot token in the website."
        )
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        raise SystemExit("TELEGRAM_BOT_TOKEN does not look like a BotFather token.")
    if owner_chat_id and not re.fullmatch(r"-?\d+", owner_chat_id):
        raise SystemExit("TELEGRAM_OWNER_CHAT_ID must be a numeric Telegram chat ID.")

    host = os.environ.get("PIZZA_HOST", "127.0.0.1")
    port = int(os.environ.get("PIZZA_PORT", "8765"))
    origins = parse_origins(os.environ.get(
        "PIZZA_ALLOWED_ORIGINS",
        f"http://127.0.0.1:{port},http://localhost:{port}",
    ))
    telegram = TelegramBot(token, owner_chat_id)
    server = ThreadingHTTPServer((host, port), OrderRequestHandler)
    server.allowed_origins = origins
    server.telegram = telegram
    threading.Thread(target=telegram.poll_commands, name="telegram-polling", daemon=True).start()
    if owner_chat_id:
        LOG.info("Demo order site: http://%s:%s/pizza-test.html", host, port)
    else:
        LOG.warning("TELEGRAM_OWNER_CHAT_ID is unset; the bot can show chat IDs but cannot accept orders.")
        LOG.info("Send /whoami to the bot, set TELEGRAM_OWNER_CHAT_ID, then restart the server.")
    LOG.info("Allowed website origins: %s", ", ".join(sorted(origins)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping test-order server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
