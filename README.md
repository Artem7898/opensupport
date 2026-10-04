# OpenSupport

Платформа поиска open source проектов с переходом к GitHub Sponsors и Open Collective.
Философия: открытый код устойчив, когда у людей есть время на его поддержку.

## Что работает

- Русский и английский интерфейсы; выбор языка хранится только на текущем устройстве.
- Каталог из 6 реальных проектов, поиск, фильтры по теме и платформе, сортировка, карточки с источниками.
- Прямые внешние ссылки спонсорства. Для каталога OpenSupport не обрабатывает платежи и не хранит данные карт. Автор платформы отдельно публикует адрес для добровольной поддержки в USDT (TRC20).
- Готовое Django-приложение: регистрация, сессии, заявки, подтверждение доступа к репозиторию, модерация, журнал решений.
- Код владения в `.github/opensupport.json`, срок 24 часа. Сервер сверяет ссылку спонсорства с публичным `FUNDING.yml`.
- Только подтверждённая и одобренная заявка попадает в публичный API. Автор не может модерировать свою заявку.

## Граница опубликованной версии

**Sites размещает только каталог (`dist/`). Django в Sites не запускается.**
Опубликованная версия показывает реальные ссылки спонсорства и позволяет скачать черновик заявки.
Регистрация, отправка заявок и модерация в ней не подключены: интерфейс честно сообщает об этом.
Никаких фиктивных отправок или фиктивных пользователей нет.

Полный сайт запускается из этого же репозитория на Python-хостинге. Django через WhiteNoise отдаёт тот же интерфейс и `/api/` с одного домена. Поэтому CORS и токены в browser storage не нужны.
Для подключения этого API именно к домену Sites необходим отдельный поддерживаемый серверный шлюз и согласованная схема сессий; настройка URL сама по себе не включает авторизацию.

## Локальный запуск с uv

Требуется uv. Зависимости и Python зафиксированы в `uv.lock` и `.python-version`.

```bash
uv sync --frozen
export DJANGO_DEBUG=1
export DJANGO_SECRET_KEY="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(64))')"
uv run backend/manage.py migrate
uv run backend/manage.py collectstatic --noinput
uv run backend/manage.py createsuperuser
uv run backend/manage.py runserver 127.0.0.1:8000
```

Открыть `http://127.0.0.1:8000/`. Администрирование: `/admin/`.
Вначале создать обычного автора через «Добавить проект», а для модерации использовать отдельного администратора.
Секрет в этих командах сохраняется только в текущем shell; для постоянной среды задайте стабильный секрет через менеджер секретов или переменные окружения.

Стек проверенной сборки: **Python 3.14.7, Django 6.1.1**, PostgreSQL для production, SQLite для разработки. Django Nova не требуется: модель данных и процесс модерации не нуждаются в дополнительном слое ORM/schema.

## Процесс размещения

1. Автор регистрируется и создаёт заявку со ссылкой репозитория, описаниями RU/EN, категорией и профилем спонсорства.
2. API выдаёт непредсказуемый код; хранится только SHA-256 кода.
3. Автор публикует JSON с кодом в `.github/opensupport.json` основной ветки и настраивает `.github/FUNDING.yml`.
4. Сервер проверяет публичность репозитория, наличие лицензии SPDX, код, срок действия и соответствие funding URL. Если своего FUNDING.yml нет, проверяется файл в репозитории `<owner>/.github`.
5. Заявка поступает модератору. Модератор проверяет лицензию, связь получателя с проектом и назначение финансирования.
6. После одобрения проект появляется в каталоге. Проверка старше 7 дней требует новой заявки.

GitHub OAuth не используется. Доступ подтверждается возможностью добавить уникальный файл в основную ветку — это подтверждение контроля репозитория, а не юридического владения им.
Начальные 6 проектов добавлены редакционно по открытым источникам, **не имеют статуса подтверждённого автора** и не проходили аудит кода.

## Проверки

```bash
DJANGO_DEBUG=1 uv run backend/manage.py test directory
uv run ruff check backend
uv run ruff format --check backend
node --check dist/app.js
uv run pip-audit --local --progress-spinner off
```

