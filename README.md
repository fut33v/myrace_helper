# MyRace Helper

Скрипт помогает авторизоваться на https://myrace.info и сохранить рабочую сессию для дальнейшей автоматизации (например, создания промокодов).

## Быстрый старт

1. Установите зависимости (нужен Python 3.9+):
   ```bash
   python3 -m pip install -r requirements.txt
   ```
2. Запустите скрипт, указав почту:
   ```bash
   python3 myrace_login.py --email user@example.com
   ```
   Скрипт либо инициирует отправку письма со ссылкой, либо (при новой схеме) попросит ввести пароль и одноразовый код.
   Если файл `myrace_credentials.json` лежит рядом со скриптом, поля `email` и `password` из него подставятся автоматически.
   Код подтверждения можно передать заранее через `--otp` или ввести вручную по запросу.
3. После завершения входа проверка по умолчанию попробует открыть страницу купонов для забега `1440`. Куки сохраняются в файл `cookies/myrace_cookies.txt`.

Дополнительные флаги см. через `python3 myrace_login.py -h`.

### Полезные опции

- `--timeout 60` — увеличить тайм-аут запросов (по умолчанию 30 с).
- `--retries 5 --backoff 2` — добавить больше повторов с экспоненциальной задержкой, если сайт отвечает медленно.
- `--reuse-session` — пропустить отправку письма и использовать сохранённые cookies.
- `--credentials-file other.json` — путь к JSON с полями `email` и `password` (по умолчанию `myrace_credentials.json`).
- `--otp 123456` — одноразовый код из письма (если сайт запрашивает подтверждение входа).

### Работа с промокодами

1. После успешного логина можно показать доступные шаблоны для забега:
   ```bash
   python3 myrace_login.py --reuse-session --race-id 1440 --list-types
   ```
2. Чтобы создать промокод, укажите подходящий тип (по названию или части ссылки) и подставьте значения полей формы:
   ```bash
   python3 myrace_login.py \
     --reuse-session \
     --race-id 1440 \
     --coupon-type "Скидка 100%" \
     --field code=BLACKFRIDAY \
     --field discount=100
   ```
   Добавьте `--show-fields` для просмотра всех полей и `--dry-run`, если нужно только собрать данные без отправки формы.

### Структура файла `myrace_credentials.json`

```json
{
  "email": "simplycomp@yandex.ru",
  "password": "ВАШ_ПАРОЛЬ"
}
```

Если файл переименован, передайте путь через `--credentials-file`. Пароль используется только в тех сценариях, где MyRace запрашивает его перед отправкой одноразового кода.

### Импорт cookies из браузера

Если у вас уже есть авторизованная вкладка, можно экспортировать cookies из браузера и конвертировать их в формат Netscape:

1. Экспортируйте cookies для домена `myrace.info` в JSON (например, через DevTools или расширение).
2. Сохраните результат в файл `cookies_browser.json`.
3. Выполните:
   ```bash
   python3 convert_cookies.py --input cookies_browser.json --output cookies/myrace_cookies.txt
   ```
4. Запустите основной скрипт с флагом `--reuse-session`, чтобы работать с этими cookie без повторной авторизации.

## Автоматизация через Selenium

Файл `myrace_selenium.py` использует реальный браузер (Chrome или Firefox), чтобы пройти весь поток входа и создать промокод.

1. Убедитесь, что установлен Selenium и выбранный браузер (Chrome ≥ 115 поддерживает встроенный Selenium Manager).
   ```bash
   python3 -m pip install -r requirements.txt
   ```
2. Запуск с использованием сохранённых cookies:
   ```bash
   python3 myrace_selenium.py --race-id 1440 --coupon-type "Скидка 100%" \
     --field code=BLACKFRIDAY --field discount=100 --dry-run --show-fields
   ```
   Команда откроет страницу создания промокода, покажет доступные поля и заполнит их без отправки.
