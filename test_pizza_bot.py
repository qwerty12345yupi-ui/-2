import json
import threading
import unittest
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pizza_bot


def sample_order(**overrides):
    payload = {
        "requestId": str(uuid.uuid4()),
        "customer": {
            "name": "Анна",
            "phone": "+7 900 000-00-00",
            "address": "Тестовая улица, дом 1",
        },
        "fulfillment": "delivery",
        "deliveryTime": "Как можно скорее (30–45 минут)",
        "promoCode": "",
        "items": [{"id": "margherita", "size": "medium", "quantity": 1}],
    }
    payload.update(overrides)
    return payload


class FakeTelegram:
    owner_chat_id = "12345"

    def __init__(self):
        self.orders = []

    def send_order(self, order, order_id):
        self.orders.append((order, order_id))


class OrderValidationTests(unittest.TestCase):
    def test_server_calculates_delivery_and_total(self):
        order = pizza_bot.validate_order(sample_order())
        self.assertEqual(order["subtotal"], 590)
        self.assertEqual(order["delivery"], 149)
        self.assertEqual(order["total"], 739)

    def test_server_calculates_pickup_promo_and_sizes(self):
        payload = sample_order(
            fulfillment="pickup",
            promoCode="первый",
            items=[{"id": "bbq", "size": "large", "quantity": 2}],
        )
        order = pizza_bot.validate_order(payload)
        self.assertEqual(order["items"][0]["unit_price"], 965)
        self.assertEqual(order["subtotal"], 1930)
        self.assertEqual(order["discount"], 193)
        self.assertEqual(order["delivery"], 0)
        self.assertEqual(order["total"], 1737)

    def test_rejects_unknown_product_and_invalid_phone(self):
        payload = sample_order(items=[{"id": "free-pizza", "size": "medium", "quantity": 1}])
        with self.assertRaises(pizza_bot.OrderValidationError):
            pizza_bot.validate_order(payload)
        payload = sample_order(fulfillment=[])
        with self.assertRaises(pizza_bot.OrderValidationError):
            pizza_bot.validate_order(payload)
        payload = sample_order(items=[{"id": [], "size": "medium", "quantity": 1}])
        with self.assertRaises(pizza_bot.OrderValidationError):
            pizza_bot.validate_order(payload)
        payload = sample_order(customer={"name": "Анна", "phone": "bad", "address": "Улица"})
        with self.assertRaises(pizza_bot.OrderValidationError):
            pizza_bot.validate_order(payload)

    def test_escapes_untrusted_order_text_in_telegram_message(self):
        order = pizza_bot.validate_order(sample_order(customer={
            "name": "<b>Анна</b>",
            "phone": "+7 900 000-00-00",
            "address": "<script>alert(1)</script>",
        }))
        message = pizza_bot.format_order_message(order, "ABC12345")
        self.assertIn("&lt;b&gt;Анна&lt;/b&gt;", message)
        self.assertIn("&lt;script&gt;", message)
        self.assertIn("ТЕСТОВЫЙ ЗАКАЗ", message)


class OrderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.telegram = FakeTelegram()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), pizza_bot.OrderRequestHandler)
        cls.server.allowed_origins = {"http://localhost:8765"}
        cls.server.telegram = cls.telegram
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post_order(self, payload, origin="http://localhost:8765"):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(
            "POST",
            "/api/orders",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Origin": origin},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def get(self, path):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        connection.close()
        return status, content_type, body

    def test_serves_test_site_and_health_without_exposing_secrets(self):
        status, content_type, body = self.get("/pizza-test.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("Тесто".encode("utf-8"), body)
        status, content_type, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertIn(b'"telegramConfigured": true', body)

    def test_forwards_order_and_deduplicates_retry(self):
        payload = sample_order()
        first_status, first = self.post_order(payload)
        retry_status, retry = self.post_order(payload)
        self.assertEqual(first_status, 201)
        self.assertEqual(retry_status, 200)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(first["orderId"], retry["orderId"])
        self.assertEqual(len(self.telegram.orders), 1)

    def test_rejects_unapproved_origin(self):
        status, body = self.post_order(sample_order(), origin="https://untrusted.example")
        self.assertEqual(status, 403)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