24 проверки покрывают CSRF, анонимные записи, IDOR, права модератора, самостоятельное одобрение, подделку статуса, истёкшие/поддельные коды, смену кода, несоответствие FUNDING.yml, отсутствие публикации до модерации, устаревшую проверку владения, дубли репозиториев, ограничения входа и заголовки безопасности.
GitHub в тестах подменён; это воспроизводимые проверки контрактов, а не имитация проведённой проверки владения реальными проектами.
Адаптивная CSS-сетка, семантическая разметка, доступные подписи, native dialog и reduced-motion реализованы; визуальная проверка в браузере в этой среде недоступна.
WebMCP-фильтр включается только при наличии `document.modelContext`; его проверка в поддерживаемом браузере также недоступна.

## Защита

- CSRF на всех изменяющих API, Django session auth, HTTP-only session cookies, Secure cookies в production.
- `DEBUG=0` по умолчанию, обязательный секрет без production fallback, фиксированный `ALLOWED_HOSTS`.
- HTTPS redirect, HSTS, CSP, запрет framing, nosniff, same-origin requests.
- Пароли от 12 символов с валидаторами Django; сброс пароля по email пока не реализован.
- Ограничения запросов в БД: 12 попыток аутентификации и 40 прочих записей на адрес за 10 минут.
- Входящий IP берётся из `REMOTE_ADDR`, пользовательский `X-Forwarded-For` не принимается. За прокси общий адрес консервативно разделяет лимит между посетителями; production следует дополнить лимитами на контролируемом edge.
- Сервер GitHub фиксирован; пользователь не задаёт хост исходящего запроса. Redirects отключены, установлен timeout, размер ответа ограничен.
- Только HTTPS-профили GitHub Sponsors и Open Collective, без query/fragment/userinfo/port; никаких пользовательских redirect endpoints.
- Код репозитория не скачивается для выполнения. Файлы не исполняются. YAML safe_load, aliases/anchors отклоняются.
- Проверки прав на сервере, блокировка строк при смене состояния, DB constraint для единственного опубликованного репозитория, журнал решений.
- API не отдаёт черновики анонимным посетителям; приватные ответы имеют `Cache-Control: no-store`.

Это MVP с проверяемыми механизмами защиты, а не сертификат неуязвимости. Перед открытым приёмом заявок нужны эксплуатационная настройка, резервное копирование, edge rate limits, мониторинг, периодическая перепроверка funding links и независимый security review. Для администратора рекомендуется ограничить доступ на уровне прокси и включить дополнительную аутентификацию на этом уровне. MFA внутри приложения пока не реализована.

## Docker / PostgreSQL

```bash
cp .env.example .env
# Заполните DJANGO_SECRET_KEY, POSTGRES_PASSWORD, домен и CSRF origin.
# DJANGO_TRUST_PROXY=1 допустим только за вашим прокси,
# который удаляет клиентский X-Forwarded-Proto и задаёт свой.
docker compose build
docker compose up -d db
docker compose run --rm web /app/.venv/bin/python backend/manage.py migrate
docker compose run --rm web /app/.venv/bin/python backend/manage.py createsuperuser
docker compose up -d web
```

Порт приложения публикуется только на `127.0.0.1:8000`. Завершайте TLS на Nginx/Caddy. Файл `.env` не коммитится. Контейнер работает без root, с read-only FS и сброшенными capabilities. Docker smoke в текущей среде не выполнен.
Очистка rate-limit записей раз в час:

```bash
docker compose exec web /app/.venv/bin/python backend/manage.py cleanup_rate_limits
```

Для production GitHub API желательно задать серверный `GITHUB_TOKEN` с минимальными правами чтения публичных репозиториев; он не попадает в интерфейс. Без токена используются анонимные лимиты GitHub.

## Структура

```text
backend/config/         настройки, маршруты, WSGI
backend/directory/      модели, API, проверка, модерация, тесты
dist/                   интерфейс, начальный каталог, security headers
pyproject.toml          зависимости и инструменты
uv.lock                 зафиксированные версии
Dockerfile              Python-приложение
compose.yaml            PostgreSQL + web
.openai/hosting.json     публикация каталога в Sites
```

## Источники

Проверены 2026-10-04. Данные о сборах, спонсорах и звёздах намеренно не выводятся: живого источника финансовой статистики нет.

