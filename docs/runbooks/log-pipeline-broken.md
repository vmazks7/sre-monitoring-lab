# Runbook: Log Pipeline Broken (Filebeat → Logstash → Elasticsearch)

**Когда открывать:** в Kibana Discover не появляются новые события,
либо `curl "http://10.10.10.13:9200/appserver-transactions-*/_count?pretty"`
не растёт, хотя генератор трафика точно работает.
**Приоритет:** P2 — на работу самого платёжного шлюза не влияет, но
теряется вся видимость через логи (Kibana, поиск аномалий, reconciliation).

## Диагностика — пройти цепочку от источника к приёмнику

Лог идёт по пути: `traffic_gen.py` → файл на диске → Filebeat → Logstash
(порт 5044) → Elasticsearch → Kibana. Проверяй именно в этом порядке —
не имеет смысла разбираться с Logstash, если файла с логами вообще нет.

### 1. Файл с логами существует и растёт?

```bash
ls -la /var/log/appserver/transactions.log
tail -f /var/log/appserver/transactions.log
```
Если файла нет — проблема не в pipeline, а в самом генераторе: либо
директория `/var/log/appserver/` не создана/не те права (запись в лог
файл тихо проваливается с предупреждением в stderr, не падает процесс —
легко пропустить), либо `traffic_gen.py run` вообще не запущен
(`tmux ls`, есть ли сессия).

### 2. Filebeat реально читает файл и пытается его отправить?

```bash
sudo systemctl status filebeat
sudo journalctl -u filebeat -n 30 --no-pager
```

Смотри конкретно на `output`-строки:
- `"Connecting to backoff(async(tcp://10.10.10.13:5044))"` — хороший
  знак, Filebeat настроен на Logstash и пытается подключиться.
- `"Connecting to backoff(elasticsearch(http://...:5044))"` — плохой
  знак: слово `elasticsearch` в скобках, даже если порт правильный (5044),
  означает, что активна секция `output.elasticsearch`, а не
  `output.logstash`. Дефолтный конфиг Filebeat включает
  `output.elasticsearch` из коробки — если при правке файла её не
  закомментировать полностью, Filebeat пытается говорить HTTP-протоколом
  Elasticsearch с портом, который слушает Logstash по бинарному
  Beats-протоколу.
  ```bash
  grep -A2 "^output\." /etc/filebeat/filebeat.yml
  ```
  Должна быть только одна активная `output`-секция — `output.logstash`.
  Если видишь обе (одна закомментирована не полностью) — почини и
  перезапусти:
  ```bash
  sudo systemctl restart filebeat
  ```

### 3. Logstash реально принимает соединение на 5044?

```bash
sudo ss -tlnp | grep 5044
sudo journalctl -u logstash -n 50 --no-pager
```

Симптом того самого рассинхрона Filebeat-output из пункта 2 виден именно
здесь:
```
InvalidFrameProtocolException: Invalid version of beats protocol: 69
```
(байт `69` — буква `E`, начало HTTP-запроса `GET`/`ELASTIC...` — Logstash
получил не то, что ожидал). Если видишь это в момент, когда Filebeat
активен — возвращайся к пункту 2, дело там, не в самом Logstash.

### 4. Elasticsearch принимает данные от Logstash?

```bash
curl -s "http://10.10.10.13:9200/_cat/indices?v" | grep appserver
```
Если индекса `appserver-transactions-*` вообще нет — данные не доходят
до Elasticsearch (либо Logstash не может до него достучаться, либо сам
Elasticsearch недоступен — см. `elasticsearch-cluster-red.md`). Если
индекс есть, но `docs.count` не растёт — события доходят, но, возможно,
отбрасываются фильтром pipeline'а (`json`-парсинг падает на
неожиданном формате строки).

### 5. Kibana видит индекс, но Discover пустой?

Проверь, что Data View реально указывает на правильный index pattern:
Stack Management → Data Views → `appserver-transactions*`, timestamp
field `@timestamp`. И что временной диапазон в Discover (справа сверху)
покрывает момент, когда реально шли события — самая частая ложная тревога
"логов нет", когда они просто не попадают в выбранное окно времени.

## Проверка после восстановления — сквозная

```bash
# сгенерируй заведомо новое событие (или просто подожди — генератор в tmux)
sleep 15
curl -s "http://10.10.10.13:9200/appserver-transactions-*/_count?pretty"
# число должно расти при повторных вызовах через несколько секунд
```

## Постмортем — шаблон

```markdown
## Инцидент: Log Pipeline Broken — <дата>

**Разрыв на этапе (файл / Filebeat / Logstash / Elasticsearch / Kibana):** [указать]
**Длительность потери логов:** с XX:XX до XX:XX
**Действия:** [что сделано]
**Что можно улучшить:** [например: алерт на отсутствие новых документов в индексе N минут]
```
