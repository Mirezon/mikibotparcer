# Telegram-бот: запуск и размещение на хостинге

Проект запускается командой `python v.py`. Бот работает через Telegram Long Polling, поэтому для него не требуется домен, HTTPS или открытый входящий порт.

## 1. Локальный запуск

Требуется Python 3.11-3.13. Python 3.14 может работать, но для хостинга рекомендуется стабильная версия 3.12.

В PowerShell из каталога проекта:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python v.py
```

В `.env` укажите токен, полученный у `@BotFather`:

```dotenv
BOT_TOKEN=токен_бота_от_BotFather
CHECK_INTERVAL_MINUTES=30
DB_NAME=tracker.db
```

Остановить локальный запуск: `Ctrl+C`.

## 2. Размещение на Linux VPS

Ниже приведен вариант для Ubuntu/Debian с пользователем `bot` и каталогом `/opt/mikibotparcer`.

### Подготовка сервера

Подключитесь к серверу по SSH и выполните:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
sudo useradd --system --home /opt/mikibotparcer --shell /usr/sbin/nologin bot || true
sudo mkdir -p /opt/mikibotparcer
sudo chown -R "$USER":"$USER" /opt/mikibotparcer
```

Загрузите в `/opt/mikibotparcer` файлы `v.py`, `requirements.txt`, `.env.example` и, если нужно сохранить существующие подписки, `tracker.db`. Это можно сделать через Git, SCP или SFTP. Не загружайте `.venv`, `__pycache__` и настоящий `.env` в публичный репозиторий.

Из каталога проекта установите зависимости:

```bash
cd /opt/mikibotparcer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

В `.env` укажите настоящий `BOT_TOKEN`. Затем назначьте владельца файлов:

```bash
sudo chown -R bot:bot /opt/mikibotparcer
sudo chmod 600 /opt/mikibotparcer/.env
```

### Запуск как системный сервис

В репозитории есть готовый файл `deploy/mikibot.service`. Установите его так:

```bash
sudo cp deploy/mikibot.service /etc/systemd/system/mikibot.service
sudo systemctl daemon-reload
sudo systemctl enable --now mikibot
sudo systemctl status mikibot
```

После `enable --now` бот работает в фоне. SSH-консоль можно закрыть: `systemd` продолжит работу независимо от SSH-сессии и запустит бота снова после перезагрузки сервера.

### Проверка и управление

Показать журнал в реальном времени:

```bash
sudo journalctl -u mikibot -f
```

Основные команды:

```bash
sudo systemctl restart mikibot   # применить изменения к коду или .env
sudo systemctl stop mikibot      # остановить
sudo systemctl start mikibot     # запустить
sudo systemctl status mikibot    # проверить состояние
```

Сервис настроен на автоматический перезапуск при ошибке. Если бот не запускается, сначала проверьте журнал и значение `BOT_TOKEN`:

```bash
sudo journalctl -u mikibot -n 100 --no-pager
```

## 3. Быстрый запуск без systemd

Для временного запуска на VPS можно использовать `tmux`:

```bash
sudo apt install -y tmux
cd /opt/mikibotparcer
tmux new -s mikibot
. .venv/bin/activate
python v.py
```

Нажмите `Ctrl+B`, затем `D`, чтобы отсоединиться от сессии. Бот останется работать после закрытия SSH. Вернуться к журналу можно командой:

```bash
tmux attach -t mikibot
```

Для постоянной эксплуатации используйте `systemd`: `tmux` не заменяет автозапуск после перезагрузки.

## 4. Обновление бота

Сделайте резервную копию базы, обновите файлы и перезапустите сервис:

```bash
cd /opt/mikibotparcer
sudo cp tracker.db tracker.db.backup-$(date +%F-%H%M)
# загрузить новые v.py и requirements.txt
sudo -u bot /opt/mikibotparcer/.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart mikibot
sudo systemctl status mikibot
```

`tracker.db` содержит подписки пользователей. Не удаляйте его при обновлении, если хотите сохранить данные.

## Важные ограничения

- Не запускайте два экземпляра этого бота одновременно: Telegram не позволит двум polling-процессам получать одни и те же обновления.
- Храните `.env` в секрете. Если токен стал доступен другим, перевыпустите его через `@BotFather`.
- Для polling не нужно открывать порт на сервере. Серверу нужен исходящий доступ в интернет к Telegram и к сайтам-источникам.
- Если провайдер хостинга останавливает бесплатные или спящие приложения, выберите VPS или тариф с постоянным процессом.