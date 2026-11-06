# Промпт для Codex / Cursor: локальная RAG на RTX 5060 Ti 16 ГБ

**Редакция:** 29.10.2026, отдельная ветка RTX5060Ti, v1.3 (открытый стек, UI, агенты). Согласовано с `ROADMAP_local_RAG_RTX5060Ti_16GB.md`. Статус: ТЗ на реализацию; код, запуск и совместимость ещё не проверены.

Ты — senior Python/backend/ML engineer. Работай как coding agent, создавай реальные файлы проекта, не ограничивайся описанием. Реализуй воспроизводимый MVP для имеющегося ПК Windows 11, 32 ГБ RAM, NVIDIA GeForce RTX 5060 Ti 16 ГБ VRAM. Приёмка — 100–500 документов и одна генерация одновременно. 100 000+ документов и 10+ активных пользователей — будущее направление, не обязательство этого оборудования.


## Профиль RTX5060Ti: ресурсы и развёртывание

Это самостоятельные копии исходной редакции от 28.10.2026. Исходники `ROADMAP_local_RAG.md` и `CODEX_CURSOR_PROMPT.md` не изменяются. В этой ветке использовать только пару `ROADMAP_local_RAG_RTX5060Ti_16GB.md` + `CODEX_CURSOR_PROMPT_RTX5060Ti_16GB.md`; изменения между ветками не переносить автоматически. Наличие файлов в чате не означает автоматическое прикрепление к общим источникам проекта.

### Известное оборудование и границы

| Параметр | Зафиксировано для этой ветки |
| --- | --- |
| ОС хоста | Windows 11 |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16 ГБ VRAM |
| Системная RAM | 32 ГБ, отдельно от VRAM |
| CPU | Не указан; определить модель и число логических процессоров |
| Системный SSD | Под Windows; доступный остаток ориентировочно 20–30 ГБ, уточнить фактическое свободное место |
| Диск данных | HDD почти 2 ТБ; фактическое свободное место, буква диска и состояние требуют проверки |
| Режим приёмки | 100–500 документов, один выполняемый LLM-запрос, один ingestion job |

### Единый способ запуска

Основной путь: Windows 11 → WSL2 с Ubuntu → Docker Engine + Compose plugin внутри этой дистрибуции. Для GPU-контейнеров настроить NVIDIA Container Toolkit для этого Engine. Драйвер NVIDIA устанавливается на Windows; Linux display driver внутри WSL не устанавливать. Перед установкой проверить официальные инструкции WSL/NVIDIA, поддержку выбранной версии Ubuntu и текущего драйвера. Docker Desktop не требуется и не входит в обязательный стек; не запускать два Docker daemon параллельно.

Ollama работает только в Compose. Вся рабочая WSL-дистрибуция (ext4 VHDX), включая `/var/lib/docker`, volumes, модели, код и cache, физически размещается на HDD. Данные PostgreSQL/Qdrant хранить в Linux FS, не в bind mount `/mnt/c`. Именованный volume не определяет физический диск: проверить VHDX и `docker info` → Docker Root Dir. Для нового окружения выбрать поддерживаемую установленной версией WSL установку/импорт на HDD; для существующего — backup и поддерживаемый перенос с проверкой восстановления. Не применять `wsl --unregister` и не перемещать работающий VHDX вручную.

Проверить GPU в WSL, затем в контейнере и Ollama; зафиксировать образ теста и digest. Поддержка RTX 5060 Ti в документации не заменяет испытание этого ПК. CPU fallback не считать успешным GPU-запуском. В MVP PyTorch для Docling/embeddings CPU-only; GPU выделена Ollama. Не фиксировать произвольную старую CUDA-сборку для RTX 50.

### Начальные лимиты (гипотезы для замера)

WSL2: `memory=20GB`, `swap=4GB` в `%UserProfile%\.wslconfig`. Лимит общий для WSL-дистрибутивов и Docker; оставить около 12 ГБ физической RAM Windows, браузеру и Cursor. Swap — аварийный запас, не способ увеличить рабочий корпус. Его файл также разместить на HDD через `swapfile` в `.wslconfig`, используя реальный абсолютный Windows-путь; букву диска сначала определить. Подкачка на HDD особенно медленная: постоянный swap означает необходимость уменьшить нагрузку, а не нормальный режим. Перед `wsl --shutdown` сохранить работу и предупредить о завершении всех WSL-сессий. Число `processors` выбрать после определения CPU, не задавать выдуманные 16 ядер.

| Сервис/группа | Начальный предел RAM | Режим |
| --- | --- | --- |
| PostgreSQL | 1 ГБ | Умеренные connection pool и shared_buffers |
| Valkey | 256 МБ | Ограниченный broker; noeviction, сбой доставки восстанавливается outbox |
| Qdrant | 2 ГБ | on-disk vectors/payload, один индекс MVP |
| API + статический React UI | 1 ГБ | Один процесс, без загрузки embedding-модели в API |
| CPU embedding service | 5 ГБ | Единственный экземпляр BGE-M3; query и ingestion используют его |
| Ingestion worker с Docling/OCR | 5 ГБ | Celery concurrency=1, prefetch=1; CPU-only, парсинг по одному документу |
| Dispatcher + reconciler суммарно | 512 МБ | Без ML-моделей |
| Ollama: системная RAM | 3 ГБ | VRAM учитывается отдельно; без штатного CPU offload |

Для nginx зарезервировать дополнительно 128 МиБ. Сумма пределов — около 17.9 ГБ; остаток внутри WSL нужен ядру и служебным процессам. Это стартовая конфигурация, а не гарантия размещения любой модели/страницы: измерить RSS/пики, OOM и потребление WSL; уменьшать batch/размер задания или разделять стадии при превышении. Не увеличивать все лимиты одновременно за предел WSL. Контейнерные лимиты реализовать и проверить через Docker inspect, а не оставить таблицей в README. Тяжёлые сборки выполнять отдельно от рабочего режима.

Один CPU embedding service не должен дублировать веса в API и Celery. Запросы пользователя имеют приоритет между ingestion-микробатчами. Начальный batch=1, максимум 2 CPU-потока на тяжёлый сервис, затем настройка по CPU. Chunk target 300–450 токенов LLM, overlap 40–60, структурные границы сохраняются; отдельно проверять max_length embedding tokenizer. Ограничить исходный upload 25 МиБ и время парсинга, большие файлы обрабатывать отдельным профилем. Размер файла не ограничивает сложность PDF: нужны таймаут, лимит страниц/распаковки и понятный job error. OCR и reranker не включать в постоянную параллельную нагрузку; reranker по умолчанию выключен.

