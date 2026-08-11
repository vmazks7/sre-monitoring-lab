# Runbook: Elasticsearch Won't Start / Cluster Red

**Когда открывать:** `systemctl status elasticsearch` не `active`, либо
`curl http://10.10.10.13:9200/_cluster/health?pretty` показывает
`"status": "red"`, либо Kibana не может подключиться
(`Unable to retrieve version information from Elasticsearch nodes`).
**Приоритет:** P1 для всей ветки логов — без Elasticsearch не работают ни
Logstash (пишет в никуда), ни Kibana Discover.

## Диагностика — по порядку исключения

Три independent причины дают внешне похожую картину ("ES не работает"),
но чинятся по-разному.

### 1. Сервис вообще запускается?

```bash
systemctl status elasticsearch
```

Если `Active: failed`, смотри код и сразу `journalctl`:
```bash
sudo journalctl -u elasticsearch -n 50 --no-pager
```

**Конфликт конфигурации** — самая частая причина падения именно на
старте (сервис даже не пытается открыть порт):
```
IllegalArgumentException: setting [cluster.initial_master_nodes] is not
allowed when [discovery.type] is set to [single-node]
```
Эти два параметра в `elasticsearch.yml` взаимоисключающие для однонодового
кластера — решение:
```bash
sudo grep -n "cluster.initial_master_nodes\|discovery.type" /etc/elasticsearch/elasticsearch.yml
```
Убедись, что `cluster.initial_master_nodes` закомментирован или удалён,
активна только `discovery.type: single-node`.

**OOM** — если в логе `Job for elasticsearch.service failed because of
an out-of-memory (OOM) situation`, проверь фактическую память:
```bash
free -h
```
Правильное решение — выделить ВМ больше RAM (Elasticsearch по умолчанию
просит ~50% системной памяти под heap), не урезать heap искусственно —
с урезанным heap на многосервисной ВМ ты просто откладываешь ту же
проблему на первый серьёзный всплеск нагрузки.

### 2. Сервис жив, но кластер `red`

```bash
curl -s http://10.10.10.13:9200/_cluster/health?pretty
```
Смотри `unassigned_shards`/`unassigned_primary_shards` — если больше нуля,
это почти всегда диск:
```bash
curl -s "http://10.10.10.13:9200/_cluster/allocation/explain?pretty"
```
Ищи в ответе `"decider": "disk_threshold"` и
`"explanation": "the node is above the high watermark..."`. Проверь
реальное место:
```bash
df -h /
```
Elasticsearch блокирует выделение новых шардов уже при 90% занятости
диска (`high watermark`) — это защита, не баг. Решение — расширить диск
(см. `disk-full-lvm.md`), не пытаться обойти watermark через настройки
(это лечит симптом, не причину, и рискует реально заполнить диск под 0).

После расширения диска кластер сам переоценивает allocation в течение
минуты; форсировать вручную:
```bash
curl -X POST "http://10.10.10.13:9200/_cluster/reroute?retry_failed=true"
```

### 3. ES зелёный, но Kibana всё равно не может стартовать

Отдельная, третья причина — таймаут миграции системных индексов Kibana:
```
[index_not_green_timeout] Timeout waiting for the status of the
[.kibana_8.19.19_001] index to become 'green'
```
Причина — дефолтный `number_of_replicas: 1` у системных индексов
Kibana, а реплике физически негде разместиться на одной ноде (индекс
навсегда остаётся `yellow`). Это НЕ то же самое, что `red`-кластер из
пункта 2 — статус может быть `yellow`, а не `red`, и это тоже блокирует
Kibana:
```bash
curl -X PUT "http://10.10.10.13:9200/.kibana*/_settings" \
  -H "Content-Type: application/json" \
  -d '{"index": {"number_of_replicas": 0}}'
```
И на будущее — дефолт для всех новых индексов сразу, чтобы не повторять
это каждый раз, когда Logstash создаёт новый дневной индекс:
```bash
curl -X PUT "http://10.10.10.13:9200/_index_template/single_node_defaults" \
  -H "Content-Type: application/json" \
  -d '{"index_patterns": ["*"], "template": {"settings": {"number_of_replicas": 0}}, "priority": 1}'
```

## Проверка после восстановления

```bash
curl -s http://10.10.10.13:9200/_cluster/health?pretty
# ожидаем "status": "green" (или хотя бы "yellow" без unassigned)

sudo systemctl restart kibana
sleep 20
curl -I http://localhost:5601
# curl -I http://10.10.10.13:5601
# ожидаем HTTP/1.1 302 Found
 
```

## Постмортем — шаблон

```markdown
## Инцидент: Elasticsearch Down — <дата>

**Причина (конфиг / память / диск / реплики Kibana):** [указать]
**Длительность:** с XX:XX до XX:XX
**Действия:** [что сделано]
**Что можно улучшить:** [например: алерт на заполнение диска задолго до 90%]
```