- [GitHub: FUNDING.yml](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/displaying-a-sponsor-button-in-your-repository)
- [FastAPI / Sebastián Ramírez: sponsorship](https://github.com/sponsors/tiangolo)
- [pytest: FUNDING.yml](https://github.com/pytest-dev/pytest/blob/main/.github/FUNDING.yml)
- [Pydantic: FUNDING.yml](https://github.com/pydantic/pydantic/blob/main/.github/FUNDING.yml)
- [HTTPX: FUNDING.yml](https://github.com/encode/httpx/blob/master/.github/FUNDING.yml)
- [Pallets: общий FUNDING.yml](https://github.com/pallets/.github/blob/main/FUNDING.yml)
- [Starlette: FUNDING.yml](https://github.com/encode/starlette/blob/main/.github/FUNDING.yml)
- [Django deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/)
- [Vercel Geist: типографика](https://vercel.com/geist/typography) и [сетка](https://vercel.com/geist/grid) — ориентиры композиции; визуальная система OpenSupport создана отдельно.

`check --deploy` проходит с одним осознанным предупреждением `security.W021`: HSTS preload не включён до выбора постоянного домена и решения о preload-регистрации. HTTPS, secure cookies и обычный HSTS в production включены.

Дополнительно выполнены DOM-проверки начального каталога, поиска, фильтров, пустого состояния, сброса, смены языка, профиля проекта, честного отключённого приёма заявок и навигации. HTTP smoke через Django test client проверяет `/`, CSS, JavaScript, JSON-каталог, session API и health API.


## Поддержка автора OpenSupport

На всех страницах добавлен отдельный блок поддержки автора платформы и пункт «Поддержать OpenSupport» в навигации.

- Актив: USDT.
- Сеть: TRON (TRC20).
- Адрес, предоставленный автором: `TSa7CFcQRJ5tw5cGHSwMoZ9brdmUC893Lx`.
- Кнопка копирует адрес целиком; при недоступном Clipboard API поле выделяется для ручного копирования.
- Сайт не подключает криптокошелёк, не запрашивает seed/private key, не отправляет переводы и не показывает фиктивные подтверждения оплаты.
- Блок предлагает поставить звезду проекту на GitHub. Пока точный GitHub URL OpenSupport не задан, ссылка с честной подписью «Проекты автора на GitHub» ведёт на `https://github.com/Artem7898?tab=repositories`.
- Поддержка автора платформы отделена от funding links проектов каталога. Это не меняет допустимые ссылки в заявках.

## Вход в админку на сервере

Полный Django-сайт имеет маршрут `/admin/`. После размещения на собственном домене откройте `https://ваш-домен/admin/`. Если Django развёрнут на поддомене, используйте домен Django, например `https://app.example.com/admin/`.

Создать администратора нужно **в среде работающего приложения на сервере** и с теми же настройками БД. Локальная SQLite-база и локальные пользователи не переносятся автоматически в PostgreSQL.

Для уже запущенного Docker Compose:

```bash
docker compose exec web /app/.venv/bin/python backend/manage.py migrate
docker compose exec web /app/.venv/bin/python backend/manage.py createsuperuser
```

Без Docker, из каталога приложения, с загруженными production-переменными окружения:

```bash
uv run --frozen backend/manage.py migrate
uv run --frozen backend/manage.py createsuperuser
```

Команда интерактивно запросит имя, email и пароль. Вход в Django Admin — по имени пользователя и паролю, не через GitHub. Изменение забытого пароля:

```bash
docker compose exec web /app/.venv/bin/python backend/manage.py changepassword ИМЯ_ПОЛЬЗОВАТЕЛЯ
```

Для обычного сотрудника требуются `is_active=True`, `is_staff=True` и нужные permissions. Для модерации дополнительно нужно `directory.review_submission`. Суперпользователь получает эти права. Администратор не может одобрить собственную заявку: для неё нужен другой модератор.

На сервере задайте постоянный `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, реальный `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://ваш-домен`, настройте HTTPS и проксирование `/admin/`, `/api/` и статических ресурсов. Не используйте `runserver` для публикации.

В текущей публикации Sites находится только статический каталог. По её адресу `/admin/` не заработает до подключения настоящего Django-развёртывания. Добавление блока поддержки не меняет это ограничение.
