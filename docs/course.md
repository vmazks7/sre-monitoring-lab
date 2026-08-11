# Курс: Мониторинг высоконагруженных транзакционных систем (SRE Lab)

Практический курс на 8 модулей. Формат — в своём темпе (ориентир 2–3 модуля за интенсивный день).
Каждый модуль: короткий контекст → точные команды → проверка → типичные проблемы и решение.

**Цель репозитория:** воспроизводимая лаборатория + материал для портфолио (ссылка на GitHub в резюме).

**Проверенный стек на момент сборки гайда:**
- Хост: Windows 11, VMware Workstation
- 3× Ubuntu **26.04 LTS (resolute)** Server
- Dual-NIC: lab `10.10.10.0/24` (Host-only) + NAT для интернета и доступа с хоста
- PostgreSQL 18, Prometheus 3.x, Alertmanager, Grafana, ELK 8.x, Zabbix (agent2 + server), Toxiproxy, node_exporter, postgres_exporter

**Ресурсы ВМ (минимум, проверено):**

| ВМ | vCPU | RAM | Диск | IP lab (ens33) | IP NAT (ens34, DHCP) |
|----|------|-----|------|----------------|----------------------|
| AppServer | 2 | 4 GB | 20 GB | 10.10.10.11 | 192.168.36.x |
| DBHost | 2 | 4 GB | 20 GB | 10.10.10.12 | 192.168.36.x |
| Monitoring | 2 | 16 GB | 100 GB | 10.10.10.13 | 192.168.36.x |

На хосте с 32 GB RAM этого достаточно, чтобы держать все три ВМ одновременно (будет апдейт для хоста с 16 GB RAM)

---

## Модуль 0. Архитектура и виртуальные машины

### Топология

```
┌─────────────────────────────────────────────────────────────────┐
│  Host: Windows 11 + VMware Workstation                          │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐       │
│  │  AppServer   │   │   DBHost     │   │  Monitoring    │       | 
│  │  Ubuntu 26.04│   │  Ubuntu 26.04│   │  Ubuntu 26.04  |       │
│  │  2 vCPU/4GB  │   │  2 vCPU/4GB  │   │  2 vCPU/16GB   |       │
│  │  10.10.10.11 │   │  10.10.10.12 │   │  10.10.10.13   |       │
│  └──────┬───────┘   └──────┬───────┘   └───────────┬────┘       │
│         │                  │                       │            │
│         └──────────────────┴───────────────────────┘            │ 
│              ens33: Host-only / Custom  10.10.10.0/24           │
│              ens34: NAT (интернет, DHCP, доступ с хоста)        │
└─────────────────────────────────────────────────────────────────┘
```

**Роли:**
- **AppServer** — генератор транзакций (`traffic_gen.py`), Toxiproxy, mock-провайдеры, node_exporter, Filebeat
- **DBHost** — PostgreSQL (`shop`), postgres_exporter, node_exporter
- **Monitoring** — Prometheus, Alertmanager, Grafana, ELK, Zabbix Server, node_exporter

### 0.1. Сеть в VMware

1. **Edit → Virtual Network Editor** (от администратора).
2. **Add Network** → выбери свободный VMnet (например VMnet2).
3. Тип: **Host-only** (или Custom Host-only).
4. Subnet: `10.10.10.0`, mask `255.255.255.0`.
5. **DHCP выключен** (IP только статикой на ВМ).
6. NAT (обычно VMnet8) оставь по умолчанию — вторая карта каждой ВМ пойдёт туда.

### 0.2. Создание ВМ (повторить три раза)

1. Скачай **Ubuntu 26.04 LTS Server** с официального сайта Ubuntu.
2. VMware: **Create a New Virtual Machine**.
3. ISO → Ubuntu Server 26.04.
4. Имя ВМ / hostname / пользователь Linux:
   - `appserver` / user `appserver`
   - `dbhost` / user `dbhost`
   - `monitoring` / user `monitoring`