LLM: `qwen3:8b`, выбранный и проверенный Q4_K_M artifact; если тег указывает на иной artifact — явно выбрать нужный и записать digest. Одна загруженная модель, один выполняемый запрос, контекст 4096. Для коротких RAG-ответов выбрать и проверить `think=false` через поддерживаемый API зафиксированной версии; не путать текстовую подсказку с настройкой API. Полный токеновый бюджет и резерв ответа остаются обязательными. 14B — только сравнительный эксперимент после baseline; модели 27B/32B и CPU offload не являются базовой конфигурацией. Не обещать скорость по одному объёму VRAM.

Не запускать одновременно с RAG тяжёлые ComfyUI/video/TTS-модели на этой же GPU. Для переключения остановить приём новых генераций, завершить/отменить текущую, выгрузить LLM и проверить VRAM; возвращение в RAG — с healthcheck. Генерация видео, изображений, речи и распознавание лиц не входят в этот RAG MVP.

### Диск, корпус и локальная сеть

**Размещение на имеющихся дисках:** SSD используется Windows; 20–30 ГБ не выделять под модели или Docker data. Основное хранилище — HDD почти 2 ТБ. Ёмкость диска не равна свободному месту: измерить фактический остаток и состояние диска. Для пилота резервировать ориентировочно 100–150 ГБ свободного места на HDD под образы, модели, originals, индексы и рабочие файлы; это плановая оценка, не обещание точного расхода.

| Что хранится | Физический диск / правило |
| --- | --- |
| Windows и обычные небольшие установочные файлы | Системный SSD; сохранить запас для обновлений и временных файлов |
| Docker Engine data root, images, build cache, volumes | HDD; выбрать data location до pull/build |
| Рабочая WSL-дистрибуция и код | Её VHDX на HDD; код внутри Linux FS |
| Ollama models, embeddings, Docling/OCR artifacts, tokenizer cache | Volumes/каталоги внутри Docker/WSL на HDD; не дублировать кэш на C: |
| PostgreSQL, Qdrant, originals, extracted files, job scratch, logs | Volumes на HDD с ограничением роста логов/временных файлов |
| WSL swap | HDD, только аварийный резерв |
| Backup | Отдельный физический носитель или сетевое хранилище; каталог на том же HDD — только локальная копия |

**Обязательное действие до скачивания моделей:** определить фактический HDD и свободное место, разместить рабочую WSL-дистрибуцию на нём, проверить путь ext4 VHDX и Docker Root Dir внутри этой дистрибуции. Контрольный pull/build должен увеличить занятое место на HDD. Docker Engine data root по умолчанию находится внутри WSL; отдельного VHDX Docker Desktop в этом варианте нет. Перед переносом существующей дистрибуции подготовить проверенную резервную копию и место для временных копий. Команды зависят от установленной версии WSL; сначала проверить её возможности.

После настройки выполнить маленький pull/build и загрузить один документ; проверить рост занятого места на HDD, свободное место SSD и расположение всех cache/temp. Windows всё равно использует системный диск для части установочных/служебных данных — не обещать нулевой расход C:. Настроить контроль свободного места на обоих дисках и ограничить build cache, ротацию логов и job scratch; не удалять volumes автоматической очисткой.

**Особенности HDD:** холодная загрузка моделей, сборка контейнеров, OCR и массовая индексация будут медленнее; случайный доступ PostgreSQL/Qdrant и on-disk индекса может стать узким местом. После загрузки LLM в VRAM генерация не читает все веса с HDD для каждого токена, но полная задержка RAG зависит и от поиска/CPU. Не обещать SSD-производительность и не переносить весь индекс в RAM при её лимите 32 ГБ. Сохранить одну ingestion-задачу; ограничить очереди/пакеты записи, при росте задержки chat приостанавливать фоновую загрузку. Модель держать в VRAM в пределах рабочего сеанса с ограниченным idle timeout, чтобы сократить повторные чтения HDD; выгружать перед ComfyUI.

После 100–500 документов измерить дополнительно disk latency/queue, чтение/запись HDD, cold start модели и chat при ingestion. Затем решать о 1/5/10 тыс. документов. 100 тыс. не обязательны на этой конфигурации. Будущий SSD под активные БД/индексы и Docker data — возможное ускорение после измерений, не условие запуска пилота. Учесть рост VHDX и отдельное место для восстановления/backup.

По умолчанию UI/API доступен только с этого ПК. Для других компьютеров — отдельный LAN-профиль: публиковать только reverse proxy с HTTPS и auth; ограничить Windows Firewall доверенной подсетью (её адрес неизвестен), проверить доступ с другого ПК. БД, Valkey, Qdrant, embeddings и Ollama остаются во внутренней сети Compose. WSL-сеть/проброс портов проверить для выбранного режима; не предполагать автоматическую доступность из LAN. Сначала 1, затем 2–3 пользователя, одна генерация и общая ограниченная очередь. Локальная сеть сама по себе не увеличивает ресурсы GPU.

### Последовательность и приёмка именно этого ПК

1. Hardware report: PowerShell — CPU/RAM/диски, `nvidia-smi`, `wsl --status`, `wsl --version`; WSL — `free -h`, `df -h`, `docker version`, `docker compose version`. Отсутствие команды показать как проблему подготовки, не скрывать.
2. Проверить GPU внутри контейнера Engine по официальным инструкциям NVIDIA для WSL и Container Toolkit. Затем загрузить только одну LLM, выполнить запрос, проверить `ollama ps`, логи и VRAM через `nvidia-smi`; не принимать CPU fallback за успешный GPU-запуск.
3. Поднять БД/очередь/API/CPU embedding service/worker; выполнить один полный сценарий с 3–5 документами и реальной цитатой.
4. Пройти 100–500 документов, затем измерить отдельные режимы chat, ingestion и chat+ingestion. Снять TTFT, latency p50/p95, queue wait, tokens/s, RSS/WSL RAM, peak VRAM, чанки/с, свободный диск. Записать размер выборки и cold/warm отдельно.
5. Проверить queue overflow, restart Docker/Windows, worker crash, ACL/удаление/backup restore и offline. Переполнение очереди возвращает понятный 429/503; OOM не вызывает бесконечный retry. Пилот принимается после прохождения полного сценария без OOM в измеренных режимах; целевую задержку согласовать по baseline.
6. Подготовить README с отдельными блоками PowerShell и WSL bash, резервным копированием volumes/оригиналов и точной инструкцией остановки/возобновления. Не объявлять выполненными проверки, которых coding agent не мог запустить на реальном ПК.

### Проверенные справочники (29.10.2026)

- NVIDIA CUDA on WSL: https://docs.nvidia.com/cuda/wsl-user-guide/index.html
- NVIDIA Container Toolkit / Engine: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- WSL2 memory/swap: https://learn.microsoft.com/windows/wsl/wsl-config
- Ollama hardware support: https://docs.ollama.com/gpu
- Ollama context, concurrency и GPU placement: https://docs.ollama.com/faq

