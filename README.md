# matrix-it-support-bot

Бот Службы технической поддержки для **Matrix Synapse** с интеграцией **Jira**: приём заявок,
статусы, уведомления, вложения, комментарии, комната администраторов.

Бизнес-логика перенесена из корпоративного Telegram-бота
[`otr_bot`](https://github.com/dtyugaev/otr_bot) (ветка `fix_notif`) и адаптирована под
экосистему Matrix: вместо inline-кнопок — текстовые меню с эмодзи-реакциями.

## Возможности

- Подключение к Synapse по `access_token`, auto-join, «забывание» комнаты при кике.
- Одна комната = один пользователь; у пользователя может быть несколько комнат, уведомления
  приходят во все.
- Меню на реакциях `m.reaction`: бот сам расставляет эмодзи, пользователь выбирает пункт реакцией.
- Заявки Jira: создание, список с пагинацией, поиск по номеру, комментарии, предоставление
  информации, переоткрытие, подтверждение решения, вложения одним zip-архивом.
- Динамическое подменю действий: состав пунктов зависит от текущего статуса заявки.
- Уведомления через плагин **Jira Status Listener** + резервный JQL-опрос.
- Автоочистка вложений по TTL: сообщение в комнате, локальный файл, медиа-хранилище Synapse
  (Admin API с fallback-токеном).
- Комната администраторов с настраиваемым набором событий и командами `!admin …`.
- Все фразы бота — в одном файле `src/texts.py`, конфигурация — в едином `config.yaml`.

## Требования

Python 3.13+, доступ к Synapse и Jira, PostgreSQL (или SQLite), плагин Jira Status Listener.

## Быстрый старт

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml   # заполните токен Matrix, доступ к Jira и БД
chmod 600 config.yaml
python3 main.py --config config.yaml
```

Дальше: пригласите бота в комнату — он войдёт сам и пришлёт гостевое меню. Нажмите реакцию
«▶️ Старт» или отправьте `!start`.

## Команды пользователя

| Команда | Назначение |
|---|---|
| `!start` | Регистрация пользователя в этой комнате |
| `!create` | Создать заявку в Jira |
| `!list` | Список моих заявок |
| `!status IT-1234` | Статус заявки по номеру |
| `!menu` | Главное (или гостевое) меню |
| `!help` | Справка по командам и навигации |

Команды администратора (в комнате администраторов): `!admin help`, `!admin status`,
`!admin users`, `!admin export_users`, `!admin audit`, `!admin stats`, `!admin logs`,
`!admin send`, `!admin broadcast`, `!admin answer`.

## Структура проекта

```
matrix-it-support-bot/
├── main.py                     # точка входа
├── config.yaml(.example)       # конфигурация (все параметры прокомментированы)
├── requirements.txt
├── src/
│   ├── bot.py                  # сборка сервисов, обработчики nio, sync-цикл, фоновые задачи
│   ├── config.py, logger.py, constants.py, texts.py
│   ├── db/                     # base.py, schema.py, repository.py
│   ├── jira/                   # client.py, transitions.py, status_listener.py
│   ├── matrix/                 # client.py, menu.py, formatting.py, admin_api.py
│   ├── handlers/               # commands, reactions, messages, membership, admin, actions
│   ├── services/               # users, issues, attachments, ui, admin_room, notifications
│   └── utils/                  # validators, text, dates
├── deploy/                     # systemd unit, Dockerfile, docker-compose.yml
├── docs/                       # руководства (.docx), architecture.png/.mmd, анализ эталона
├── storage/                    # БД sqlite, журнал, вложения, nio_store
└── tests/                      # юнит- и интеграционные тесты
```

## Тесты

```bash
pytest -q                 # обычный запуск
python3 tests/run_all.py  # запуск без pytest
```

## Документация

- `docs/Руководство пользователя.docx` — команды, дерево меню, примеры диалогов.
- `docs/Руководство администратора.docx` — установка, все параметры `config.yaml`, запуск,
  обслуживание, комната администраторов, оформление меню, обоснование библиотек.
- `docs/Анализ эталонного проекта.md` — карта модулей, схема БД, сценарии, матрица
  «статус → действия», что не перенесено и почему.
- `docs/architecture.png`, `docs/architecture.mmd` — схема архитектуры.

## Лицензия и поддержка

Внутренний проект ОТР. Вопросы — в комнату администраторов бота или на it@otr.ru.