5. Ресурсы — по таблице выше.
6. **Network Adapter 1:** Custom → VMnet с `10.10.10.0/24` (lab).
7. **Add → Network Adapter:** NAT (интернет + доступ браузером с Windows).
8. Установка ОС: стандартный Server, OpenSSH server **включить**.
9. После первого входа: `sudo apt update && sudo apt upgrade -y && sudo reboot`.

### 0.3. Netplan на каждой ВМ

Файл: `/etc/netplan/01-netcfg.yaml`  
(старый `00-installer-config.yaml` лучше удалить, чтобы не конфликтовал).

**AppServer** (`10.10.10.11`):

```yaml
network:
  version: 2
  ethernets:
    ens33:
      match:
        macaddress: <MAC_ens33>
      set-name: ens33
      dhcp4: false
      addresses: [10.10.10.11/24]
    ens34:
      dhcp4: true
      dhcp6: false
```

**DBHost** — то же, адрес `10.10.10.12/24`.  
**Monitoring** — то же, адрес `10.10.10.13/24`.

MAC возьми из `ip link` или из настроек ВМ. Имена интерфейсов могут быть `ens33`/`ens34` (как в этой лабе).

Применить:

```bash
sudo rm -f /etc/netplan/00-installer-config.yaml
sudo netplan apply
ip a
ip route
```

- Lab-трафик (`10.10.10.0/24`) — через `ens33`, без default gateway (это нормально).
- Интернет и default route — через `ens34` (DHCP от VMware NAT).

Проверка с каждой ВМ:

```bash
ping -c 2 10.10.10.11
ping -c 2 10.10.10.12
ping -c 2 10.10.10.13
ping -c 2 8.8.8.8
```

### 0.4. Возможные проблемы (модуль 0)

| Симптом                                      | Решение                                                                                         |
|----------------------------------------------|-------------------------------------------------------------------------------------------------|
| Два netplan-файла конфликтуют                | Оставить один `01-netcfg.yaml`, удалить installer-config                                        |
| `netplan apply` не меняет IP                 | `sudo systemctl restart systemd-networkd`, проверить MAC в `match`                              |
| Нет интернета                                | Проверить, что вторая NIC в режиме NAT и `ens34` получил DHCP                                   |
| Клон ВМ → путаница hostname/machine-id       | `sudo hostnamectl set-hostname ...`; `sudo rm /etc/machine-id && sudo systemd-machine-id-setup` |

---

## Модуль 1. База данных (DBHost)

Схема и демо-данные лежат в репозитории: `dbhost/sql/sql_db_setup.sql`.  
**Не набирай DDL вручную** — импортируй файл.

### 1.1. Установка PostgreSQL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
psql --version
```

### 1.2. Пользователь и БД приложения

```bash
sudo -u postgres psql -c "CREATE USER appuser WITH PASSWORD 'apppass';"
sudo -u postgres psql -c "CREATE DATABASE shop OWNER appuser;"
```

### 1.3. Импорт схемы

Скопируй `sql_db_setup.sql` на DBHost (scp / shared folder):


С хоста:

```powershell
scp C:\path\to\sql_db_setup.sql dbhost@10.10.10.11:/tmp
```
Затем на ВМ:

```bash
sudo -u postgres psql -d shop -f /tmp/sql_db_setup.sql
```

Проверка:

```bash
sudo -u postgres psql -d shop -c "\dt"
sudo -u postgres psql -d shop -c "SELECT count(*) FROM providers;"
```

### 1.4. Пользователь для postgres_exporter

```bash
sudo -u postgres psql -c "CREATE USER exporter WITH PASSWORD 'exporter_pass';"
sudo -u postgres psql -c "GRANT pg_monitor TO exporter;"
```

### 1.5. pg_hba.conf

Файл (PostgreSQL 18): `/etc/postgresql/18/main/pg_hba.conf`

Добавь доступ из lab-сети и localhost (метод `scram-sha-256` или `md5` — как принято у установки):

```
# lab network
host    shop         appuser     10.10.10.0/24    scram-sha-256
host    shop         exporter    10.10.10.0/24    scram-sha-256
# local zabbix / tools
host    zabbix_db    zabbix      127.0.0.1/32     md5
host    all          all         127.0.0.1/32     scram-sha-256
```

```bash
sudo systemctl reload postgresql
```

Проверка с AppServer:

```bash
psql -h 10.10.10.12 -U appuser -d shop -c "SELECT 1;"
```

### 1.6. Возможные проблемы (БД)

| Симптом                                   | Решение                                                                         |
|-------------------------------------------|---------------------------------------------------------------------------------|
| `no pg_hba.conf entry for host "..."`     | Добавить подсеть клиента (`10.10.10.0/24` или NAT `192.168.36.0/24`) и `reload` |
| `password authentication failed`          | Пароль / `ALTER USER`; метод в hba должен совпадать                             |
| Импорт от `postgres` в БД `OWNER appuser` | При необходимости сменить owner таблиц или импортировать с нужными правами      |
| Подключение только с localhost            | `listen_addresses = '*'` в `postgresql.conf` + reload                           |

---

## Модуль 2. AppServer: экспортёры, Toxiproxy, генератор

### 2.1. Базовые пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libpq-dev git tmux curl postgresql-client
```

