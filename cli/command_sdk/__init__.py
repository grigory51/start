"""Контракт команд без зависимости от Textual: context + filters → snapshot."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Awaitable, Callable

from .. import config


@dataclass
class RowAction:
    """Действие над выбранной строкой; confirmation форматируется по колонкам."""
    key: str
    label: str
    handler: Callable[[TaskContext, dict[str, str]], Awaitable[None]]
    confirmation: str


@dataclass
class TableSnapshot:
    """Один снимок таблицы: колонки и строки одинаковой ширины."""
    columns: tuple[str, ...]
    rows: list[tuple[str, ...]]
    actions: tuple[RowAction, ...] = ()

    def __post_init__(self) -> None:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Количество значений строки не совпадает с колонками")


@dataclass
class TaskContext:
    """Запуск процессов с sudo, таймаутом и отменой, предоставляемый менеджером."""
    sudo: bool = False
    timeout: float = 15.0

    async def run(self, *argv: str, allowed_codes: tuple[int, ...] = (0,)) -> str:
        """Выполнить argv без shell; отмена/таймаут завершает и собирает процесс."""
        if self.sudo:
            # Обновляем уже полученный timestamp, не запрашивая пароль внутри TUI.
            await TaskContext(timeout=self.timeout).run("sudo", "-n", "-v")
            argv = ("sudo", "-n", "--", *argv)
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=config.REPO_DIR,
            env={**os.environ, "REPO": str(config.REPO_DIR), "LC_ALL": "C"},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            # Отдельная группа при сохранении controlling TTY для sudo timestamp.
            process_group=0 if os.name == "posix" else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.terminate()
            if process.returncode is None:
                # sudo передаёт SIGTERM дочерней команде; SIGKILL не даёт ему
                # завершить привилегированный процесс перед возвратом в менеджер.
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        output = stdout.decode(errors="replace")
        error = stderr.decode(errors="replace")
        if process.returncode not in allowed_codes or (process.returncode != 0 and error.strip()):
            raise subprocess.CalledProcessError(process.returncode or 1, argv, output, error)
        return output


Provider = Callable[[TaskContext, dict[str, str]], Awaitable[TableSnapshot]]


def load_provider(reference: str) -> Provider:
    module, name = reference.split(":", 1)
    provider = getattr(importlib.import_module(module), name)
    if not inspect.iscoroutinefunction(provider):
        raise TypeError(f"{reference} должен быть async provider-функцией")
    return provider
