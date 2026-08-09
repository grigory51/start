---
name: network-operations
description: Диагностика, эксплуатация и безопасное изменение корпоративных сетей. Используй для NOC-инцидентов, анализа конфигураций и телеметрии, routing/switching, BGP/OSPF/IS-IS, MPLS, EVPN/VXLAN, STP/LAG, DNS/DHCP/NAT/VPN, packet capture, производительности и vendor-specific работы с Cisco IOS/IOS XE/NX-OS, Juniper Junos, Huawei VRP и MikroTik RouterOS.
---

# Network Operations

Работай от наблюдаемого симптома к доказанной причине. Не подменяй факты типовой схемой из памяти.

## Контекст

Перед работой определи:

- влияние на пользователей и сервисы, начало и динамику инцидента;
- источник и назначение трафика, оба направления пути, VRF/VLAN/tenant;
- vendor, platform, точную версию NOS и роль каждого устройства;
- ожидаемое состояние из SoT, схемы, конфигурации или change ticket;
- доступные источники: monitoring, logs, flow telemetry, configs, pcap, controller/API.

Для incident response прочитай [references/incident-response.md](references/incident-response.md). Для команд и различий вендоров прочитай [references/vendors.md](references/vendors.md). Для выбора automation/MCP прочитай [references/tooling.md](references/tooling.md).

## Безопасность

- Не подключайся к сетевому устройству или удалённому хосту и не выполняй даже read-only CLI-команду без явного разрешения владельца с точными targets и командами.
- Любой config push, NETCONF/RESTCONF mutation, commit, rollback, clear/reset, flap, failover, reload, firmware upgrade, сканирование или packet capture требует отдельного явного разрешения.
- Перед изменением покажи diff или точный command set, blast radius, pre-checks, rollback и post-checks. Не объединяй независимые изменения.
- Не отключай host-key/TLS verification и не используй plaintext management protocol без явного решения пользователя.
- Не сохраняй пароли, tokens, private keys и community strings в репозитории, inventory или выводе. Используй environment, keychain или внешний secret store.
- Сохраняй management path и out-of-band доступ. Не меняй одновременно основной и резервный control path.

## Диагностика

1. Зафиксируй симптом как проверяемое утверждение: кто, откуда, куда, по какому протоколу и когда не проходит.
2. Построй предполагаемый forward и reverse path. Отмечай NAT, firewall, load balancer, tunnel, ECMP и asymmetric routing.
3. Раздели management, control и data plane. Рабочий BGP session не доказывает корректный forwarding.
4. Иди по слоям и границам отказа: physical → link/LAG → VLAN/STP → ARP/ND → routing/FIB → policy/NAT → transport/application.
5. Сопоставь факты по единому времени. Проверяй timezone, NTP и разницу между event time и ingestion time.
6. Сформируй несколько гипотез, для каждой укажи подтверждающий и опровергающий тест. Проверяй наиболее дешёвый и безопасный тест первым.
7. После разрешения собери минимальный достаточный набор данных. Не выгружай полную конфигурацию и все логи, если нужен один интерфейс или peer.
8. Меняй одну причинную переменную. Затем повтори исходный пользовательский тест и проверь соседние сервисы.

## Источники истины

Приоритет:

1. Фактический packet path и counters в момент проблемы.
2. Structured operational state из controller/API/telemetry.
3. Running/candidate configuration точной версии устройства.
4. SoT и approved intended state.
5. Схемы и документация.

Расхождение между SoT и live state является отдельным результатом, а не поводом молча выбрать одну сторону.

## Изменения

Для любого production change подготовь:

- цель и доказанную причину;
- затрагиваемые устройства, интерфейсы, VRF/VLAN и соседей;
- pre-check snapshot;
- точный vendor-native diff/commands;
- ожидаемое влияние и время convergence;
- автоматический либо пошаговый rollback с trigger conditions;
- post-checks control plane, data plane и пользовательского сервиса;
- способ доказать отсутствие unintended side effects.

Предпочитай candidate/compare/commit-confirmed, checkpoints и transactional API, когда platform их поддерживает. Не имитируй транзакцию на системе с immediate-apply semantics.

## Формат результата

Выдавай кратко:

1. Impact и scope.
2. Проверенные факты с источником и timestamp.
3. Topology/path и локализованная граница отказа.
4. Гипотезы по вероятности и тесты.
5. Команды или запросы, ожидающие разрешения.
6. Root cause либо текущая неопределённость.
7. Change, rollback и verification, если изменение согласовано.

Не называй гипотезу root cause, пока она не объясняет симптом и не подтверждена независимой проверкой.