### 2.2. node_exporter (порт 9100), ставится на все три ВМ

Актуальную версию смотри на GitHub releases (не копируй старый номер — сочетание `/latest/` и чужого имени файла даёт 404).

```bash
cd /tmp
wget https://github.com/prometheus/node_exporter/releases/download/v1.12.1/node_exporter-1.12.1.linux-amd64.tar.gz
tar xvf node_exporter-1.12.1.linux-amd64.tar.gz
sudo mv node_exporter-1.12.1.linux-amd64/node_exporter /usr/local/bin/
sudo useradd --no-create-home --shell /usr/sbin/nologin node_exporter 2>/dev/null || true
sudo chown node_exporter:node_exporter /usr/local/bin/node_exporter

sudo tee /etc/systemd/system/node_exporter.service > /dev/null << 'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
Type=simple
User=node_exporter
Group=node_exporter
ExecStart=/usr/local/bin/node_exporter
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now node_exporter
curl -s localhost:9100/metrics | head
```

То же самое поставь на **DBHost** и **Monitoring**.



### 2.3. Toxiproxy

```bash
cd /tmp
# версию сверь со страницей releases Shopify/toxiproxy
wget https://github.com/Shopify/toxiproxy/releases/latest/download/toxiproxy-server-linux-amd64
sudo mv toxiproxy-server-linux-amd64 /usr/local/bin/toxiproxy-server
sudo chmod +x /usr/local/bin/toxiproxy-server

sudo tee /etc/systemd/system/toxiproxy.service > /dev/null << 'EOF'
[Unit]
Description=Toxiproxy - TCP proxy for chaos testing
After=network.target

[Service]
ExecStart=/usr/local/bin/toxiproxy-server -host 0.0.0.0 -port 8474
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now toxiproxy
curl -s http://localhost:8474/proxies
```

Скрипт прокси из репо: `appserver/toxiproxy/toxiproxy_setup.sh`  
(upstream БД: `10.10.10.12:5432`, mock-провайдеры на `127.0.0.1:9101-9103`).

```bash
sudo mkdir -p /opt/appserver
# скопируй appserver/{traffic_gen,toxiproxy,systemd} в /opt/appserver и unit-ы в /etc/systemd
# scp C:\path\to\appserver appserver@10.10.10.11:/tmp
# sudo cp /tmp/appserver/traffic_gen /opt/appserver - для traffic_gen 
# sudo cp /tmp/appserver/toxiproxy /opt/appserver - для toxiproxy
# sudo cp /tmp/appserver/systemd/providers-mock@.service /etc/systemd
# sudo cp /tmp/appserver/systemd/toxiproxy.service /etc/systemd

sudo chmod +x /opt/appserver/toxiproxy/toxiproxy_setup.sh
sudo bash /opt/appserver/toxiproxy/toxiproxy_setup.sh
curl -s http://localhost:8474/proxies | python3 -m json.tool
```

Ожидаются прокси: `paycore`, `fastpay`, `cryptogate`, `postgres` (`0.0.0.0:15432` → DBHost:5432).