Ссылки подтверждают возможности платформ, а не совместимость конкретного набора образов на данном ПК. Зафиксировать версии после фактического smoke test.


## Условия

- Всё локально, без платных API и внешней передачи документов. Первоначальная загрузка docker images и весов моделей допустима.
- Хост Windows 11; Docker Engine и Compose v2 внутри WSL2, NVIDIA Container Toolkit для GPU. Не требовать переустановки Windows или обязательной установки Docker Desktop.
- Русские и англоязычные документы: бухгалтерские, учебные и другие.
- Не внедряй сразу мультиагентную архитектуру: один оркестратор + интерфейсы инструментов.
- Сначала изучи `ROADMAP_local_RAG_RTX5060Ti_16GB.md` и этот документ. Если существуют `docker-compose.yml`, `.env.example` и код — изучи их; если отсутствуют — создай. Проверь совместимость перед реализацией. Не требуй несуществующий `ROADMAP.md`.
- Подтверждённые пользователем характеристики: Windows 11, 32 ГБ RAM, RTX 5060 Ti 16 ГБ. Известны небольшой системный SSD (около 20–30 ГБ доступного остатка) и HDD почти 2 ТБ. Неизвестны CPU, фактическое свободное место HDD/буква диска и версии драйвера/WSL/Docker. Собери их перед запуском, не выдумывай и не используй характеристики HP ProLiant. Сроки roadmap предварительные.

## Выбранный стек

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16, Valkey (точный совместимый релиз после smoke test), Celery + Valkey через Redis transport (выбрано: retries, маршрутизация и отдельные очереди; PostgreSQL хранит достоверное состояние заданий), Docling (для PDF/DOCX), openpyxl/csv для таблиц после MVP, Qdrant, Ollama с `qwen3:8b` (Q4), embedding `BAAI/bge-m3` на CPU через sentence-transformers (dense backend), локальный reranker выключен до замеров. Один общий CPU embedding service для API и ingestion. Интерфейс MVP: React + TypeScript + Vite, статическая сборка через единый reverse proxy; FastAPI обслуживает /api. Сервер Node в рабочем режиме не нужен. Логи structlog, тесты pytest.

## Открытый стек и границы собственного кода

Выбран модульный монолит FastAPI: доменные модули в одном backend, отдельные worker/embedding процессы по ресурсной необходимости. Kubernetes, отдельный микросервис на каждую кнопку и несколько RAG-фреймворков одновременно не нужны. Готовый парсер, индекс и runtime моделей не переписываем. Свой код отвечает за ACL, версии, публикацию, интеграции, веб-интерфейс и предметные функции.

| Задача | Основной инструмент и лицензия кода | Способ применения / граница |
| --- | --- | --- |
| Контейнеры | Docker Engine/Moby, Compose — Apache-2.0 | Внутри WSL2; версии и digest закрепить после проверки |
| API и контракты | FastAPI MIT, Pydantic MIT | OpenAPI, серверные permissions, общая бизнес-логика |
| Данные | PostgreSQL — PostgreSQL License; SQLAlchemy MIT, Alembic MIT | Истина о версиях/ACL/jobs; миграции в git |
| Фоновые задания | Celery BSD-3-Clause + Valkey BSD-3-Clause | Redis protocol/transport; совместимость проверить тестами доставки, retry и восстановления; результат задания хранить в PostgreSQL |
| Извлечение | Docling MIT; Tesseract Apache-2.0 для выбранного OCR-профиля | CPU; структуру и evidence сохранять. Модели Docling имеют отдельные лицензии |
| Векторный поиск | Qdrant Apache-2.0 | Dense baseline; on-disk, ACL-фильтры; собственный адаптер |
| Ключевые слова | PostgreSQL FTS | Русская/английская конфигурации; точные номера/артикулы дополнительно нормализовать; RRF после baseline |
| LLM runtime | Ollama MIT | Локальная Qwen3-8B Q4; digest/квантование отдельно от лицензии runtime |
| Embeddings | sentence-transformers Apache-2.0 + BGE-M3 MIT (веса) | Один CPU-сервис; dense 1024; пакет/ревизию закрепить |
| Разметка | Label Studio Community Apache-2.0 | Отдельный профиль; обмен JSONL через адаптер, собственная история и ACL в backend |
| Оценивание | pytest MIT; Ragas Apache-2.0 | Основные метрики считаем кодом; Ragas/LLM judge опциональны, локальные providers явно задать |
| Web | React MIT, TypeScript Apache-2.0, Vite MIT | Собственный UI; сборка статики, без постоянного Node/SSR |
| Компоненты UI | MUI Core MIT, TanStack Table MIT, Apache ECharts Apache-2.0 | Только открытые базовые пакеты; платные MUI X Pro/Premium не закладывать |
| Проверки UI | Playwright Apache-2.0; Vitest MIT | Сквозные пользовательские сценарии и проверка API/UI ошибок |
| Входная точка | nginx BSD-2-Clause | Один origin для статики и /api; HTTPS в LAN-профиле |
| Логи/качество кода | structlog MIT, Ruff MIT | JSON logs, lint; метрики через защищённое API |

Это план выбора компонентов, не результат проверки совместимости конкретных релизов. До первой сборки создать `THIRD_PARTY.md`: компонент, версия/commit/digest, официальный URL исходников, SPDX/license-файл, зависимости и ограничения. Проверять лицензии кода, образов и весов отдельно. Qwen3-8B — Apache-2.0 для исходных весов; проверить происхождение конкретного Q4 artifact. Базовые Windows и NVIDIA driver остаются проприетарными зависимостями имеющегося ПК. Cursor — инструмент разработки с собственными условиями и расходами, не бесплатная OSS-часть runtime RAG.

Valkey выбран вместо неопределённого `Redis 7`: условия лицензии Redis зависят от версии. URI Celery остаётся `redis://valkey:6379/0`, extra пакета — `celery[redis]`; протокол и имя библиотеки не означают запуск сервера Redis. До принятия связки проверить ack/redelivery, visibility timeout, broker restart, лимиты/noeviction и восстановление outbox. При несовместимости зафиксировать ADR и выбрать проверенный OSS broker, не менять его молча.

Haystack Apache-2.0 допустим как последующая библиотека pipeline через адаптер, но не обязательный сервис MVP: вначале достаточно явных функций retrieval/generation. LangGraph и графовая БД — только после измеримой потребности. Готовые all-in-one RAG-приложения не становятся параллельным хранилищем пользователей/документов: это создало бы два независимых ACL и индекса.

## Собственный веб-интерфейс и расширения

