# Runbook: Pending Transactions Stuck

**Алерт:** `PendingTransactionsStuck` — более 30 транзакций в pending дольше 5 минут
**Приоритет по умолчанию:** P2. P1, если число растёт без остановки (settlement-поток фактически не работает).

## Симптомы

- `app_pending_transactions` растёт и не снижается
- Пользователи (в реальной системе) видят "платёж обрабатывается" неопределённо долго
- Возможен рост `settle_latency_sec` для транзакций, которые всё же резолвятся

## Диагностика

### 1. Settlement-поток вообще жив?

```bash
tmux attach -t traffic-gen
```
Смотри, бегут ли в терминале строки `event: "transaction_settled"`. Если
только `transaction_created`, а `transaction_settled` не появляются
вообще — settlement-поток завис или упал.

```bash
ps aux | grep traffic_gen
```

### 2. Это учебный сценарий "зависшие pending"?

Легко забыть, что включал через меню:
```
traffic_gen.py → 3 → 4. Статус
```
Смотри строку "Зависшие pending: провайдер X" — если там не "нет", это
сознательно включённый сценарий (`state.stuck_provider`), settlement-поток
намеренно пропускает транзакции этого провайдера.

### 3. Проблема на уровне БД

```sql
SELECT count(*), min(created_at), max(created_at)
FROM transactions
WHERE status = 'pending';

SELECT pid, now()-query_start AS duration, state, query
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC LIMIT 10;
```

Если settlement-поток пытается писать в БД, но она недоступна/залочена —
транзакции физически не могут смениться со статуса `pending`.

### 4. Проблема на уровне вызова провайдера

Settlement вызывает провайдера синхронно (`call_provider()`) — если сам
вызов зависает (не таймаутится, а именно висит), поток блокируется на
одной транзакции и не переходит к следующей. Проверь Toxiproxy:
```bash
curl http://localhost:8474/proxies | python3 -m json.tool
```
Ищи toxic типа `timeout` с очень большим значением или полное отключение
прокси (`"enabled": false`) без соответствующего таймаута на стороне
`requests` — в редких случаях это может вести к зависанию дольше
ожидаемых 6 секунд (таймаут `requests.post(..., timeout=6)`).

## Восстановление

**Если это тестовый сценарий:**
```
traffic_gen.py → 3 → 12. Снять ВСЕ деградации
```

**Если settlement-поток реально упал (исключение в Python):**
```bash
tmux attach -t traffic-gen
# Ctrl+C, затем заново:
python3 traffic_gen.py run
```
Обрати внимание: раз это singleton in-memory circuit breaker и Toxiproxy
конфиг (не персистентный), после рестарта:
- circuit breaker сбросится в `closed` для всех провайдеров — это нормально
- Toxiproxy прокси (`paycore`/`fastpay`/`cryptogate`/`postgres`) нужно
  пересоздать, если Toxiproxy сам тоже перезапускался:
  ```bash
  bash appserver/toxiproxy/toxiproxy_setup.sh
  ```

**Если проблема в БД:** см. `db-unavailable.md`.

## Проверка после восстановления

```promql
app_pending_transactions
```
Должно начать убывать в течение минуты-двух после устранения причины —
settlement-поток резолвит до 50 pending за один проход, раз в секунду.

## Постмортем — шаблон

```markdown
## Инцидент: Pending Stuck — <дата>

**Пиковое число pending:** X
**Длительность:** с XX:XX до XX:XX (UTC)
**Причина:** [упавший поток / забытый тестовый сценарий / проблема БД / зависший вызов провайдера]
**Что можно улучшить:** [например: добавить watchdog-поток, перезапускающий settlement при N упавших итерациях подряд]
```
