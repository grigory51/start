#!/usr/bin/env node
/**
 * statusline.mjs — статусбар Claude Code (своя плашка, без зависимостей).
 *
 * Работает на нативных полях stdin Claude Code — без внешних пакетов и sqlite.
 * Линкуется в ~/.claude/ менеджером claude-agents и подключается через
 * settings.json `statusLine` (см. config.toml [statusline]).
 *
 * CC передаёт JSON на stdin (docs: code.claude.com/docs/en/statusline). Используем:
 *   model.display_name                     — модель
 *   rate_limits.five_hour / seven_day      — лимиты использования (used% + resets_at)
 *   cost.total_duration_ms                 — длительность сессии
 *   context_window.used_percentage         — заполнение контекста
 *
 * Печатает одну строку. Всегда exit 0 — статусбар не должен ронять сессию.
 */

import { readFileSync } from "node:fs";

// --- ANSI ---
const C = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  cyan: "\x1b[36m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  red: "\x1b[31m",
};
const sep = `${C.dim} | ${C.reset}`;

// Цвет по проценту: <70 зелёный, <90 жёлтый, иначе красный.
function pct(n) {
  if (n == null) return null;
  const v = Math.round(n);
  const col = v < 70 ? C.green : v < 90 ? C.yellow : C.red;
  return `${col}${v}%${C.reset}`;
}

// Человекочитаемый остаток до resets_at (unix sec) — "2h13m" / "4d14h" / "" если нет.
function untilReset(epochSec) {
  if (!epochSec) return "";
  const ms = epochSec * 1000 - Date.now();
  if (ms <= 0) return "";
  const min = Math.floor(ms / 60000);
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  const m = min % 60;
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m`;
}

// Длительность сессии из мс — "0m" / "57m" / "1h05m".
function dur(ms) {
  if (!ms) return "0m";
  const min = Math.floor(ms / 60000);
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

function read() {
  try {
    return JSON.parse(readFileSync(0, "utf8"));
  } catch {
    return {};
  }
}

function main() {
  const d = read();
  const parts = [];

  // Бренд + модель.
  const model = d?.model?.display_name;
  if (model) parts.push(`${C.bold}${C.cyan}${model}${C.reset}`);

  // Лимиты использования.
  const rl = d?.rate_limits ?? {};
  const limit = (lim, label) => {
    if (!lim || lim.used_percentage == null) return null;
    const reset = untilReset(lim.resets_at);
    const tag = reset ? `${label}(${reset})` : label;
    return `${C.dim}${tag}: ${C.reset}${pct(lim.used_percentage)}`;
  };
  const lims = [limit(rl.five_hour, "5h"), limit(rl.seven_day, "7d")].filter(Boolean);
  if (lims.length) parts.push(lims.join(" "));

  // Сессия.
  if (d?.cost?.total_duration_ms != null) {
    parts.push(`${C.dim}session: ${C.reset}${C.green}${dur(d.cost.total_duration_ms)}${C.reset}`);
  }

  // Контекст.
  const ctx = d?.context_window?.used_percentage;
  if (ctx != null) parts.push(`${C.dim}ctx: ${C.reset}${pct(ctx)}`);

  process.stdout.write(parts.join(sep));
}

main();