Свой UI нужен для единой работы с документами, разметкой, согласованием и будущей аналитикой. Готовый чат и административная панель Qdrant этого целиком не покрывают. Используем открытые библиотеки; ядра сторонних продуктов не форкаем ради отдельных кнопок.

| Экран | MVP | Последующие функции |
| --- | --- | --- |
| Вход | Login/logout, истечение сессии, понятные ошибки | Пользователи, группы, роли |
| Документы | Список/поиск/пагинация, upload, статус job, версия, удалить | Массовые операции, сравнение редакций |
| Источник | Текст чанка, заголовок, реальная страница/раздел, защищённый оригинал | Просмотр PDF с выделением evidence |
| Поиск и чат | Фильтры, найденные фрагменты, ответ/partial/not_found, цитаты, очередь | История диалогов и расширенная обратная связь |
| Проверка метаданных | Карточка, корректировка, происхождение, история | Очередь разметки, переход в Label Studio |
| Качество | Статусы обработки и доступные ошибки | Наборы вопросов, runs, сравнение метрик и ошибок |
| Новые знания | После MVP | Предложения, источники, review/approve/reject, история |
| Аналитика | Минимум: доступные документы/jobs, очередь, ошибки | Темы/сроки/сравнения/предметные отчёты, экспорт |
| Состояние системы | Admin: зависимости, диск, очередь, safe errors | Ресурсы/latency/эксплуатационные графики |
| Dashboard / Storage / Models | Admin console + Model Hub (tag/GGUF/URL) + launcher 1A | Offline pack, deeper charts |

Статика и шрифты локальные; CDN и внешняя аналитика не используются. UI не обращается прямо к Ollama/Qdrant/PostgreSQL. Все чтения, экспорты, агрегаты и действия проходят через `/api` с общей авторизацией. Меню скрывает недоступные действия для удобства, но решение всегда принимает backend. Агрегаты пользователя считаются только по доступным документам; администраторские метрики отделены. Очистка Markdown/HTML, запрет произвольных URL/скриптов из ответов и документов, CSRF для cookie-сессий обязательны.

Расширения сначала являются обычными доверенными модулями репозитория: `modules/<name>` на backend и `apps/web/src/features/<name>` на frontend. Контракт модуля: id, версия, permissions, API prefix, config schema, миграции, фоновые задачи, audit events, UI route/navigation и тесты. Регистрация — при сборке/старте по allowlist; произвольная установка кода из браузера не нужна. Feature flags выключают UI и endpoints согласованно. Модули используют общий DocumentService/AuthorizationService/RetrievalService, а не читают базы в обход проверок.

Первая аналитика — детерминированные подсчёты по метаданным и статусам. Семантические сводки позже маркируются как выводы модели и содержат источники. SQL-инструмент использует разрешённые read-only представления, параметры, timeout/row limit; LLM не исполняет произвольный SQL. Каждая новая кнопка должна иметь действие API, права, обработку ошибок и критерий приёмки.

## Порядок реализации и контрольные результаты

Функциональные блоки F01–F10 описывают состав системы; этапы S0–S7 определяют зависимый порядок разработки. Не пытаться завершить целиком один блок до первого сквозного запуска. Оценка и безопасность начинаются до настройки поиска.

| Этап | Подзадачи и порядок | Проверяемый результат / переход |
| --- | --- | --- |
| S0. Инвентаризация и контракты | F01.1; схемы F02/F03/F04; 10–20 тестовых вопросов; license manifest; ADR по UI/Engine/broker | Hardware report, пути HDD, unknowns, API/data schema, backlog с F-ID. Ничего тяжёлого до проверки диска |
| S1. Среда | F01.2–F01.5; Engine/Toolkit; PostgreSQL/Valkey/Qdrant/Ollama; API/web shell; F11/F12 skeleton | GPU smoke, лимиты RAM, данные на HDD, startup/stop без потери volumes |
| S1b. Admin + Models | F11 Admin Console; F13 Model Hub (tag/GGUF/URL); F12 Launcher Win+Linux | Админ UI, смена LLM, один exe start/stop |
| S2. Вертикальный срез | F02.1–4; F03.1–5; F04.1–7 минимум; F05.1–2; F06.1–5; тесты F07/F09 | Login → 3–5 документов → job → dense search → ответ с источником; изоляция и неполная публикация проверены |
| S3. Надёжный MVP | F03.6, все обязательные контракты; web каталог/карточка; F09.1–3 | Restart/retry/delete/ACL revoke/offline; backup/restore на маленьком корпусе до массового импорта |
| S4. Пилот и разметка | F04.8–13, F07; 100–500 документов, 100–200 вопросов, calibration/held-out | Проверенные labels и provenance, измеренные retrieval/answer quality, HDD/RAM/VRAM/latency; дефекты baseline исправлены |
| S5. Улучшение поиска и LAN | По одной функции: F05.3–5, OCR/форматы F03.7, F04.14–17, F09.4–5 | Сравнение с baseline по тем же правилам, без утечек/регрессий; LAN только после auth/offline/restore. Локальный режим допустимо сохранить |
| S6. Знания и аналитика | F08; F10.1–3; UI review, protected dashboards и module registry | Новое знание вне общего поиска до решения; аналитика ACL-aware; один пример расширения с API/UI/tests |
| S7. Масштабирование по данным | F09.6; P2 F04/F10 | Поэтапно 1/5/10 тыс. документов, затем отдельное решение о 100 тыс./новом оборудовании; отчёт вместо обещаний |

Этап закрывается кодом, проверками и доказательствами. Временная заглушка разрешена в unit tests, но не доказывает E2E. Не переносить разметку схемы и тестовый набор на конец; сложные методы подключать только после baseline. Ragas/Label Studio запускать отдельным профилем с приостановкой тяжёлого ingestion и пересчётом RAM. Сборки web/образов выполнять вне нагрузочного теста. Сроки определить после S1/S2; календарные обещания до определения CPU и состава документов не давать.

## Работа агентов разработки в Cursor

Агенты Cursor создают и проверяют код, скрипты, миграции и документацию. Это не runtime-агенты RAG; установка Cursor на сервер и платная модель/API для работы готовой RAG не требуются. Разработка в Cursor может использовать облачные модели: не передавать реальные документы и секреты без отдельного разрешения, использовать синтетические фикстуры. Работа локальной RAG остаётся offline после подготовки.