Примечание: После рестарта сервиса, необходимо проверить существуют ли прокси провайдеров.
Они хранятся только в памяти процесса Toxiproxy, а не на диске, и исчезают при каждом рестарте сервиса.

```bash
sudo systemctl status toxiproxy
# если inactive/failed:
sudo systemctl enable --now toxiproxy
sudo systemctl status toxiproxy
# проверить моки
curl http://localhost:8474/proxies
# если пусто ({}) — пересоздаем:
bash /opt/appserver/toxiproxy/toxiproxy_setup.sh
```

### 2.4. Mock-провайдеры и код генератора

```bash
sudo useradd --no-create-home --shell /bin/false appuser 2>/dev/null || true
sudo cp -r /path/to/appserver/traffic_gen /opt/appserver/
sudo cp /path/to/appserver/systemd/providers-mock@.service /etc/systemd/system/

cd /opt/appserver/traffic_gen
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# процесс systemd (User=appuser) должен видеть зависимости:
# либо поправить unit на venv/bin/python, либо:
sudo pip install -r requirements.txt --break-system-packages

sudo systemctl daemon-reload
sudo systemctl enable --now providers-mock@9101 providers-mock@9102 providers-mock@9103
curl -s -X POST localhost:9101/charge -H 'Content-Type: application/json' -d '{}'
```

### 2.5. Генератор трафика

**Что делает:** создаёт транзакции в PostgreSQL, ходит в mock-провайдеры **через Toxiproxy**, ведёт circuit breaker, пишет JSON-логи и отдаёт метрики Prometheus на `:8000`. Аварии включаются из **интерактивного меню**, без правки кода.

**Запуск:**

```bash
cd /opt/appserver/traffic_gen
source venv/bin/activate   # если используешь venv

python3 traffic_gen.py seed   # один раз — справочники

sudo mkdir -p /var/log/appserver
sudo chown "$(whoami):$(id -gn)" /var/log/appserver

tmux new -s traffic-gen
python3 traffic_gen.py run
# Detach: Ctrl+B, D
```

**Проверка:**
- меню отвечает на сценарии деградации;
- `tail -f /var/log/appserver/transactions.log` — JSON-события;
- `curl -s localhost:8000/metrics | head` — метрики приложения;
- `psql -h 127.0.0.1 -p 15432 -U appuser -d shop -c "SELECT count(*) FROM transactions;"` — рост строк (через Toxiproxy).

Полный исходник в репозитории (`appserver/traffic_gen/`).

### 2.6. Filebeat (логи → Logstash на Monitoring)

```bash
wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | sudo gpg --dearmor -o /usr/share/keyrings/elastic.gpg
echo "deb [signed-by=/usr/share/keyrings/elastic.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | sudo tee /etc/apt/sources.list.d/elastic-8.x.list
sudo apt update && sudo apt install -y filebeat

sudo tee /etc/filebeat/filebeat.yml > /dev/null << 'EOF'
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/appserver/transactions.log

output.logstash:
  hosts: ["10.10.10.13:5044"]
EOF

sudo systemctl enable --now filebeat
sudo systemctl status filebeat
```

### 2.7. Возможные проблемы (AppServer)

| Симптом                                       | Решение                                                                                  |
|-----------------------------------------------|------------------------------------------------------------------------------------------|
| wget node_exporter / prometheus 404           | В URL старая версия при редиректе `/latest/` — качай `.../download/vX.Y.Z/file-X.Y.Z...` |
| `toxiproxy_setup.sh: Permission denied`       | `chmod +x` или `sudo bash ...`                                                           |
| `connection ... port 15432 ... server closed` | Toxiproxy не запущен / нет proxy `postgres` / PostgreSQL на DBHost недоступен            |
| providers-mock failed                         | Права на `/opt/appserver`, Python-зависимости для `User=` в unit                         |
| seed не коннектится к БД                      | `config.py`: хост `127.0.0.1`, порт `15432`, user/password как на DBHost                 |
| Нет `/var/log/appserver`                      | Создать каталог и выдать права пользователю, от которого идёт `run`                      |