3. Чтобы пройти полный вход (email → пароль → код из письма) вручную:
   ```bash
   python3 myrace_selenium.py --email simplycomp@yandex.ru --password "пароль" \
     --race-id 1440 --coupon-type "Скидка 100%" --field code=BLACKFRIDAY --field discount=100
   ```
   Если требуется код подтверждения, скрипт попросит ввести его в консоли либо примет через `--otp`.
4. Добавьте `--headless`, чтобы запустить браузер без GUI, и `--save-cookies`, чтобы сохранить обновлённую сессию обратно в `cookies/myrace_cookies.txt`.

## Пакетное создание промокодов

Для выпуска серии кодов воспользуйтесь `create_promo_codes.py`. По умолчанию он создаёт `tipacyclo3`…`tipacyclo7` со 100 % скидкой, лимитом 1 и выбором всех слотов.

```bash
python3 create_promo_codes.py \
  --coupon-type "Скидка 100%" \
  --race-id 1440 \
  --headless
```

Основные флаги:

- `--codes code1 code2 …` — свой список кодов.
- `--discount`, `--deduction`, `--usage-limit`, `--slot-value` — параметры скидки/лимита/слотов.
- `--field name=value` — точечное переопределение полей формы, если авто‑подстановка не совпадает.
- Добавьте `--dry-run`, чтобы проверить заполнение без реального создания.
- `--step-delay 3` — делает паузу между шагами, чтобы можно было наблюдать браузер.

## Запуск в Docker

Если на сервере неудобно ставить браузер, можно собрать контейнер:

```bash
docker build -t myrace-helper .
```

Запуск скрипта с теми же аргументами (по умолчанию открывается `create_promo_codes.py`):

```bash
docker run --rm -it \
  -v "$(pwd)/cookies:/app/cookies" \
  myrace-helper \
  python3 create_promo_codes.py \
    --coupon-type "На определенную дистанцию" \
    --codes tipacyclo3 tipacyclo4 tipacyclo5 tipacyclo6 tipacyclo7 \
    --discount 100 \
    --usage-limit 1 \
    --slot-value all \
    --race-id 1440 \
    --headless \
    --step-delay 0
```

Ключевые моменты:

- Внутри образа уже установлены Chromium и chromedriver.
- Для удобства можно задать другую команду на запуск, например `docker run … myrace-helper python3 myrace_selenium.py --email ...`.
- Для headless-режима достаточно оставить `--headless`; если хотите наблюдать процесс, уберите флаг и пробросьте X11 или используйте VNC.

Для удобства есть скрипт `run_bot.sh`, который собирает образ и запускает Telegram-бота внутри контейнера:

```bash
./run_bot.sh
```

По умолчанию используется `.env` (для `TELEGRAM_BOT_TOKEN` и других переменных), а также каталог `cookies` с файлом `myrace_cookies.txt`, который пробрасывается в контейнер. Можно переопределить:

```bash
ENV_FILE=prod.env COOKIES_DIR=prod_cookies ./run_bot.sh --extra-arg
```

Все дополнительные аргументы после скрипта передаются `telegram_bot.py`.

## Telegram-бот для создания промокодов

Файл `telegram_bot.py` разворачивает бота, который принимает команды и вызывает `create_promo_codes.py` под капотом.

### Настройка окружения

1. Создайте бота через `@BotFather`, получите токен и выставьте переменную окружения `TELEGRAM_BOT_TOKEN`.
2. Бот работает только с готовым файлом cookies (без логина/пароля). Укажите:
- `MYRACE_COOKIES_PATH` — путь к файлу cookies (по умолчанию `cookies/myrace_cookies.txt`).
- `MYRACE_RACE_ID` (по умолчанию `1440`) и `MYRACE_COUPON_TYPE` (можно указать несколько вариантов через `|`, например `На определенную дистанцию|At a certain distance`).
- `MYRACE_SLOT_VALUE`, `MYRACE_USAGE_LIMIT`, `MYRACE_STEP_DELAY` для дополнительных настроек.
- `TELEGRAM_ADMIN_IDS` — список ID пользователей через запятую. Только эти пользователи могут запускать команды создания промокодов.
- `MYRACE_RACES_PATH` — путь к JSON-файлу с вручную добавленными гонками (по умолчанию `races.json`).