| Роль | Ответственность | Передача результата |
| --- | --- | --- |
| Ведущий / интегратор | ADR, backlog, порядок S0–S7, согласование API/схем, интеграция | Задача с F-ID, зависимостями, файлами и приёмкой; итоговая сборка |
| Infrastructure | PowerShell/WSL bootstrap, Engine/Toolkit, Compose, GPU, offline/backup/restore | Воспроизводимые scripts, hardware/placement report, реальные логи |
| Backend / security | Миграции, auth/ACL, API, jobs/outbox, публикация/удаление | OpenAPI, migrations, negative/integration tests |
| RAG / data | Docling, chunking, embedding/search, metadata, citations, оценка | Проверенный pipeline, фикстуры, benchmark и revisions |
| Frontend | Экраны React, typed API client, модули/дашборды | UI с реальным API, browser E2E, отсутствие внешних CDN |
| QA / reviewer | Независимая проверка приёмки, failure/ACL/offline/нагрузочные тесты | Findings с воспроизведением; отчёт passed/failed/not_run/blocked |

Роли можно выполнять последовательно одним агентом. Если Cursor поддерживает параллельные агенты, разделять независимые задачи после фиксации контрактов; каждый работает в своей ветке/worktree. Общие миграции, Compose, lockfiles и API-контракт меняет назначенный владелец; интегратор разрешает конфликты и выполняет итоговые проверки. Не запускать параллельные GPU-тесты и несколько экземпляров всего стека на этом ПК.

Карточка задачи: `task_id`, `F-ID`, этап, цель, зависимости, allowed files/branch, входные контракты, критерии, команды проверки, риски и rollback. Статусы: planned → ready → in_progress → review → verified; blocked имеет конкретную причину. Передача: commit/diff, изменённые контракты, миграции, команды и exit codes, пути артефактов, непроверенные пункты и следующая задача. При изменении контракта интегратор обновляет оба документа/ADR, dependent tasks и tests.

Каждая итерация: прочитать оба файла и repository state → выбрать следующую готовую задачу → реализовать небольшой сквозной результат → выполнить соответствующие проверки → исправить выявленные дефекты → интегрировать → обновить backlog и отчёт. Не генерировать весь проект одной бесконтрольной операцией. Самопроверка автора не заменяет review критичных ACL/публикации/удаления.

Если агент не имеет доступа к Windows/GPU, он готовит код и точные команды, запускает доступные CPU/API tests и отмечает GPU/WSL/LAN как `not_run` или `blocked`. Отчёт не должен выдавать контейнерный mock за реальный Ollama/GPU тест. Установку/перенос WSL и restart выполнять по фактическому состоянию; не удалять данные/volumes ради успешного теста.

## Дополнительные контракты v1.4 — Admin / Launcher / Model Hub

Согласовано с roadmap F11–F13 и ADR 0002 (2026-11-03):

1. **Launcher 1A** в `apps/launcher`: один бинарник Win+Linux; preflight Docker/GPU/disk/port; `compose up/down`; открыть UI на `127.0.0.1:8080`. Не монолит без Docker.
2. **Admin Console:** Dashboard/System/Storage/Models; `/api/admin/system`, `/api/admin/storage`, `/api/admin/models`; только admin.
3. **Model Hub:** активная модель; pull tag; upload GGUF → `ollama create`; URL import job; switch + smoke; сохранить parallel=1 и ctx 4096.
4. **Portability:** portable bundle + `RAG_DATA_ROOT`; платформы v1 Windows+Linux NVIDIA.

## Дополнительные контракты v1.5 — F14 Corpus Quality Loop

1. **Offline only:** quality/synth/eval/chat без интернета и без Cursor; `OLLAMA_NO_CLOUD=1`; UI без CDN.
2. **Quality API:** `/api/admin/quality/level|runs|eval-set/*`; containment score; уровни low/ok/good/strong; status ok|stale|running|failed.
3. **Дозагрузка:** corpus fingerprint; после новых docs → stale + auto-run на old∪new; eval set расширяется, не затирается.
4. **Documents:** `GET /api/documents?q=` поиск + corpus counts в Storage/Documents.

## Дополнительные контракты реализации v1.3

1. Создай `AGENTS.md`, `.cursor/rules/` (формат проверяй по установленному Cursor), `docs/adr/`, `docs/backlog.md`, `docs/progress.md`, `THIRD_PARTY.md`, `models.lock.json`, каталог `artifacts/`. Это задачи на будущую реализацию, не уже существующие файлы. Не полагайся на недоступные функции конкретного тарифа Cursor.
2. Backend: `modules/documents`, `annotations`, `evaluation`, `knowledge`, `analytics`; frontend: `apps/web/src/features/`. Не создавать пустые экраны под видом завершённых функций. Сначала S2/S3; последующие модули включать по feature flags.
3. Дополнительные модели БД по этапам: annotations/annotation_reviews; evaluation_datasets/questions/qrels/runs; knowledge_proposals/reviews; audit_events. Каждая связана с tenant, author, target version и schema revision. Общая permissions policy обязательна.
4. REST: GET `/api/documents` (pagination/filters), GET/PATCH `/api/documents/{id}/metadata` (expected revision), GET `/api/annotations`, POST `/api/annotations/import`, GET `/api/annotations/export`, POST `/api/evaluations/runs`, GET `/api/evaluations/runs/{id}`, POST `/api/knowledge/proposals`, POST `/api/knowledge/proposals/{id}/reviews`, GET `/api/analytics/summary`, GET `/api/admin/system`. Новые маршруты реализуются на соответствующем этапе, с permission checks, OpenAPI и тестами; список не требует немедленно реализовать все P1.
5. Импорт Label Studio: проверять схему, устойчивые IDs, версию и ACL; не принимать импортированного reviewer/approved как доверенную идентичность. Использовать service account с ограниченной областью или локальный ручной экспорт/импорт. До решения о совместимой авторизации не открывать Label Studio в LAN. История и решения сохраняются нашей системой.
6. Разметка: предложенные метки не меняют ACL; correction extracted text создаёт новую extraction/index revision и контролируемую перепубликацию. Эталоны привязаны к исходной версии, stale annotations помечаются. Calibration и held-out не смешивать; synthetic не считать автоматически эталоном.
7. Analytics API применяет общий authorization scope ДО count/group/export; тесты проверяют отсутствие утечки числом, названиями категорий и экспортом. SQL только параметризованный по allowlist views; лимиты/timeout/audit.
8. Новые знания: proposed → in_review → approved/rejected. Экспертное право выдаёт admin; member не становится reviewer по полю запроса. Одобрение создаёт производный материал с provenance, отдельным publication job и проверкой ACL; изменение исходных версий помечает результат stale. Не расширять доступ автоматически при объединении источников.
9. Предусмотри `scripts/preflight.ps1`, `scripts/bootstrap-wsl.sh`, `scripts/smoke.sh`, `scripts/offline-check.sh`, `scripts/backup.sh`, `scripts/restore.sh`, `scripts/benchmark.py`. Скрипты идемпотентны, не содержат выдуманной буквы диска/подсети и не удаляют volumes. Привилегированные действия отделить от read-only preflight; секреты вводить локально и не печатать.
10. Compose profiles: core, annotation, evaluation; LAN override отдельно. В рабочем core статика React через nginx, backend и внутренние сервисы. Предусмотреть дополнительную RAM nginx и служебных процессов внутри WSL; Node builder выключен. Annotation/evaluation не запускать поверх полного бюджета без перерасчёта.
11. Lockfiles Python/JS, образы без `latest`, модели с digest/revision и cache manifest. Для CPU PyTorch явно выбрать CPU package index. Не загружать автоматически GPU torch через зависимость Docling/embeddings; проверять итоговый образ и device. Tool/model licenses сверять для точных артефактов.
12. CPU проверки: Ruff, pytest unit/API, TypeScript check, web build, Vitest. Integration: настоящий PostgreSQL/Valkey/Qdrant, миграции и idempotency. E2E Playwright: login/upload/job/question/source, 403/404, отзыв ACL, ошибки/очередь. GPU: отдельный real Ollama smoke. Reliability: worker kill, duplicate delivery, broker loss, stale lease, interrupted index, delete race. Последовательность: быстрые проверки → integration → UI E2E → GPU → recovery/offline → pilot/load.
13. Вести `artifacts/<run-id>/` с hardware/config revisions, commands+exit codes, результатами и JSON/Markdown отчётом. Секреты/сырой корпус не включать. Статусы passed/failed/not_run/blocked; провал теста сначала исправить. Не менять смысл теста только ради зелёного результата.
14. При завершении этапа: реализованный scope с F-ID, фактические проверки, ограничения, rollback и следующая ready task. На реальном ПК проверить shutdown/start Windows/WSL, доступность Engine после рестарта и сохранность volumes; документация не равна успешному развёртыванию.

