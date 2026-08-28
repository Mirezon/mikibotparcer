# Telegram-бот

Бот отслеживает изменения игровых страниц и сообщает об обновлениях в Telegram. Проект работает через Telegram Long Polling: для запуска не нужны домен, HTTPS и открытый входящий порт.

Репозиторий: https://github.com/Mirezon/mikibotparcer

## Установка на Ubuntu/Debian VPS

Подключитесь к VPS по SSH и выполните команды ниже. У вас должны быть права `sudo`.

### 1. Установить системные пакеты

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 2. Скачать бота по ссылке с GitHub

```bash
sudo mkdir -p /opt/mikibotparcer
sudo chown "$USER":"$USER" /opt/mikibotparcer
git clone https://github.com/Mirezon/mikibotparcer.git /opt/mikibotparcer
cd /opt/mikibotparcer
```

Если каталог уже существует и бот ранее устанавливался, используйте обновление вместо `git clone`:

```bash
cd /opt/mikibotparcer
git pull origin main
```

### 3. Создать виртуальное окружение и установить зависимости

```bash
cd /opt/mikibotparcer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Создать настройки бота

```bash
cp .env.example .env
nano .env
```

В `.env` укажите токен, который выдал `@BotFather`:

```dotenv
BOT_TOKEN=сюда_вставьте_токен_бота
CHECK_INTERVAL_MINUTES=30
DB_NAME=tracker.db
```

Сохранение в `nano`: `Ctrl+O`, `Enter`, затем выход: `Ctrl+X`.

Настройте системного пользователя и права:

```bash
sudo useradd --system --home /opt/mikibotparcer --shell /usr/sbin/nologin bot 2>/dev/null || true
sudo chown -R bot:bot /opt/mikibotparcer
sudo chmod 600 /opt/mikibotparcer/.env
```

### 5. Запустить в фоне через systemd

Готовый файл сервиса уже находится в репозитории. Установите его:

```bash
sudo cp /opt/mikibotparcer/deploy/mikibot.service /etc/systemd/system/mikibot.service
sudo systemctl daemon-reload
sudo systemctl enable --now mikibot
```

Проверить состояние:

```bash
sudo systemctl status mikibot
```

После `enable --now` SSH-консоль можно закрыть. Бот продолжит работать в фоне, запустится после перезагрузки VPS и автоматически перезапустится после ошибки.

## Управление ботом

```bash
sudo systemctl start mikibot
sudo systemctl stop mikibot
sudo systemctl restart mikibot
sudo systemctl status mikibot
```

Просмотр журнала в реальном времени:

```bash
sudo journalctl -u mikibot -f
```

Последние 100 строк журнала:

```bash
sudo journalctl -u mikibot -n 100 --no-pager
```

## Обновление бота

Перед обновлением сохраните базу данных. В ней хранятся подписки пользователей:

```bash
cd /opt/mikibotparcer
sudo cp tracker.db "tracker.db.backup-$(date +%F-%H%M)"
git pull origin main
sudo /opt/mikibotparcer/.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart mikibot
sudo systemctl status mikibot
```

Не удаляйте `tracker.db`, если нужно сохранить подписки.

## Быстрый временный запуск через tmux

Этот способ оставляет процесс работать после закрытия SSH, но не запускает его автоматически после перезагрузки сервера:

```bash
sudo apt install -y tmux
cd /opt/mikibotparcer
tmux new -s mikibot
. .venv/bin/activate
python v.py
```

Отсоединиться, не останавливая бота: нажмите `Ctrl+B`, затем `D`.

Вернуться в сессию:

```bash
tmux attach -t mikibot
```

Для постоянной работы используйте `systemd`.

## Локальный запуск в Windows

В PowerShell из каталога проекта:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python v.py
```

Остановить бота: `Ctrl+C`.

## Важно

- Не публикуйте файл `.env`: в нем находится секретный токен Telegram.
- Не запускайте два экземпляра бота одновременно, иначе Telegram будет переключать обновления между процессами.
- `tracker.db` игнорируется Git и остается только на сервере.
- VPS должен иметь исходящий доступ в интернет к Telegram и сайтам-источникам.
- Бесплатный хостинг может останавливать фоновые процессы. Для постоянной работы нужен VPS или тариф без остановки приложений.