# Ранбуки

Каждый файл — самостоятельный документ на один тип инцидента: симптом,
приоритет, порядок диагностики, действия по восстановлению, эскалация,
шаблон постмортема. Формат одинаковый во всех, чтобы во время реального
разбора не тратить время на поиск нужного раздела.

## Бизнес-алерты (срабатывают из Alertmanager)

Симулируются через меню `traffic_gen.py` → 3 "Сценарии деградации".

| Ранбук | Когда открывать |
|---|---|
| [high-error-rate.md](high-error-rate.md) | Алерт `HighPaymentErrorRate` — резкий рост failed/timeout транзакций |
| [high-latency.md](high-latency.md) | Алерт `HighLatencyP95` — расчёт транзакций стал медленным |
| [provider-degradation.md](provider-degradation.md) | Алерт `ProviderCircuitBreakerOpen` / `ProviderConversionLow` — конкретный провайдер деградирует |
| [pending-stuck.md](pending-stuck.md) | Алерт `PendingTransactionsStuck` — транзакции не резолвятся |
| [db-unavailable.md](db-unavailable.md) | Алерт `DBHostDown` / `PostgresConnectionsHigh` |
| [reconciliation-mismatch.md](reconciliation-mismatch.md) | Алерт `ProviderMismatchRateHigh` — расхождение статусов с провайдером |

## Инфраструктурные инциденты (реальные, встреченные при разворачивании стека)

Это не смоделированные сценарии, а реальные проблемы, с которыми стек
столкнулся при первой сборке — от них не защищает circuit breaker или
Toxiproxy, потому что они на уровень ниже: сама инфраструктура мониторинга.

| Ранбук | Когда открывать |
|---|---|
| [service-crashloop.md](service-crashloop.md) | Любой systemd-сервис (`providers-mock@*`, `toxiproxy`, exporters) падает в CrashLoop |
| [elasticsearch-cluster-red.md](elasticsearch-cluster-red.md) | Elasticsearch не стартует, кластер `red`, или Kibana не может подключиться |
| [postgres-auth-failures.md](postgres-auth-failures.md) | `password authentication failed`, `permission denied for table`, `pg_up 0` у exporter'а |
| [log-pipeline-broken.md](log-pipeline-broken.md) | Логи транзакций не доходят до Kibana Discover |
| [disk-full-lvm.md](disk-full-lvm.md) | Диск ВМ заполнен, каскадные отказы сервисов из-за нехватки места |

## Общие правила для всех инцидентов

- **P1** (критично) — полная недоступность транзакций, error rate > 50%, БД недоступна. Действовать немедленно.
- **P2** (высокий) — деградация: error rate 10-50%, latency значительно выше нормы, сервис работает частично.
- **P3** (средний) — локальная аномалия: один провайдер, разовые всплески без устойчивого тренда.

Порядок диагностики всегда снизу вверх по стеку — от дешёвых проверок к дорогим:
**сеть → БД → приложение/circuit breaker → внешний провайдер**.

