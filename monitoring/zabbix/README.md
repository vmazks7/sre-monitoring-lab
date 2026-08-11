# Zabbix

Установка не изменилась относительно исходного курса:

```bash
wget https://repo.zabbix.com/zabbix/6.4/ubuntu/pool/main/z/zabbix-release/zabbix-release_6.4-1+ubuntu22.04_all.deb
sudo dpkg -i zabbix-release_6.4-1+ubuntu22.04_all.deb
sudo apt update
sudo apt install zabbix-server-pgsql zabbix-frontend-php zabbix-nginx-conf zabbix-sql-scripts zabbix-agent2
```

Отдельная БД `zabbix_db` (не путать с `shop`), хосты `AppServer`/`DBHost` добавляются вручную в Web UI со стандартными шаблонами "Linux by Zabbix agent" и "PostgreSQL by Zabbix agent 2".

Подробности и триггеры — в `docs/course.md`, модуль 2 и 5.