## Реализовать

1. Дерево репозитория: `apps/api`, `apps/web`, `apps/worker`, `apps/embeddings`, `modules`, `rag/ingestion`, `rag/chunking`, `rag/retrieval`, `rag/generation`, `rag/metadata`, `tests`, `configs`, `scripts`.
2. `docker-compose.yml`, `.env.example`, `.gitignore`, Dockerfile(ы), `pyproject.toml` или requirements (с зафиксированными совместимыми версиями), Makefile, README.
3. PostgreSQL + Alembic: tenants, users, tenant_memberships (admin/member), access_groups, group_memberships, documents, document_access_groups, document_versions, chunks, ingestion_jobs, outbox_events, query_audit. Поля и ограничения — по схеме roadmap. Tenant-safe связи обязательны. Документ имеет active_version_id и deleted_at; версия — SHA256, storage_key, provenance и revisions; job — lease/heartbeat/attempts; chunk — version_id/ordinal. Не вводи глобальный UNIQUE(sha256).
4. Надёжная загрузка файлов: типы PDF, DOCX, TXT на MVP. Ограничение размера/MIME, безопасные пути, SHA256, дедупликация. Сохранять оригинал; в одной транзакции PostgreSQL создавать документ/версию, job и outbox event; dispatcher доставляет в Celery через Valkey. Возвращать document_id, version_id, job_id. Доставка как минимум один раз; worker идемпотентен. Дедупликация ограничена tenant/owner/нормализованным ACL/domain-контекстом и не раскрывает чужих загрузок. Idempotency key scoped tenant/user/operation; гонки закрыть ограничениями/блокировкой БД.
5. Парсер Docling и структурный chunking. Сохранять заголовки, ссылки на страницы и разделы. Нельзя придумывать page numbers; если нет — null. Для сканов — настроенный OCR либо needs_ocr; пустое извлечение не переводить в ready. Ограничивать время/память парсинга и распаковку DOCX.
6. CPU embedding сервис с `BAAI/bge-m3` и батчами; Qdrant collection с 1024-dim cosine dense векторами, metadata payload и payload indexes. Фильтры tenant/owner/access_groups/domain применяются внутри Qdrant до поиска. До retrieval разрешать только активные опубликованные версии по PostgreSQL, затем повторно проверять права/статус перед выдачей. Для MVP допустим dense-only; этап 2 — dense + PostgreSQL FTS + RRF с ACL до обеих веток. ts_rank/ts_rank_cd не BM25. Настоящий BM25 требует отдельно выбранного проверенного механизма; не предполагать его наличие в Qdrant. Не называть dense-only гибридным.
7. `POST /api/documents`, `GET /api/documents/{id}`, `GET /api/jobs/{id}`, `POST /api/chat`, `GET /api/health` и OpenAPI. Вопрос => ACL/domain/active-version filter => retrieval => упаковка в токеновый бюджет => локальный Ollama => ответ со статусом answer/partial/not_found и citations [{document_id, version_id, chunk_id, page, section}]. 6–8 фрагментов — верхний ориентир. Сервер строит citations из сохранённых метаданных только для labels, реально переданных в контексте; проверяет доступ, версию и provenance. При недостатке подтверждений — «не найдено в документах». Правильный chunk_id сам по себе не доказывает утверждение: поддержку утверждений оценивать отдельно.
8. С первого этапа вход администратора с безопасным password hash, без стандартного пароля, серверная сессия/проверенный токен и отзыв при logout. Для cookie — HttpOnly/SameSite, CSRF для изменений, Secure при HTTPS; ограничить перебор login. Полноценные серверные tenant/owner/group-проверки обязательны уже в MVP; расширенный UI управления ролями позже. Не маркировать MVP как production-ready до реализации корректной авторизации и тестов изоляции.
9. Управление давлением нагрузки: одна генерация одновременно, максимум 4 ожидающих запроса приложения, таймауты, 429/503 с понятным сообщением, отдельная очередь ingestion. `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_CONTEXT_LENGTH=4096`; API явно передаёт `num_ctx=4096`. Внутренняя очередь Ollama также ограничена. Две генерации — только эксперимент после измерений. Общий лимит на все API processes/replicas, не semaphore одного процесса; избегай одновременного запуска GPU embeddings, иначе VRAM-конфликт.
10. Тесты: парсинг, chunking, dedupe, ACL negative tests, отсутствие выдуманных цитат, интеграционный smoke test, API contract. Сгенерировать фикстуры без конфиденциальных данных.
11. Добавить scripts предварительной загрузки Ollama, embeddings/tokenizers, всех нужных моделей Docling/OCR, создания Qdrant, миграций и smoke test. Зафиксировать совместимые версии пакетов/образов, точные ревизии моделей, квантование, tokenizer и checksum manifest. Проверить совместимость Python 3.12, не выдавать её за установленный факт. Для Ollama задать OLLAMA_NO_CLOUD=1, для остальных компонентов — поддерживаемый offline-режим. Проверить upload и chat при блокировке внешней сети и работающей внутренней сети сервисов. Дать пошаговые команды установки и запуска, в том числе `docker compose config`, `docker compose up -d --build`, `docker compose exec ollama ollama pull qwen3:8b`, `curl ...`.
12. Добавить документацию по пакетной индексации 100k файлов, retry/dead-letter queue, snapshot и восстановлению; масштабирование проверить экспериментально, НЕ обещать 10 быстрых параллельных LLM-ответов с 16GB VRAM.