---

## Модуль 3. DBHost: postgres_exporter

```bash
cd /tmp
wget https://github.com/prometheus-community/postgres_exporter/releases/download/v0.20.1/postgres_exporter-0.20.1.linux-amd64.tar.gz
tar xvf postgres_exporter-0.20.1.linux-amd64.tar.gz
sudo mv postgres_exporter-0.20.1.linux-amd64/postgres_exporter /usr/local/bin/

sudo useradd --no-create-home --shell /usr/sbin/nologin postgres_exporter 2>/dev/null || true

sudo tee /etc/systemd/system/postgres_exporter.service > /dev/null << 'EOF'
[Unit]
Description=Postgres Exporter for Prometheus
After=network.target postgresql.service

[Service]
Environment=DATA_SOURCE_NAME="postgresql://exporter:exporter_pass@localhost:5432/shop?sslmode=disable"
ExecStart=/usr/local/bin/postgres_exporter
Restart=always
User=postgres_exporter

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now postgres_exporter
curl -s localhost:9187/metrics | head
```

Без `sslmode=disable` exporter часто «молчит», если SSL на PostgreSQL не настроен.

---

## Модуль 4. Monitoring: Prometheus, Alertmanager, Grafana

### 4.1. Prometheus

```bash
cd /tmp
wget https://github.com/prometheus/prometheus/releases/download/v3.13.2/prometheus-3.13.2.linux-amd64.tar.gz
tar xvf prometheus-3.13.2.linux-amd64.tar.gz
cd prometheus-3.13.2.linux-amd64
sudo mv prometheus promtool /usr/local/bin/
sudo mkdir -p /etc/prometheus /var/lib/prometheus

sudo useradd --no-create-home --shell /usr/sbin/nologin prometheus 2>/dev/null || true
sudo cp /path/to/repo/monitoring/prometheus/prometheus.yml /etc/prometheus/
sudo cp /path/to/repo/monitoring/prometheus/alert_rules.yml /etc/prometheus/
sudo chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus

sudo tee /etc/systemd/system/prometheus.service > /dev/null << 'EOF'
[Unit]
Description=Prometheus
After=network.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus \
  --web.listen-address=0.0.0.0:9090
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now prometheus
curl -s http://localhost:9090/-/healthy
```

### 4.2. Alertmanager

```bash
cd /tmp
wget https://github.com/prometheus/alertmanager/releases/download/v0.33.1/alertmanager-0.33.1.linux-amd64.tar.gz
tar xvf alertmanager-0.33.1.linux-amd64.tar.gz
sudo mv alertmanager-0.33.1.linux-amd64/alertmanager /usr/local/bin/
sudo mkdir -p /etc/alertmanager /var/lib/alertmanager
# scp с хоста в папку /tmp
sudo cp /tmp/monitoring/alertmanager/alertmanager.yml /etc/alertmanager/
# Подставь bot_token, chat_id, SMTP (Rambler/Yandex/Gmail app password)

sudo tee /etc/systemd/system/alertmanager.service > /dev/null << 'EOF'
[Unit]
Description=Prometheus Alertmanager
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/alertmanager \
  --config.file=/etc/alertmanager/alertmanager.yml \
  --storage.path=/var/lib/alertmanager \
  --web.listen-address=0.0.0.0:9093
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now alertmanager
curl -s http://localhost:9093/-/healthy
```

### 4.3. Grafana

```bash
sudo apt install -y grafana
sudo systemctl enable --now grafana-server
```

UI с **хоста Windows** (если ВМ без GUI):

```text
http://10.10.10.13:3000
```

или через NAT-IP Monitoring (`http://192.168.36.x:3000`).

Логин по умолчанию: `admin` / `admin`,
После логина попросит поменять.

**Data sources:**
1. Prometheus — `http://localhost:9090`
2. PostgreSQL — Host `10.10.10.12:5432`, DB `shop`, user `appuser`, password `apppass`, SSL `disable`