### Запуск

```bash
python3 telegram_bot.py
```

Команды в Telegram:

- `/promo100 <код> [лимит]` — создаёт промокод со 100 % скидкой и лимитом (по умолчанию 1).
- `/promo <код> <скидка> [лимит]` — произвольная скидка и ограничение.
- `/setcookies` — бот попросит прислать JSON с cookies отдельным сообщением и сохранит его в `MYRACE_COOKIES_PATH`.
- `/races` — показать доступные гонки (и ту, что выбрана сейчас).
- `/income` — вывести текущий доход и количество участников для выбранной гонки (через кнопки).
- `/goal [<id>] <сумма>` — установить цель по доходу (по умолчанию для текущей гонки); `/goal clear` снимает цель, `/goal` без аргументов показывает текущие значения.
- `/setrace <id>` — выбрать гонку для будущих промокодов.
- `/addrace <https://myrace.info/events/...>` — добавить гонку вручную с её страницы события.
- `/races` — показывает список доступных гонок и текущую выбранную.
- `/setrace <id>` — выбрать гонку для создания промокодов.

Command list для BotFather:

```
promo100 - создать промокод 100% /promo100 <код> [лимит]
promo - создать промокод /promo <код> <скидка> [лимит]
setcookies - загрузить cookies JSON
races - показать список гонок
income - показать доход и участников
goal - задать цель по доходу
setrace - выбрать текущую гонку
addrace - добавить гонку по ссылке
```

Бот отправляет статус выполнения и сообщение об ошибке (если Selenium-команда завершилась неуспешно).

## Мониторинг дохода гонок

Скрипт `race_income_watcher.py` раз в несколько минут открывает страницу `https://myrace.info/entities/races/<id>`, парсит значения «Участников» и «Ваш доход» и уведомляет администраторов в Telegram при каждом изменении дохода.

### Переменные окружения

- `MYRACE_WATCH_RACE_IDS` — список ID гонок через запятую. Если не задан, берутся ID из `MYRACE_RACES_PATH`, затем `MYRACE_RACE_ID` (по умолчанию 1440).
- `MYRACE_WATCH_INTERVAL` — периодичность проверки в секундах (по умолчанию 300).
- `MYRACE_WATCH_STATE_PATH` — путь к JSON с последними значениями дохода/участников (по умолчанию `data/race_income_state.json`).
- `MYRACE_GOALS_PATH` — файл с целями по доходу (по умолчанию `data/income_goals.json`). Команда `/goal` в боте обновляет этот файл; скедулер берёт цели оттуда и сообщает, сколько осталось до каждой цели.
- Общие переменные: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `MYRACE_COOKIES_PATH`, `MYRACE_RACES_PATH`.

### Запуск

```bash
python3 race_income_watcher.py
```

При первом запуске скрипт только запомнит текущие показатели (уведомлений не будет). Как только «Ваш доход» на странице гонки изменится, админы получат сообщение с прошлым и текущим значением, а также количеством участников.

## Docker Compose

Файл `docker-compose.yml` разворачивает два сервиса в одном образе:

- `bot` — Telegram-бот (`telegram_bot.py`).
- `scheduler` — монитор дохода (`race_income_watcher.py`).

Для корректной работы создайте `.env` с токеном/ID админов и заранее подготовьте каталог `cookies` вместе с файлом `myrace_cookies.txt` (например, `mkdir -p cookies && touch cookies/myrace_cookies.txt`). Каталоги `cookies/` и `data/` монтируются внутрь контейнера, поэтому в них будут храниться cookies, `race_income_state.json`, собранные гонки и прочие артефакты.

Запуск:

```bash
docker compose up -d bot scheduler
```

Чтобы остановить сервисы:

```bash
docker compose down
```

При необходимости можно оставить работающим только один сервис, например `docker compose up bot`.
