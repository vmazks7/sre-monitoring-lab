# Runbook: Database Unavailable / High Connections

**Алерты:** `DBHostDown` (Prometheus target недоступен) / `PostgresConnectionsHigh` (>80 активных соединений)
**Приоритет по умолчанию:** P1 для `DBHostDown` — это блокирует всю систему. P2 для `PostgresConnectionsHigh`, пока не перешло в отказ.

## Симптомы

- `up{job="dbhost_node"} == 0` — Prometheus не может достучаться до node_exporter на DBHost
- Каскадный рост error rate и latency на AppServer (settlement не может писать/читать)
- `pg_stat_database_numbackends` близко к лимиту `max_connections` (по умолчанию в PostgreSQL — 100)

## Диагностика

### 1. Это сеть или сама ВМ/сервис?

```bash
ping -c 5 10.10.10.12
```

Если пинг не идёт вообще — проблема на уровне ВМ или сети (VMware/host-only
адаптер), не самого PostgreSQL. Проверь, не активна ли деградация через
Toxiproxy на прокси `postgres`:
```bash
curl http://localhost:8474/proxies/postgres
```
Если `"enabled": false` или есть toxics с `type: "timeout"` — это она.

Если пинг идёт, но конкретные порты недоступны:
```bash
nc -zv 10.10.10.12 5432    # PostgreSQL
nc -zv 10.10.10.12 9100    # node_exporter
nc -zv 10.10.10.12 9187    # postgres_exporter
```

### 2. Сам PostgreSQL жив на DBHost?

```bash
sudo systemctl status postgresql
sudo journalctl -u postgresql -n 50 --no-pager
```

### 3. Если это `PostgresConnectionsHigh` — откуда идут соединения?

```sql
SELECT client_addr, count(*), state
FROM pg_stat_activity
GROUP BY client_addr, state
ORDER BY count(*) DESC;
```

Частая причина в этой лабе — settlement-поток или generation_loop не
закрывают соединения корректно (утечка коннектов), либо реально высокая
нагрузка при агрессивном тестировании (частые "Всплеск нагрузки" через
меню подряд).

## Восстановление

**Если это Toxiproxy-деградация:**
```bash
traffic_gen.py → 3 → 12. Снять ВСЕ деградации
```
или напрямую:
```bash
curl -X DELETE http://localhost:8474/proxies/postgres/toxics/lag
curl -X POST http://localhost:8474/proxies/postgres -d '{"enabled": true}'
```

**Если PostgreSQL упал:**
```bash
sudo systemctl restart postgresql
```
Проверь логи сразу после — часто причина в диске (см. опыт с Elasticsearch
в этом же проекте: `df -h` на DBHost, если место кончилось — WAL-файлы
PostgreSQL могут расти быстро при интенсивной записи транзакций).

**Если слишком много соединений:**
Убей зависшие/простаивающие соединения:
```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle' AND now() - state_change > interval '10 minutes';
```
На постоянной основе — рассмотри добавление pgbouncer перед PostgreSQL
(вне рамок текущей лабы, но реалистичный следующий шаг для боевой системы).

## Проверка после восстановления

```bash
curl http://10.10.10.13:9090/targets   # dbhost_node и postgres должны стать UP
```
```promql
pg_stat_database_numbackends{datname="shop"}   # должно вернуться к норме
```

## Постмортем — шаблон

```markdown
## Инцидент: DB Unavailable — <дата>

**Длительность:** с XX:XX до XX:XX (UTC)
**Причина:** [сеть/Toxiproxy/сбой PostgreSQL/исчерпание соединений/диск]
**Влияние на систему:** [полная остановка / частичная деградация]
**Действия:** [что сделано для восстановления]
**Что можно улучшить:** [например: connection pooling, алерт на предиктивный рост соединений]
```
