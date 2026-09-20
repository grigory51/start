"""Деревья процессов Codex из общего снимка ps."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
import sys
from typing import Literal

from .. import config
from ..command_sdk import RowAction, RowDetails, RowTable, TableSnapshot, TaskContext

COLUMNS = ("SESSION", "PID", "PPID", "PGID", "CPU %", "MEM %", "ELAPSED", "STATE", "TYPE", "COMMAND")


@dataclass
class Process:
    pid: int
    ppid: int
    pgid: str
    cpu: float
    memory: float
    elapsed: str
    state: str
    command: str

    @classmethod
    def parse(cls, line: str) -> Process:
        pid, ppid, pgid, cpu, memory, elapsed, state, command = line.strip().split(maxsplit=7)
        return cls(int(pid), int(ppid), pgid, float(cpu), float(memory), elapsed, state, command)


    def sort_value(self, column: str) -> float | str:
        if column == "ELAPSED":
            return elapsed_seconds(self.elapsed)
        if column == "COMMAND":
            return self.command.casefold()
        return {"PID": self.pid, "CPU %": self.cpu, "MEM %": self.memory}[column]


def elapsed_seconds(value: str) -> int:
    days, separator, time = value.rpartition("-")
    seconds = 0
    for part in time.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds + (int(days) * 86400 if separator else 0)


def sorting(
    filters: dict[str, str], columns: tuple[str, ...], default: str, order: str
) -> tuple[str, bool]:
    column = filters.get("sort", default)
    direction = filters.get("order", order)
    if column not in columns:
        raise ValueError(f"Неизвестное поле сортировки: {column}")
    if direction not in {"asc", "desc"}:
        raise ValueError("order должен быть asc или desc")
    return column, direction == "desc"


async def tree_snapshot(
    context: TaskContext, filters: dict[str, str], *, session: str = ""
) -> TableSnapshot:
    session = session or filters.get("session", "").strip()
    if session and (not session.isascii() or not session.isdecimal() or int(session) < 1):
        raise ValueError("session должен быть положительным PID Codex")
    kind_filter = filters.get("kind", "all")
    if kind_filter not in {"all", "process", "mcp"}:
        raise ValueError("kind должен быть all, process или mcp")
    sort, descending = sorting(filters, ("PID", "CPU %", "MEM %", "ELAPSED", "COMMAND"), "PID", "asc")
    query = filters.get("command", "").casefold()
    roots_output = await context.run("pgrep", "-ix", "codex", allowed_codes=(0, 1))
    root_ids = {int(pid) for pid in roots_output.split()}
    actions = (RowAction("enter", "О процессе", process_details),)
    if not root_ids:
        return TableSnapshot(COLUMNS, [], actions)
    output = await context.run("ps", "-ww", "-e", "-o", "pid=,ppid=,pgid=,pcpu=,pmem=,etime=,stat=,args=")
    processes: dict[int, Process] = {}
    for line in output.splitlines():
        if line.strip():
            process = Process.parse(line)
            processes[process.pid] = process
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process.pid)
    # Вложенный Codex относится к дереву внешней сессии и не дублируется.
    roots = []
    for pid in sorted(root_ids & processes.keys()):
        parent = processes[pid].ppid
        ancestors = {pid}
        while parent in processes and parent not in ancestors and parent not in root_ids:
            ancestors.add(parent)
            parent = processes[parent].ppid
        if parent not in root_ids:
            roots.append(pid)
    trees: list[tuple[float, int, list[tuple[str, ...]]]] = []
    for root in roots:
        rows: list[tuple[str, ...]] = []
        total_memory = 0.0
        stack: list[tuple[int, int, Literal["process", "mcp"]]] = [(root, 0, "process")]
        visited: set[int] = set()
        while stack:
            pid, depth, kind = stack.pop()
            if pid in visited:
                continue
            visited.add(pid)
            process = processes[pid]
            command = process.command.casefold()
            if "mcp" in command or "node_repl" in command:
                kind = "mcp"
            prefix = "  " * (depth - 1) + "↳ " if depth else ""
            rows.append((str(root), str(pid), str(process.ppid), process.pgid,
                         f"{process.cpu:.2f}", f"{process.memory:.2f}", process.elapsed,
                         process.state, kind, prefix + process.command))
            total_memory += process.memory
            siblings = sorted(children.get(pid, []))
            siblings.sort(key=lambda child: processes[child].sort_value(sort), reverse=descending)
            stack.extend((child, depth + 1, kind) for child in reversed(siblings))
        trees.append((total_memory, root, rows))
    rows = [row for _, root, tree in sorted(trees, key=lambda tree: (-tree[0], tree[1]))
            if not session or root == int(session)
            for row in tree if (kind_filter == "all" or row[8] == kind_filter)
            and query in row[9].casefold()]
    return TableSnapshot(COLUMNS, rows, actions)


async def process_details(
    context: TaskContext, row: dict[str, str], *, session: str = ""
) -> RowDetails:
    session = session or row["SESSION"]
    current = await tree_snapshot(context, {}, session=session)
    selected = next((values for values in current.rows if values[1] == row["PID"]), None)
    title = f"Процесс PID {row['PID']} · Codex {session}"
    if selected is None:
        return RowDetails(title, "Процесс завершился или больше не принадлежит этой сессии.")
    cwd = (await session_directories(context, [session]))[session]
    cpu = sum(float(values[4]) for values in current.rows)
    memory = sum(float(values[5]) for values in current.rows)
    text = "\n".join(f"{column}: {value}" for column, value in zip(COLUMNS[:-1], selected[:-1]))
    command = selected[-1].lstrip()
    if command.startswith("↳ "):
        command = command[2:]
    text += f"\n\nКоманда запуска:\n{command}\n\nCWD сессии: {cwd}"
    text += f"\nВсего процессов: {len(current.rows)} · CPU: {cpu:.2f}% · MEM: {memory:.2f}%"
    return RowDetails(title, text)


async def session_directories(context: TaskContext, sessions: list[str]) -> dict[str, str]:
    directories = dict.fromkeys(sessions, "Недоступен")
    if not sessions:
        return directories
    if sys.platform == "linux":
        for pid in sessions:
            try:
                directories[pid] = str(Path(f"/proc/{pid}/cwd").readlink())
            except (FileNotFoundError, PermissionError) as error:
                directories[pid] = f"Недоступен: {error}"
    else:
        output = await context.run(
            "lsof", "-a", "-p", ",".join(sessions), "-d", "cwd", "-Fpn", allowed_codes=(0, 1)
        )
        pid = ""
        for line in output.splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("n") and pid in directories:
                directories[pid] = line[1:]
    return directories


async def snapshot(context: TaskContext, filters: dict[str, str]) -> TableSnapshot:
    columns = ("PID", "CWD", "PROCESSES", "CPU %", "MEM %", "ELAPSED")
    sort, descending = sorting(filters, columns, "MEM %", "desc")
    tree = await tree_snapshot(context, {})
    sessions: dict[str, list[tuple[str, ...]]] = {}
    for row in tree.rows:
        sessions.setdefault(row[0], []).append(row)
    directories = await session_directories(context, list(sessions))
    query = filters.get("cwd", "").casefold()
    rows = []
    for pid, processes in sessions.items():
        cwd = directories[pid]
        if query not in cwd.casefold():
            continue
        cpu = sum(float(process[4]) for process in processes)
        memory = sum(float(process[5]) for process in processes)
        rows.append((pid, cwd, str(len(processes)), f"{cpu:.2f}", f"{memory:.2f}", processes[0][6]))
    index = columns.index(sort)
    rows.sort(key=lambda row: int(row[0]))
    rows.sort(key=lambda row: (
        row[index].casefold() if sort == "CWD" else
        elapsed_seconds(row[index]) if sort == "ELAPSED" else float(row[index])
    ), reverse=descending)
    return TableSnapshot(
        columns, rows,
        (RowAction("enter", "Дерево процессов", open_session),),
    )


async def session_snapshot(
    context: TaskContext, filters: dict[str, str], *, session: str
) -> TableSnapshot:
    tree = await tree_snapshot(context, filters, session=session)
    visible = [index for index, column in enumerate(tree.columns) if column not in {"SESSION", "PGID"}]
    return TableSnapshot(
        tuple(tree.columns[index] for index in visible),
        [tuple(row[index] for index in visible) for row in tree.rows],
        (RowAction("enter", "О процессе", partial(process_details, session=session)),),
    )


async def open_session(context: TaskContext, row: dict[str, str]) -> RowTable:
    return RowTable(
        config.Task(
            name="codex-session", title=f"Codex {row['PID']} · {row['CWD']}",
            description="Дерево процессов сессии", run={}, sudo=context.sudo,
            filters=[
                config.TaskFilter("kind", "Тип", default="all", options=["all", "process", "mcp"]),
                config.TaskFilter("command", "Команда содержит"),
                config.TaskFilter("sort", "Сортировка", default="PID",
                                  options=["PID", "CPU %", "MEM %", "ELAPSED", "COMMAND"]),
                config.TaskFilter("order", "Порядок", default="asc", options=["asc", "desc"]),
            ],
        ),
        partial(session_snapshot, session=row["PID"]),
    )
