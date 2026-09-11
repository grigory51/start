"""Общий Textual-экран для команд, реализующих контракт SDK."""
from __future__ import annotations

import asyncio
import subprocess
import traceback
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Label, Select, Static

from .. import config
from . import Provider, TaskContext


class TaskTableScreen(Screen):
    """Экран не знает команд и семантики фильтров; это ответственность provider."""

    BINDINGS = [
        Binding("q", "close", "Назад из таблицы"),
        Binding("escape", "close", "Назад", priority=True),
        Binding("ctrl+r", "reload", "Обновить"),
    ]
    DEFAULT_CSS = """
    TaskTableScreen { layout: vertical; }
    TaskTableScreen #task-title { height: auto; padding: 1; text-style: bold; }
    TaskTableScreen #task-filters { height: auto; }
    TaskTableScreen .task-filter { width: 1fr; height: auto; padding: 0 1; }
    TaskTableScreen Label { height: 1; }
    TaskTableScreen DataTable { height: 1fr; }
    TaskTableScreen #task-status { height: auto; max-height: 10; overflow-y: auto; padding: 0 1; }
    """

    def __init__(self, task: config.Task, provider: Provider) -> None:
        super().__init__()
        self.definition = task
        self.provider = provider
        self.context = TaskContext(sudo=task.sudo)
        self.filters = {f.name: f.default for f in task.filters}
        self.columns: tuple[str, ...] = ()
        self.ready = False
        self.fetching = False
        self.generation = 0

    def compose(self) -> ComposeResult:
        yield Static(self.definition.title, id="task-title", markup=False)
        with Horizontal(id="task-filters"):
            for field in self.definition.filters:
                with Vertical(classes="task-filter"):
                    yield Label(field.label, markup=False)
                    if field.options:
                        yield Select([(value, value) for value in field.options],
                                     value=field.default, allow_blank=False, id=f"filter-{field.name}")
                    else:
                        yield Input(value=field.default, placeholder="Все", id=f"filter-{field.name}")
        yield DataTable(id="task-data", cursor_type="row", zebra_stripes=True)
        yield Static("", id="task-status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.ready = True
        self.query_one(DataTable).focus()
        self.set_interval(self.definition.refresh, self.poll)
        self.action_reload()

    def poll(self) -> None:
        if not self.fetching:
            self.action_reload()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.ready:
            self.filters[event.input.id.removeprefix("filter-")] = event.value.strip()
            self.action_reload()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self.ready and event.value is not Select.BLANK:
            self.filters[event.select.id.removeprefix("filter-")] = str(event.value)
            self.action_reload()

    def on_input_submitted(self) -> None:
        self.query_one(DataTable).focus()

    def action_reload(self) -> None:
        self.generation += 1
        self.fetch(self.generation, dict(self.filters))

    @work(exclusive=True, group="task-refresh", exit_on_error=False)
    async def fetch(self, generation: int, filters: dict[str, str]) -> None:
        self.fetching = True
        try:
            snapshot = await self.provider(self.context, filters)
            if generation != self.generation:
                return
            table = self.query_one(DataTable)
            cursor = table.cursor_row
            scroll = table.scroll_offset
            changed = snapshot.columns != self.columns
            table.clear(columns=changed)
            if changed:
                table.add_columns(*(Text(label) for label in snapshot.columns))
                self.columns = snapshot.columns
            table.add_rows(tuple(Text(str(cell)) for cell in row) for row in snapshot.rows)
            if snapshot.rows:
                table.move_cursor(row=min(cursor, len(snapshot.rows) - 1))
                table.scroll_to(scroll.x, scroll.y, animate=False)
            self.query_one("#task-status", Static).update(
                f"{len(snapshot.rows)} строк · {datetime.now():%H:%M:%S} · "
                "Tab — фильтры · Enter — таблица · q — назад из таблицы · Esc — назад всегда"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self.generation:
                details = traceback.format_exc()
                if isinstance(exc, subprocess.CalledProcessError):
                    details += f"\n{exc.stderr or exc.output or ''}"
                self.query_one(DataTable).clear()
                self.query_one("#task-status", Static).update(details)
        finally:
            if generation == self.generation:
                self.fetching = False

    def action_close(self) -> None:
        self.ready = False
        self.workers.cancel_group(self, "task-refresh")
        self.app.pop_screen()
