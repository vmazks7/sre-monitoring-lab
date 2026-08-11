# Runbook: Service CrashLoop (systemd)

**Когда открывать:** любой из кастомных сервисов лабы
(`providers-mock@*`, `toxiproxy`, `postgres_exporter`, `node_exporter`)
не поднимается, либо `systemctl status` показывает `failed`/`activating
(auto-restart)` по кругу.
**Приоритет:** зависит от того, какой сервис — `providers-mock`/`toxiproxy`
на AppServer блокируют весь генератор трафика (P1 для лабы), `node_exporter`
влияет только на один дашборд (P3).

## Первый шаг — читать код выхода

```bash
systemctl status <service>
```

Обрати внимание на строку `Main PID: ... (code=exited, status=N/REASON)` —
`REASON` почти всегда прямо называет причину, не нужно лезть в логи:

| status | Что значит | Куда идти |
|---|---|---|
| `217/USER` | Пользователь из `User=` в юните не существует | раздел "Отсутствующий пользователь" |
| `200/CHDIR` | Нет прав зайти в `WorkingDirectory` | раздел "Права на директорию" |
| `1/FAILURE` | Общая ошибка — нужно смотреть логи процесса | раздел "Смотрим логи процесса" |
| `127/USER` (реже) | Не найден исполняемый файл (`ExecStart`) | проверь путь бинарника |

## Диагностика

### Отсутствующий пользователь (217/USER)

```bash
grep "^User=" /etc/systemd/system/<service>.service
id <тот_пользователь>
```
Если "no such user":
```bash
sudo useradd --no-create-home --shell /bin/false <пользователь>
```
Дальше обязательно сброс счётчика рестартов — без этого systemd после
5 неудачных попыток подряд включает rate-limit
(`Start request repeated too quickly`) и не запустит сервис заново, даже
если причина уже устранена:
```bash
sudo systemctl reset-failed <service>
sudo systemctl restart <service>
```

### Права на директорию (200/CHDIR)

```bash
grep "^WorkingDirectory=" /etc/systemd/system/<service>.service
ls -la <эта_директория>
```
Владелец и права должны позволять пользователю из `User=` заходить
внутрь (execute-бит на все родительские директории тоже, не только на
целевую):
```bash
sudo chown -R <юзер>:<группа> <директория>
sudo chmod -R u+rwX,go+rX <директория>
# и на родительскую, если она тоже приватная:
sudo chmod o+rX <родительская_директория>
```

### Смотрим логи процесса (1/FAILURE и остальные)

```bash
sudo journalctl -u <service> -n 50 --no-pager
```

Частые находки в этом проекте:
- `ModuleNotFoundError: No module named 'fastapi'` — зависимости стояли
  в venv, а systemd-юнит запускает системный `/usr/bin/python3` напрямую.
  Решение — либо ставить зависимости глобально
  (`sudo pip install -r requirements.txt --break-system-packages`),
  либо явно указать путь до `venv/bin/python3` в `ExecStart`.
- `externally-managed-environment` при голом `pip install` без флага —
  PEP 668 на современных Ubuntu, обязателен `--break-system-packages`
  или отдельный venv.
- `Connection refused` при старте, если сервис на старте пытается сразу
  подключиться к зависимости (БД, другой прокси) — это уже не проблема
  самого сервиса, идти в `postgres-auth-failures.md` или проверять,
  жив ли Toxiproxy.

## Восстановление — общий чек-лист

```bash
sudo systemctl daemon-reload      # если менял .service файл
sudo systemctl reset-failed <service>
sudo systemctl restart <service>
systemctl status <service>
sudo journalctl -u <service> -n 20 --no-pager   # свежий лог после рестарта
```

## Специфика для providers-mock и toxiproxy в этой лабе

Это шаблонный юнит (`providers-mock@.service`) — три инстанса разом:
```bash
sudo systemctl reset-failed providers-mock@9101 providers-mock@9102 providers-mock@9103
sudo systemctl restart providers-mock@9101 providers-mock@9102 providers-mock@9103
```

**Отдельный, специфичный для Toxiproxy момент**: даже если сам сервис
поднялся успешно (`active: running`), это не значит, что прокси внутри
него настроены — конфигурация Toxiproxy живёт только в памяти процесса
и **обнуляется при каждом рестарте**:
```bash
curl -s http://localhost:8474/proxies   # {} — пусто, значит нужно пересоздать
bash appserver/toxiproxy/toxiproxy_setup.sh
```

## Постмортем — шаблон

```markdown
## Инцидент: CrashLoop <service> — <дата>

**Сервис:** [providers-mock@9101 / toxiproxy / postgres_exporter / ...]
**Код выхода:** [217/USER, 200/CHDIR, 1/FAILURE, ...]
**Причина:** [описание]
**Действия:** [что сделано]
**Что можно улучшить:** [например: добавить проверку зависимостей в CI перед деплоем юнита]
```
