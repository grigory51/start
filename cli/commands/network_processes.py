"""Сеть по приложениям: процессы и соединения из CSV nettop на macOS."""
from __future__ import annotations

import csv
import re
from io import StringIO

from ..command_sdk import TableSnapshot, TaskContext

NETWORK_COLUMNS = ("PID", "PROCESS", "INTERFACE", "CONNECTION", "STATE", "IN", "OUT")


def _nettop_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _process_and_pid(value: str) -> tuple[str, str]:
    if "<->" in value or "->" in value:
        return value, ""
    match = re.fullmatch(r"(.+)\.(\d+)", value.strip())
    return (match.group(1), match.group(2)) if match else (value, "")


def parse_nettop_csv(stdout: str) -> TableSnapshot:
    """Разобрать CSV nettop с process-id в безымянной первой колонке."""
    source_rows = [
        row for row in csv.reader(StringIO(stdout)) if any(value.strip() for value in row)
    ]
    header_index = next(
        (
            index
            for index, row in enumerate(source_rows)
            if {"interface", "state", "bytes_in", "bytes_out"}
            <= {_nettop_key(value) for value in row}
        ),
        None,
    )
    if header_index is None:
        if not source_rows:
            return TableSnapshot(columns=NETWORK_COLUMNS, rows=[])
        raise ValueError("nettop CSV не содержит заголовок interface/state/bytes")

    header_row = source_rows[header_index]
    header = {_nettop_key(value): index for index, value in enumerate(header_row)}
    rows: list[tuple[str, ...]] = []
    process = pid = ""
    for source in source_rows[header_index + 1:]:
        if not source or len(source) != len(header_row):
            continue
        values = {name: source[index].strip() for name, index in header.items()}
        identifier = source[0].strip()
        parsed_process, parsed_pid = _process_and_pid(identifier)
        if parsed_pid:
            process, pid = parsed_process, parsed_pid
            connection = ""
        else:
            connection = identifier
        if not process:
            continue
        rows.append(
            (
                pid,
                process,
                values.get("interface", ""),
                connection,
                values.get("state", ""),
                values.get("bytes_in", ""),
                values.get("bytes_out", ""),
            )
        )
    return TableSnapshot(columns=NETWORK_COLUMNS, rows=rows)


async def snapshot(
    context: TaskContext, filters: dict[str, str]
) -> TableSnapshot:
    """Снять один CSV sample nettop без запуска его curses-интерфейса."""
    protocol = filters.get("protocol", "tcp").strip().lower() or "tcp"
    if protocol not in {"tcp", "udp", "all"}:
        raise ValueError("protocol должен быть tcp, udp или all")
    application = filters.get("application", "").strip()
    arguments = [
        "nettop",
        "-L",
        "1",
        "-n",
        "-x",
        "-J",
        "interface,state,bytes_in,bytes_out",
    ]
    if protocol != "all":
        arguments.extend(("-m", protocol))
    if application:
        arguments.extend(("-p", application))
    return parse_nettop_csv(await context.run(*arguments))
