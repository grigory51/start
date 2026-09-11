"""Listening ports: TCP LISTEN и UDP-сокеты через lsof."""
from __future__ import annotations

from ..command_sdk import TableSnapshot, TaskContext

LISTENING_COLUMNS = ("PID", "PROCESS", "PROTOCOL", "ADDRESS", "PORT")


def _port(filters: dict[str, str]) -> str:
    port = filters.get("port", "").strip()
    if not port:
        return ""
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("port должен быть числом от 1 до 65535")
    return port


def _address_and_port(name: str) -> tuple[str, str]:
    endpoint = name.split("->", 1)[0].strip()
    address, separator, port = endpoint.rpartition(":")
    return (address, port) if separator else (endpoint, "")


def _append_lsof_row(
    current: dict[str, str] | None, rows: list[tuple[str, ...]]
) -> None:
    if current is None:
        return
    protocol = current.get("protocol", "").upper()
    name = current.get("name", "")
    state = current.get("state", "")
    if not name or protocol not in {"TCP", "UDP"}:
        return
    if protocol == "TCP" and state != "LISTEN":
        return
    address, port = _address_and_port(name)
    rows.append((current.get("pid", ""), current.get("process", ""), protocol, address, port))


def parse_lsof_listening(stdout: str) -> TableSnapshot:
    """Разобрать lsof ``-FpcfPnT`` в строки listening sockets."""
    rows: list[tuple[str, ...]] = []
    pid = process = ""
    current: dict[str, str] | None = None

    for line in stdout.splitlines():
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == "p":
            _append_lsof_row(current, rows)
            current = None
            pid = value
        elif field == "c":
            process = value
        elif field == "f":
            _append_lsof_row(current, rows)
            current = {"pid": pid, "process": process}
        elif current is not None and field == "P":
            current["protocol"] = value
        elif current is not None and field == "n":
            current["name"] = value
        elif current is not None and field == "T" and value.startswith("ST="):
            current["state"] = value[3:]
    _append_lsof_row(current, rows)
    return TableSnapshot(columns=LISTENING_COLUMNS, rows=rows)


async def snapshot(
    context: TaskContext, filters: dict[str, str]
) -> TableSnapshot:
    """Снять TCP/UDP listening sockets через parseable lsof field output."""
    protocol = filters.get("protocol", "tcp").strip().lower() or "tcp"
    if protocol not in {"tcp", "udp", "all"}:
        raise ValueError("protocol должен быть tcp, udp или all")
    port = _port(filters)
    protocols = ("TCP", "UDP") if protocol == "all" else (protocol.upper(),)
    snapshots = []
    for item in protocols:
        internet = f"-i{item}{f':{port}' if port else ''}"
        arguments = ["lsof", "-nP", "-FpcfPnT", internet]
        if item == "TCP":
            arguments.append("-sTCP:LISTEN")
        stdout = await context.run(*arguments, allowed_codes=(0, 1))
        snapshots.append(parse_lsof_listening(stdout))
    return TableSnapshot(
        columns=LISTENING_COLUMNS,
        rows=[row for snapshot in snapshots for row in snapshot.rows],
    )