## Обязательные контракты реализации

Эти требования входят в MVP, а не откладываются до этапов 2–4. Подробные поля, состояния и инварианты описаны в разделе «Обязательные контракты MVP» roadmap; реализовать оба документа согласованно.

### Доступ и tenant

Чтение разрешено при активном членстве в tenant И (admin этого tenant ИЛИ owner ИЛИ пересечение групп). Пустые группы — owner/admin only. Админ tenant не видит другие tenant. Tenant/owner/roles/groups брать из проверенной сессии и БД; не доверять данным клиента. Domain только сужает поиск. В MVP загружает, изменяет ACL/версии и удаляет администратор; расширенные права записи позже.

Одинаково защищать документы, jobs, чанки, оригиналы и citations. Чужой ID не раскрывает существование объекта. Повторная проверка перед ответом обязательна для отзыва доступа во время запроса. Изменения ACL синхронизировать с Qdrant; до синхронизации предварительно ограничивать область по PostgreSQL, при ошибке закрывать доступ. Кэши включают tenant/user/ACL revision/filters/active version и инвалидируются при изменениях.

### Надёжность, версии и удаление

Worker выполняет parse → chunk → CPU embed → upsert → verify → publish, берёт lease/lock на версию и обновляет heartbeat. Dispatcher может доставить повтор; reconciler восстанавливает потерянные/зависшие задания. Retry/backoff ограничены, терминальные ошибки видны в API, ручной retry не создаёт новую логическую версию. PostgreSQL — источник истины, Valkey — транспорт, Qdrant — восстанавливаемый индекс.

Стабильный chunk/point ID из version_id, index_generation и ordinal; повтор той же генерации использует те же точки. Изменение parser/chunker/embedding создаёт контролируемую новую генерацию, не частично переписывает опубликованную. Состояния версии: pending, parsing, chunking, embedding, indexing, ready; needs_ocr/failed/cancelled/deleting/deleted. Публикация только после проверки непустого текста и всех ожидаемых чанков/векторов: транзакционно ready + active_version_id. Блокировка/compare-and-swap не позволяет старому заданию заменить более новую публикацию.

Обновление не скрывает старую рабочую версию до готовности новой; обычный retrieval видит только активную. До поиска передавать допустимые пары document/version по PostgreSQL либо иной проверенный ограничитель; постфильтрация не заменяет это требование. Для большого корпуса отдельно обосновать масштабируемую синхронизацию. effective_from/to — срок действия документа, не статус индексации.

Удаление сразу ставит tombstone и запрещает чтение, затем фоновый job удаляет все версии, Qdrant points, FTS при наличии, chunks, extracted files, originals и кэши. Блокировать/отменять ingestion, не допускать воскрешения поздним worker. Физическую очистку считать завершённой только после проверки всех хранилищ. Аудит и backups — по retention; восстановление повторно применяет актуальные tombstones. Общие blobs, если используются, требуют счётчика ссылок и изоляции информации о совпадениях.

### Бюджет, цитаты и отказ

instructions + question + history + sources + protocol overhead + answer reserve + safety margin ≤ 4096. Начальные answer reserve 768 и safety margin 256; остальное измерять tokenizer выбранной LLM и её chat template. Явно выбрать/проверить thinking mode и учесть его токены. Для embedding проверять tokenizer/max_length и отсутствие незаметного обрезания чанков. Сокращать историю и набор источников с сохранением provenance; слишком длинный вопрос отклонять понятной ошибкой.

Модель может ссылаться только на серверные labels переданных источников. Невалидную ссылку не возвращать: ограниченный retry генерации либо безопасный отказ. Не выдумывать страницы DOCX/TXT. Доказательность существенных утверждений и полнота цитирования оцениваются отдельно от валидности IDs. При частичном ответе явно обозначать границы найденного.

Отказ калибровать на отвечаемых/неотвечаемых вопросах; универсального similarity threshold не задавать. Фиксировать конфигурацию с model/retrieval revision и перекалибровать после изменений. Эталон: 200–500 характерных документов, 100–200 вопросов с источниками, раздельные calibration/held-out выборки. Recall@10 ≥ 0.85 и citation correctness ≥ 0.95 — предварительные ориентиры, не уже достигнутые метрики; определить знаменатели и процедуру оценки, измерять ложные ответы/отказы.

### Дополнительные маршруты и UI

Все маршруты под `/api`; основной набор из пункта 7 дополнить:

| Метод и маршрут | Назначение |
| --- | --- |
| POST `/api/auth/login`, POST `/api/auth/logout` | Вход и отзыв сессии |
| POST `/api/documents/{id}/versions` | Новая версия, вернуть version_id/job_id |
| PATCH `/api/documents/{id}/acl` | Изменить owner/groups с проверкой tenant и полномочий |
| DELETE `/api/documents/{id}` | Скрыть сразу, вернуть job_id фоновой очистки |
| POST `/api/jobs/{id}/retry` | Контролируемый повтор failed job |
| GET `/api/chunks/{id}` | Защищённый текст и provenance источника |
| GET `/api/documents/{id}/versions/{version_id}/original` | Защищённая загрузка оригинала |

Upload возвращает 202 при фоновой обработке; jobs API показывает stage/progress/attempts/safe error. Health — минимальный liveness, детальную readiness с зависимостями ограничить доступом. UI: login → upload → job → chat → открытие защищённого источника.

### Проверки и порядок работ

Сначала один полный сценарий: администратор входит, загружает PDF с текстом/DOCX/TXT, получает job, worker публикует полную версию, retrieval учитывает ACL/domain, Ollama отвечает с реальной проверенной цитатой. Затем пилот 100–500 документов. XLSX/CSV, PPTX, расширенный OCR, SQL, reranker и 100k — следующие этапы.

Обязательные тесты дополнительно к пункту 10:

- Два tenant и несколько users/groups: чужой tenant, owner/group/no-group, поддельные client tenant/groups, прямые IDs jobs/chunks/originals, отзыв прав и устаревший кэш. Проверить фильтры до каждой ветки retrieval.
- Повтор/concurrent upload, одинаковый SHA256 в разных областях и повтор Celery task.
- Сбой после commit до Valkey, повтор dispatcher, worker kill/restart, сбой batch upsert: восстановление либо понятная ошибка, без частичной версии в поиске.
- Ошибка обновления, гонка публикаций, удаление во время ingestion и очистка всех производных данных без воскрешения.
- Скан/пустое извлечение не ready; бюджет и переполнение вопроса; невалидные citations и реальные защищённые источники; поддержка утверждений отдельно.
- Отвечаемые/неотвечаемые/partial вопросы, prompt injection из документа, login/logout, API contract, global queue limit, end-to-end и offline smoke.

