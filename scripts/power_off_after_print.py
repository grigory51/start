from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.request import Request, urlopen


BASE_URL = "http://172.22.222.30:80"
POLL_SECONDS = 15
NOZZLE_LIMIT = 50.0
BED_LIMIT = 40.0
POWER_OFF = "SET_PIN PIN=power VALUE=0.00"


@dataclass(frozen=True)
class Snapshot:
    eventtime: float
    filename: str
    state: Literal["standby", "printing", "paused", "complete", "cancelled", "error"]
    duration: float
    active: bool
    paused: bool
    nozzle: float
    nozzle_target: float
    bed: float
    bed_target: float
    power: float

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> Snapshot:
        status = result["status"]
        stats = status["print_stats"]
        return cls(
            eventtime=result["eventtime"],
            filename=stats["filename"],
            state=stats["state"],
            duration=stats["total_duration"],
            active=status["virtual_sdcard"]["is_active"],
            paused=status["pause_resume"]["is_paused"],
            nozzle=status["extruder"]["temperature"],
            nozzle_target=status["extruder"]["target"],
            bed=status["heater_bed"]["temperature"],
            bed_target=status["heater_bed"]["target"],
            power=status["output_pin power"]["value"],
        )

    def ready_to_power_off(self) -> bool:
        return (
            self.state == "complete"
            and not self.active
            and not self.paused
            and self.nozzle_target == 0
            and self.bed_target == 0
            and 0 <= self.nozzle < NOZZLE_LIMIT
            and 0 <= self.bed < BED_LIMIT
        )


def api(path: str, payload: dict[str, str] | None = None) -> Any:
    request = Request(
        BASE_URL + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=10) as response:
        data = json.load(response)
    if "error" in data:
        raise RuntimeError(f"Moonraker: {data['error']}")
    return data["result"]


def read_snapshot() -> Snapshot:
    return Snapshot.from_result(api(
        "/printer/objects/query?print_stats&virtual_sdcard&pause_resume"
        "&extruder&heater_bed&output_pin%20power"
    ))


def check_same_print(previous: Snapshot, current: Snapshot) -> None:
    if (
        current.filename != previous.filename
        or current.duration < previous.duration
        or current.eventtime <= previous.eventtime
        or (previous.state == "complete" and current.state != "complete")
    ):
        raise RuntimeError("Задание изменилось или Klipper перезапущен. Выключение отменено.")
    if current.state not in ("printing", "paused", "complete"):
        raise RuntimeError(f"Печать не завершена успешно: {current.state}. Выключение отменено.")


def main() -> None:
    previous = read_snapshot()
    if previous.state not in ("printing", "paused") or not previous.filename:
        raise RuntimeError("Запустите скрипт во время нужной печати. Питание не изменено.")
    print(f"Ожидание завершения: {previous.filename}", flush=True)
    cool_checks = 0
    while True:
        time.sleep(POLL_SECONDS)
        current = read_snapshot()
        check_same_print(previous, current)
        if current.power != 1:
            raise RuntimeError("Power уже изменён. Автоматическое выключение отменено.")
        print(
            f"{current.state}: сопло {current.nozzle:.1f} °C, стол {current.bed:.1f} °C",
            flush=True,
        )
        cool_checks = cool_checks + 1 if current.ready_to_power_off() else 0
        if cool_checks >= 2:
            # Повторное чтение сокращает окно между проверкой и командой выключения.
            final = read_snapshot()
            check_same_print(current, final)
            if final.power != 1 or not final.ready_to_power_off():
                raise RuntimeError("Состояние перед выключением изменилось. Выключение отменено.")
            print(f"Отправка: {POWER_OFF}", flush=True)
            result = api("/printer/gcode/script", {"script": POWER_OFF})
            if result != "ok":
                raise RuntimeError(f"Неожиданный ответ на выключение: {result!r}")
            print("Moonraker подтвердил выполнение команды Power off.", flush=True)
            return
        previous = current


if __name__ == "__main__":
    main()