Если слушает только `127.0.0.1` — в `/etc/grafana/grafana.ini` секция `[server]`, `http_addr` пустой, затем restart.

---

## Модуль 5. ELK на Monitoring

```bash
sudo apt install -y elasticsearch logstash kibana
```

`/etc/elasticsearch/elasticsearch.yml` (лаба без security):

```yaml
network.host: 10.10.10.13
discovery.type: single-node
xpack.security.enabled: false
```

`/etc/kibana/kibana.yml`:

```yaml
server.host: "10.10.10.13"
elasticsearch.hosts: ["http://10.10.10.13:9200"]
```

Pipeline: `monitoring/elk/logstash/appserver.conf` → `/etc/logstash/conf.d/`.

```bash
sudo systemctl enable --now elasticsearch logstash kibana
curl -s http://10.10.10.13:9200
curl -I http://10.10.10.13:5601
```

На Monitoring желательно ≥16 GB RAM: Elasticsearch легко уходит в OOM на 4–8 GB.

---

## Модуль 6. Zabbix

На Ubuntu 26.04 подключи официальный `zabbix-release` (ветка 7.0/7.4)

```bash
sudo apt install -y zabbix-server-pgsql zabbix-frontend-php zabbix-nginx-conf zabbix-sql-scripts zabbix-agent2
# + phpX.Y-pgsql / phpX.Y-fpm — по версии PHP (на 26.04 встречался 8.5)

sudo -u postgres createuser zabbix
sudo -u postgres createdb -O zabbix zabbix_db
zcat /usr/share/zabbix-sql-scripts/postgresql/server.sql.gz | sudo -u zabbix psql zabbix_db
# на новых пакетах путь может быть /usr/share/zabbix/sql-scripts/...

# в /etc/zabbix/zabbix_server.conf → добавить: DBName=zabbix_db, DBUser=zabbix, DBPassword=...
sudo systemctl restart zabbix-server zabbix-agent2 nginx php*-fpm
```

UI: `http://10.10.10.13/zabbix` (или порт из nginx-конфига Zabbix).

**Агенты на AppServer и DBHost:** `Server=` / `ServerActive=10.10.10.13`, `Hostname=AppServer` или `DBHost` (регистр как в UI).

**Хосты в UI:**
- **AppServer** — IP `10.10.10.11`, template *Linux by Zabbix agent*, группа `Payment Gateway Lab`
- **DBHost** — IP `10.10.10.12`, *Linux by Zabbix agent* + *PostgreSQL by Zabbix agent 2*

Опционально: item `app.error_rate` + trigger `min(/AppServer/app.error_rate,#3)>10`.

---

## Модуль 7. Дашборды, алерты, проверка

### Чек-лист

```bash
# AppServer
curl -s localhost:9100/metrics | head
curl -s localhost:8474/proxies | head
curl -s localhost:8000/metrics | head
systemctl is-active toxiproxy 'providers-mock@9101' filebeat

# DBHost
curl -s localhost:9100/metrics | head
curl -s localhost:9187/metrics | head
systemctl is-active postgresql postgres_exporter

# Monitoring
curl -s localhost:9090/-/healthy
curl -s localhost:9093/-/healthy
curl -s localhost:3000/api/health
curl -s localhost:9200
curl -I localhost:5601 # Если Failed to connect to localhost port 5601, то curl -I 10.10.10.13:5601
systemctl is-active prometheus alertmanager grafana-server elasticsearch logstash kibana zabbix-server
```

С Windows: Grafana `:3000`, Kibana `:5601`, Zabbix UI, Prometheus `:9090`.

Правила алертов: `monitoring/prometheus/alert_rules.yml`.  
Доставка: Telegram + email в `alertmanager.yml`.

---

## Модуль 8. Chaos / тренировки

Управление — меню `python3 traffic_gen.py run` и Toxiproxy API (латентность, timeout, отказ провайдера/БД).  
Дополнительно: сетевой partition (`iptables`), долгая транзакция в `psql`, всплеск RPS без роста error rate.

Для каждого сценария: время до алерта → дашборд → runbook в `docs/runbooks/` → короткий постмортем.


