# Тестовый Telegram-бот пиццерии

`pizza-test.html` — отдельная тестовая копия; исходный `index.html` не меняется. Для локального теста можно использовать Python-сервер, а для публикации — Cloudflare Worker и Telegram webhook. Заказы остаются демонстрационными: оплаты и доставки нет.

## Быстрый запуск на этом компьютере

1. Создай новый токен через `@BotFather`; вводи его только в защищённом запросе запуска и никогда не помещай его в HTML, исходники или чат.
2. Открой PowerShell в папке проекта и запусти:

   ```powershell
   .\start_pizza_bot.ps1
   ```

3. Вставь новый токен в скрытый запрос PowerShell и укажи свой числовой chat ID. Его можно узнать командой `/whoami` в боте. Скрипт запускает сайт и Telegram polling без сторонних Python-библиотек.
4. Открой Telegram-бота и нажми Start. Открой `http://127.0.0.1:8765/pizza-test.html`, создай тестовый заказ и проверь сообщение в Telegram.
5. Оставь PowerShell открытым: пока процесс работает и компьютер включён, сервер принимает заказы. Остановить — `Ctrl+C`.

Проверить сервер можно по адресу `http://127.0.0.1:8765/health`. Тесты без Telegram-токена запускаются командой `python -m unittest -v test_pizza_bot.py`.

## Что значит автономная работа

Бот самостоятельно опрашивает Telegram и пересылает туда заказы с сайта, но локальный запуск не переживёт выключение компьютера. Для работы 24/7 нужен постоянно работающий сервер/хостинг. Перед публичным запуском нужно настроить HTTPS, домен в `PIZZA_ALLOWED_ORIGINS`, постоянное хранилище секретов и дополнительную защиту; не выставляй встроенный HTTP-сервер напрямую в интернет.

Это учебный макет: заявки помечены как тестовые, оплата и доставка не выполняются. Не вводи реальные адреса и телефоны. Токен хранится только в скрытом вводе/переменной процесса и никогда не должен попадать в HTML, исходники или переписку.

## Публикация портфолио на GitHub Pages

Workflow `.github/workflows/pages.yml` публикует исходную главную страницу `index.html` без изменений, тестовую пиццерию, страницы Каспера и изображения пиццы. Код Worker и локальные Python-файлы в сайт не копируются.

Если публикация не запускается автоматически:

1. В репозитории открой **Settings → Pages**.
2. В **Build and deployment → Source** выбери **GitHub Actions**.
3. Открой вкладку **Actions** и перезапусти workflow **Publish portfolio pages**.

Для репозитория `qwerty12345yupi-ui/-2` ожидаемые адреса после успешного деплоя:

- `https://qwerty12345yupi-ui.github.io/-2/`
- `https://qwerty12345yupi-ui.github.io/-2/pizza-test.html`
- `https://qwerty12345yupi-ui.github.io/-2/kasper.html`
- `https://qwerty12345yupi-ui.github.io/-2/kasper-chat.html`

GitHub Pages сам по себе не запускает Python или Telegram-бота. Для заказов используется отдельный Worker.

## Cloudflare Worker и Telegram webhook

Worker находится в `cloudflare/pizza-orders`. Он повторно проверяет состав и цены корзины, ограничивает частоту отправок, не отправляет один `requestId` повторно и пересылает заказ владельцу в Telegram. D1 хранит только идентификатор запроса и короткий срок дедупликации, но не имя, телефон или адрес. Worker-версия тоже помечает заявку как тестовую.

Для подготовки нужен аккаунт Cloudflare и действующий токен бота. Если токен когда-либо публиковался, сначала отзови его через `@BotFather` (`/revoke`) и создай новый; не присылай токен в чат.

1. В PowerShell перейди в каталог Worker и войди в Cloudflare:

   ```powershell
   Set-Location .\cloudflare\pizza-orders
   npx wrangler login
   ```

2. Создай D1 и примени миграцию. Команда создания покажет `database_id`; внеси его в `wrangler.toml` вместо нулевого UUID.

   ```powershell
   npx wrangler d1 create pizza-demo-orders
   npx wrangler d1 migrations apply pizza-demo-orders --remote
   ```

3. Добавь секреты через интерактивные запросы Wrangler. Вставляй значения непосредственно в запрос Wrangler, не в команду, исходники или переписку:

   ```powershell
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_OWNER_CHAT_ID
   npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
   npx wrangler secret put RATE_LIMIT_SALT
   ```

   `TELEGRAM_OWNER_CHAT_ID` можно узнать, запустив бота и отправив ему `/whoami`. Для `TELEGRAM_WEBHOOK_SECRET` и `RATE_LIMIT_SALT` задай разные случайные строки не короче 32 символов и сохрани их локально: первая понадобится для регистрации webhook.

4. Опубликуй Worker:

   ```powershell
   npx wrangler deploy
   ```

   Адрес будет показан в выводе Wrangler и обычно имеет вид `https://pizza-order-demo.<account-subdomain>.workers.dev`.

5. Зарегистрируй Telegram webhook безопасным интерактивным скриптом. Он запросит токен и webhook-секрет скрытым вводом; используй тот же секрет, что добавлен в Cloudflare:

   ```powershell
   .\configure-telegram-webhook.ps1 -WorkerUrl "https://pizza-order-demo.<account-subdomain>.workers.dev"
   ```

   Не публикуй токен: Telegram Bot API использует его в адресе запроса.

6. Скопируй HTTPS-адрес Worker в `content` мета-тега `pizza-order-api` в `pizza-test.html` (без завершающего `/`), закоммить и отправь изменение. Например, `https://pizza-order-demo.<account-subdomain>.workers.dev`.

7. Открой `/health` у Worker и отправь тестовую заявку с опубликованной страницы. Бот должен быть запущен через `/start`; реальные телефон и адрес не используй.

Бесплатный план имеет квоты Cloudflare Workers и D1; превышение лимитов может временно остановить API. Это портфолио-демо, не production-система для реальных заказов. Для настоящей эксплуатации потребуются отдельная проверка защиты/лимитов, политика хранения персональных данных, мониторинг и надёжное операционное подтверждение заказов.
