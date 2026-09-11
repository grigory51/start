"""Общий Textual-экран для команд, реализующих контракт SDK."""
from __future__ import annotations

import asyncio
import subprocess
import traceback
from datetime import datetime

from rich.text import Text
from textual import work
from textual.events import Key
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Label, Select, Static

from .. import config
from . import Provider, RowAction, TableSnapshot, TaskContext


class ConfirmActionScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "confirm", "Да"), Binding("n,escape", "cancel", "Отмена")]
    DEFAULT_CSS = """
    ConfirmActionScreen { align: center middle; background: $background 70%; }
    ConfirmActionScreen Static { width: 60; height: auto; padding: 2; border: round $warning; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(self.message + "\n\ny — подтвердить · Esc — отменить", markup=False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TaskTableScreen(Screen):
    """Экран не знает команд и семантики фильтров; это ответственность provider."""

    BINDINGS = [
        Binding("q", "close", "Закрыть", show=False),
        Binding("escape", "close", "Закрыть", priority=True),
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
        self.snapshot: TableSnapshot | None = None
        self.action_running = False

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

    def on_mount(self) -> None:
        self.ready = True
        self.query_one(DataTable).focus()
        self.set_interval(self.definition.refresh, self.poll)
        self.action_reload()

    def poll(self) -> None:
        if not self.fetching and not self.action_running:
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
        if self.action_running:
            return
        self.generation += 1
        self.fetch(self.generation, dict(self.filters))

    @work(exclusive=True, group="task-refresh", exit_on_error=False)
    async def fetch(self, generation: int, filters: dict[str, str]) -> None:
        self.fetching = True
        try:
            snapshot = await self.provider(self.context, filters)
            if generation != self.generation:
                return
            self.snapshot = snapshot
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
                "Tab — фильтры · Enter — таблица · Esc — закрыть"
                + "".join(f" · {action.key} — {action.label}" for action in snapshot.actions)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self.generation:
                details = traceback.format_exc()
                if isinstance(exc, subprocess.CalledProcessError):
                    details += f"\n{exc.stderr or exc.output or ''}"
                self.query_one(DataTable).clear()
                self.snapshot = None
                self.query_one("#task-status", Static).update(details)
        finally:
            if generation == self.generation:
                self.fetching = False

    def action_close(self) -> None:
        self.ready = False
        self.workers.cancel_group(self, "task-refresh")
        self.workers.cancel_group(self, "row-action")
        self.app.pop_screen()

    def on_key(self, event: Key) -> None:
        table = self.query_one(DataTable)
        if not table.has_focus or self.action_running or not self.snapshot or not self.snapshot.rows:
            return
        for action in self.snapshot.actions:
            if event.key == action.key:
                event.stop()
                event.prevent_default()
                row = dict(zip(self.snapshot.columns, self.snapshot.rows[table.cursor_row]))
                self.action_running = True
                self.generation += 1
                self.workers.cancel_group(self, "task-refresh")
                self.fetching = False
                self.run_row_action(action, row)
                break

    @work(group="row-action", exclusive=True, exit_on_error=False)
    async def run_row_action(self, action: RowAction, row: dict[str, str]) -> None:
        try:
            confirmed = await self.app.push_screen_wait(
                ConfirmActionScreen(action.confirmation.format_map(row))
            )
            if confirmed:
                await action.handler(self.context, row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            details = traceback.format_exc()
            if isinstance(exc, subprocess.CalledProcessError):
                details += f"\n{exc.stderr or exc.output or ''}"
            self.notify(details, severity="error", timeout=15)
        finally:
            self.action_running = False
            if self.ready:
                self.action_reload()
