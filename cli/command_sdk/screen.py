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
from textual.widgets import DataTable, Input, Label, LoadingIndicator, Select, Static, TextArea

from .. import config
from . import Provider, RowAction, RowDetails, RowPrompt, RowTable, TableSnapshot, TaskContext


class RowDetailsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "close", "Закрыть", priority=True)]
    DEFAULT_CSS = """
    RowDetailsScreen { align: center middle; background: $background 70%; }
    RowDetailsScreen #details-panel { width: 90%; height: 85%; border: round $primary; padding: 1; }
    RowDetailsScreen Static { height: auto; }
    RowDetailsScreen TextArea { height: 1fr; margin: 1 0; }
    """

    def __init__(self, details: RowDetails, context: TaskContext | None = None) -> None:
        super().__init__()
        self.details = details
        self.context = context or TaskContext()
        self.action_running = False

    def compose(self) -> ComposeResult:
        with Vertical(id="details-panel"):
            yield Static(self.details.title, markup=False)
            yield TextArea(self.details.text, read_only=True, soft_wrap=True, show_line_numbers=False)
            yield Static(self.action_hint(), id="details-status", markup=False)

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_close(self) -> None:
        self.workers.cancel_group(self, "details-action")
        self.dismiss(None)

    def action_hint(self) -> str:
        return "Esc / q — назад" + "".join(f" · {action.key} — {action.label}" for action in self.details.actions)

    async def on_key(self, event: Key) -> None:
        for action in self.details.actions:
            if event.key == action.key:
                event.stop()
                event.prevent_default()
                if not self.action_running:
                    self.action_running = True
                    self.run_details_action(action)
                return

    @work(group="details-action", exclusive=True, exit_on_error=False)
    async def run_details_action(self, action: RowAction) -> None:
        status = self.query_one("#details-status", Static)
        status.update(f"{action.label}…")
        try:
            await present_action(self, self.context, action, {})
        finally:
            self.action_running = False
            if self.is_mounted:
                status.update(self.action_hint())


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


class RowPromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Отмена", priority=True)]
    DEFAULT_CSS = """
    RowPromptScreen { align: center middle; background: $background 70%; }
    RowPromptScreen #prompt-panel { width: 60; height: auto; border: round $primary; padding: 1; }
    RowPromptScreen Input { margin: 1 0; }
    """

    def __init__(self, prompt: RowPrompt) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-panel"):
            yield Static(self.prompt.title, markup=False)
            yield Label(self.prompt.label, markup=False)
            yield Input(value=self.prompt.default, id="prompt-input")
            yield Static("Enter — подтвердить · Esc — отменить", markup=False)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TaskTableScreen(Screen[None]):
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
    TaskTableScreen #task-loading { height: 1; }
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
        yield LoadingIndicator(id="task-loading")
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
        if self.snapshot is None:
            self.query_one("#task-loading", LoadingIndicator).display = True
            self.query_one("#task-status", Static).update("Загрузка… · Esc — назад")
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
                "Tab — фильтры · Esc — закрыть"
                + "".join(
                    f" · {action.key} — {action.label}"
                    for action in (*snapshot.actions, *snapshot.table_actions)
                )
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
                self.query_one("#task-loading", LoadingIndicator).display = False

    def action_close(self) -> None:
        self.ready = False
        self.workers.cancel_group(self, "task-refresh")
        self.workers.cancel_group(self, "row-action")
        self.dismiss(None)

    def on_key(self, event: Key) -> None:
        if event.key != "enter" and self.start_row_action(event.key):
            event.stop()
            event.prevent_default()
        elif event.key != "enter" and self.start_table_action(event.key):
            event.stop()
            event.prevent_default()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.start_row_action("enter")

    def start_row_action(self, key: str) -> bool:
        table = self.query_one(DataTable)
        if not table.has_focus or self.action_running or not self.snapshot or not self.snapshot.rows:
            return False
        for action in self.snapshot.actions:
            if key == action.key:
                row = dict(zip(self.snapshot.columns, self.snapshot.rows[table.cursor_row]))
                self.start_action(action, row)
                return True
        return False

    def start_table_action(self, key: str) -> bool:
        table = self.query_one(DataTable)
        if not table.has_focus or self.action_running or not self.snapshot:
            return False
        for action in self.snapshot.table_actions:
            if key == action.key:
                self.start_action(action, {})
                return True
        return False

    def start_action(self, action: RowAction, row: dict[str, str]) -> None:
        self.action_running = True
        self.generation += 1
        self.workers.cancel_group(self, "task-refresh")
        self.fetching = False
        self.query_one("#task-loading", LoadingIndicator).display = False
        self.query_one("#task-status", Static).update(f"{action.label}…")
        self.run_row_action(action, row)

    @work(group="row-action", exclusive=True, exit_on_error=False)
    async def run_row_action(self, action: RowAction, row: dict[str, str]) -> None:
        try:
            await present_action(self, self.context, action, row)
        finally:
            self.action_running = False
            if self.ready:
                self.action_reload()


async def present_action(
    screen: Screen, context: TaskContext, action: RowAction, row: dict[str, str]
) -> None:
    """Единый цикл подтверждения, ввода и показа результатов действия."""
    try:
        if action.confirmation is not None:
            confirmed = await screen.app.push_screen_wait(
                ConfirmActionScreen(action.confirmation.format_map(row))
            )
            if not confirmed:
                return
        result = await action.handler(context, row)
        while result is not None:
            if isinstance(result, RowDetails):
                await screen.app.push_screen_wait(RowDetailsScreen(result, context))
                return
            if isinstance(result, RowTable):
                await screen.app.push_screen_wait(TaskTableScreen(result.task, result.provider))
                return
            if isinstance(result, RowPrompt):
                value = await screen.app.push_screen_wait(RowPromptScreen(result))
                if value is None:
                    return
                result = await result.handler(context, value)
                continue
            raise TypeError(f"Неизвестный результат действия: {type(result).__name__}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        details = traceback.format_exc()
        if isinstance(exc, subprocess.CalledProcessError):
            details += f"\n{exc.stderr or exc.output or ''}"
        screen.notify(details, severity="error", timeout=15)
