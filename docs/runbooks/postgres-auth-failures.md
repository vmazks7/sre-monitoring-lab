# Runbook: PostgreSQL Auth Failures & Permission Errors

**Когда открывать:** `psycopg2.OperationalError: password authentication
failed`, `psycopg2.errors.InsufficientPrivilege`, или
`pg_up 0` у `postgres_exporter`.
**Приоритет:** P1, если это блокирует сам генератор трафика (не может
подключиться вообще); P3, если это только `postgres_exporter` (метрика
пропадает, но приложение работает).

## Диагностика — три разных класса ошибок

### Класс 1: "password authentication failed"

Значит соединение до PostgreSQL реально доходит (сеть/порт открыты), но
пароль не совпадает. Проверь вручную, в обход приложения, с тем же DSN,
что использует сервис:
```bash
psql -h 10.10.10.12 -U appuser -d shop
# или для exporter'а:
psql "postgresql://exporter:<пароль>@localhost:5432/shop?sslmode=disable" -c '\conninfo'
```
Если тоже падает — пароль реально не тот. Перезадай явно, не гадай:
```bash
sudo -u postgres psql -c "ALTER USER appuser WITH PASSWORD '<новый>';"
```
и обнови конфиг сервиса, который его использует
(`config.py` → `DB_PASSWORD`, или `DATA_SOURCE_NAME` в
`postgres_exporter.service`) — **синхронно**, иначе через минуту та же
ошибка вернётся.

### Класс 2: "permission denied for table X"

Соединение и пароль в порядке, но у пользователя нет прав на конкретный
объект. В этом проекте типичная причина — таблицы создавались от имени
`postgres` (через `sudo -u postgres psql -f sql_db_setup.sql`), а
`CREATE DATABASE ... OWNER appuser` даёт права только на саму базу, не
на объекты внутри, созданные другим пользователем:
```bash
sudo -u postgres psql -d shop -c "\dp users"   # покажет реальные gRANT'ы на таблицу
```
Решение:
```bash
sudo -u postgres psql -d shop -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO appuser;"
sudo -u postgres psql -d shop -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO appuser;"
```

### Класс 3: `pg_up 0` у postgres_exporter (не даёт явной ошибки авторизации)

```bash
curl -s localhost:9187/metrics | grep "pg_up\|pg_exporter_last_scrape_error"
```
`pg_up 0` + `pg_exporter_last_scrape_error 1` — exporter жив (HTTP
отвечает), но подключиться к PostgreSQL не может. Причины — то же самое,
что в Классе 1, только диагностировать нужно не через прямую ошибку
Python, а вручную теми же командами (см. выше), плюс отдельно проверить
`pg_hba.conf`:
```bash
sudo grep "127.0.0.1\|10.10.10" /etc/postgresql/18/main/pg_hba.conf
```
`postgres_exporter` подключается через `localhost`, ему нужна отдельная
строка именно для `127.0.0.1/32` — строка только для подсети
`10.10.10.0/24` (для внешних подключений с AppServer) её не покрывает:
```bash
echo "host all all 127.0.0.1/32 md5" | sudo tee -a /etc/postgresql/18/main/pg_hba.conf
sudo systemctl restart postgresql
sudo systemctl restart postgres_exporter
```

## Полезные диагностические запросы (держать под рукой)

Кто вообще сейчас подключён и с каким правами:
```sql
SELECT usename, client_addr, state, count(*)
FROM pg_stat_activity
GROUP BY usename, client_addr, state
ORDER BY count(*) DESC;
```

Права конкретного пользователя на конкретную таблицу:
```sql
SELECT grantee, table_name, privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'appuser'
ORDER BY table_name;
```

Проверка версии и пути конфигов (полезно, если инструкция писалась под
другую версию PostgreSQL):
```bash
psql --version
sudo -u postgres psql -c "SHOW config_file;"
sudo -u postgres psql -c "SHOW hba_file;"
```

## Проверка после восстановления

```bash
psql -h 10.10.10.12 -U appuser -d shop -c 'SELECT count(*) FROM transactions;'
curl -s localhost:9187/metrics | grep "^pg_up"
# pg_up 1
```

## Постмортем — шаблон

```markdown
## Инцидент: PostgreSQL Auth/Permission — <дата>

**Класс ошибки (пароль / права / pg_hba):** [указать]
**Затронутый компонент:** [traffic_gen.py / postgres_exporter / Zabbix agent]
**Действия:** [что сделано]
**Что можно улучшить:** [например: единый .pgpass вместо паролей в разных конфигах]
```
