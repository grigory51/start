# Incident response

## 1. Зафиксировать инцидент

- Симптом, affected services/users/sites и business impact.
- Время первого проявления, последнее известное рабочее состояние и текущая динамика.
- Недавние изменения: config, software, optics/cabling, upstream, security policy, certificates, DNS/IPAM.
- Один воспроизводимый probe с точными source, destination, protocol и timestamp.

## 2. Построить путь

Нарисуй или перечисли:

`source → access → aggregation → core/WAN → security/NAT/LB → destination`

Отдельно построй reverse path. Для каждого hop отметь VRF, VLAN, tunnel, routing protocol, ECMP, policy boundary и ownership.

## 3. Локализовать границу отказа

Проверяй переходы, а не устройства целиком:

1. Link state, optics, errors, discards, speed/duplex/FEC.
2. LAG member consistency, VLAN admission, STP state, MAC learning.
3. ARP/ND resolution и duplicate address признаки.
4. RIB и FIB на нужном VRF/table, next-hop recursion.
5. Routing adjacency, received/accepted/advertised prefixes и policy.
6. ACL/firewall/NAT counters и session state.
7. MTU/PMTUD, fragmentation, MSS, loss, latency и jitter.
8. DNS, TLS, load balancer и application health после доказанной L3/L4 связности.

## 4. Работать с доказательствами

Для каждого факта записывай источник, target и timestamp. Counter без двух измерений не показывает скорость изменения. Лог без timezone нельзя надёжно сопоставить с событием.

Не используй ping как единственное доказательство: ICMP может идти по другому policy/path или быть rate-limited. Подтверждай реальным протоколом сервиса.

## 5. Сформировать гипотезы

Таблица должна содержать:

| Гипотеза | Подтверждает | Опровергает | Следующий безопасный тест |
|---|---|---|---|

Не исправляй сразу наиболее знакомую причину. Сначала найди наблюдение, которое различает гипотезы.

## 6. Изменение

До изменения:

- baseline и сохранённый relevant config/state;
- проверенный management/OOB path;
- exact diff, blast radius и зависимости;
- rollback commands или transactional rollback;
- stop conditions и ответственный за подтверждение.

После изменения:

- дождись ожидаемого convergence, не произвольного sleep;
- сравни protocol state, RIB/FIB, counters и logs с baseline;
- повтори исходный probe из того же source;
- проверь минимум один соседний несломанный сервис;
- зафиксируй результат и отклонения.

## 7. Post-incident

Разделяй:

- triggering event;
- root cause;
- contributing factors;
- почему detection/guardrail не остановили проблему;
- corrective actions с owner и проверяемым completion criterion.

Не называй человеческую ошибку root cause, если система позволила одному действию создать недопустимый blast radius.