Ограничь CPU/RAM/concurrency Docling/OCR и embeddings, batch size и длину очередей. Зафиксируй результаты latency p50/p95, TTFT и VRAM на реальной GPU; не обещай 10 быстрых одновременных генераций. Backup/restore проверить на пилоте; перед массовой загрузкой согласовать RPO/RTO/retention. Snapshot одного Qdrant не является полной резервной копией PostgreSQL + originals + index.

## Безопасность

Не публикуй порты PostgreSQL, Valkey, Qdrant и Ollama наружу. Публикуй API только на 127.0.0.1, если не настроен защищённый reverse proxy. Не добавляй .env с секретами в git. Не исполняй инструкции из загруженных документов. Запрет произвольного SQL; только whitelisted SELECT и параметризованные запросы в поздней фазе. Данные строго разделены по tenant и access group. Не записывай содержимое конфиденциальных документов в обычные application logs.

## Definition of done

В конце покажи: созданные файлы, существенные архитектурные решения, точные команды запуска, результаты `docker compose config`, линтеров/тестов/healthcheck, работающий пример загрузки файла и вопроса с реальной цитатой. Если в окружении отсутствует NVIDIA GPU/Docker/интернет, явно зафиксируй непроверенные шаги — не заявляй об успешном запуске.

Сначала реализуй минимальный вертикальный срез end-to-end, затем расширяй. Не имитируй реализацию заглушками, не оставляй `TODO` в обязательном пути MVP.

## Изменения редакции 28.10.2026

Внесены все согласованные правки: имя roadmap и отсутствующие конфигурации, Celery, auth/tenant/ACL с MVP, безопасная область дедупликации, outbox и восстановление, полная публикация, версии и удаление, бюджет, допустимость и доказательность цитат, отказ, FTS/RRF, полный offline и проверяемые ресурсные ограничения. Эти файлы описывают последующую реализацию, не подтверждают готовую систему.


## История: изменения v1.1 — дисковая конфигурация

Уточнение пользователя: системный SSD небольшой, около 20–30 ГБ доступного остатка, HDD почти 2 ТБ. Все тяжёлые данные и виртуальные диски Docker/WSL планируются на HDD. Добавлены проверка реального свободного места, перенос cache/swap, ограничения HDD, дисковые метрики и запрет считать копию на том же диске независимой резервной копией. Исходные файлы общей ветки не изменялись.

## Изменения v1.3 — 29.10.2026

Синхронизировано с Roadmap v1.3: Engine/WSL2, открытые зависимости, React UI, разметка/evaluation/knowledge/analytics по этапам, роли агентов разработки, контракты расширений и отчётность тестов. Агенты разработки не становятся обязательными runtime-агентами RAG.

## Методы подготовки и измерения: практический профиль

- PDF/DOCX → Docling structured document → блоки с provenance; TXT → явная кодировка и абзацы. Сохранять исходный и извлечённый слои. OCR применять только к нужным страницам, сначала проверив русские/английские языковые пакеты, сложные таблицы и порядок чтения.
- Chunking по заголовкам/абзацам: target 300–450 токенов, overlap 40–60 лишь там, где полезно; таблицы делить по строкам с повтором заголовков и связи с table_id. Длинные разделы дробить, короткие объединять внутри раздела. Поиск получает текст вместе с заголовком; page/section не генерировать моделью.
- Метаданные: Pydantic schema → регулярные выражения/словари для реквизитов → валидация → экспертное исправление. LLM JSON pre-annotation после baseline; сохранять evidence, method, revision и статус. Непроверенную метку не использовать как обязательный фильтр без проверки потерь recall.
- Retrieval: dense-only baseline → отдельный эксперимент FTS+RRF → reranker, если выигрыш оправдывает CPU/RAM/latency. Менять по одному параметру; фиксировать k, tokenizer/chunker/model/index revision. RRF объединяет ранги, не сравнивает непосредственно несовместимые scores.
- Контрольный набор: реальные вопросы, точные номера, сканы/таблицы, несколько источников, отсутствующий ответ, устаревшая версия и закрытый документ. Релевантность фрагмента 0/1/2; разметчик указывает supporting evidence, а не только желаемый текст ответа. Разделять calibration и held-out, не допускать утечки перефразированных вопросов между ними.
- Recall@10: для каждого вопроса доля размеченных релевантных эталонных единиц (заранее выбрать chunk либо source section), найденных в top-10, затем macro average; вопросы без релевантного источника оценивать отдельно. При смене chunking перемаппировать evidence и qrels, не сравнивать устаревшие chunk IDs.
- Citation correctness: доля проверенных пар «существенное утверждение — приведённый источник», в которых источник поддерживает утверждение; citation coverage — доля существенных проверяемых утверждений с достаточным источником. Валидность IDs отдельная автоматическая метрика. Считать ложные ответы на unanswerable и ложные отказы на answerable, указывать размер выборки.
- Ragas/LLM judge: только локальные adapters, небольшой batch и общий GPU scheduler. Проверять schema output; Qwen3-8B может ошибаться как судья. Экспертный held-out остаётся основой приёмки; синтетические вопросы и оценки модели маркировать отдельно.

## Официальные источники архитектурного пересмотра — 29.10.2026

Официальные справочники для реализации; ключевые решения сверены с документацией. Совместимость готовой сборки не испытана. Лицензии точных закреплённых релизов сверить в THIRD_PARTY.md.

- WSL: https://learn.microsoft.com/windows/wsl/ ; лимиты: https://learn.microsoft.com/windows/wsl/wsl-config
- Docker Engine: https://docs.docker.com/engine/install/ubuntu/ ; исходники: https://github.com/moby/moby
- NVIDIA WSL: https://docs.nvidia.com/cuda/wsl-user-guide/index.html
- NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- Valkey: https://github.com/valkey-io/valkey ; Celery transport: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html
- FastAPI: https://github.com/fastapi/fastapi ; React: https://github.com/react/react ; Vite: https://github.com/vitejs/vite
- Docling: https://github.com/docling-project/docling ; Label Studio: https://github.com/HumanSignal/label-studio
- Qdrant: https://github.com/qdrant/qdrant ; Ragas: https://github.com/vibrantlabsai/ragas ; Haystack: https://github.com/deepset-ai/haystack
- Ollama: https://github.com/ollama/ollama ; Qwen3: https://huggingface.co/Qwen/Qwen3-8B ; BGE-M3: https://huggingface.co/BAAI/bge-m3

