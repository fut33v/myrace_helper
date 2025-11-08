#!/usr/bin/env python3
"""Monitor MyRace race revenue and notify admins when it changes."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from decimal import Decimal
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Dict, Iterable, List, MutableMapping, Optional, Tuple

import requests

from income_goals import get_income_goals_path, load_income_goals
from race_metrics import RaceMetrics, fetch_race_metrics, format_money

LOGGER = logging.getLogger("race_income_watcher")

DEFAULT_INTERVAL = 300  # seconds
DEFAULT_COOKIES = "cookies/myrace_cookies.txt"
DEFAULT_STATE_PATH = "data/race_income_state.json"


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    LOGGER.setLevel(level)


def _load_cookies(path: Path) -> MozillaCookieJar:
    if not path.exists():
        raise FileNotFoundError(f"Cookie-файл {path} не найден.")
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _parse_admin_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for part in raw.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            LOGGER.warning("Пропускаю некорректный TELEGRAM_ADMIN_ID: %s", chunk)
    return ids


def _load_race_ids() -> List[str]:
    explicit = os.getenv("MYRACE_WATCH_RACE_IDS", "").strip()
    if explicit:
        result = [item.strip() for item in explicit.split(",") if item.strip()]
        if result:
            return result

    store_path = Path(os.getenv("MYRACE_RACES_PATH", "races.json"))
    if store_path.exists():
        try:
            with store_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            LOGGER.warning("Не удалось разобрать %s: %s", store_path, exc)
        else:
            collected: List[str] = []
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        value = item.get("id") or item.get("race_id")
                    else:
                        value = None
                    if value is None:
                        continue
                    collected.append(str(value))
            if collected:
                return collected

    env_default = os.getenv("MYRACE_RACE_ID", "1440").strip()
    if env_default:
        return [env_default]
    return []


def _read_state(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Не удалось прочитать состояние из %s: %s", path, exc)
        return {}
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}
    LOGGER.warning("Неверный формат state-файла %s, начинаем с пустого состояния.", path)
    return {}


def _write_state(path: Path, state: MutableMapping[str, Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(path)


def _build_session(cookies_path: Path) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MyRaceHelperBot/1.0",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    session.cookies = _load_cookies(cookies_path)
    return session


def _send_notification(
    bot_token: str,
    admin_ids: Iterable[int],
    message: str,
) -> None:
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for chat_id in admin_ids:
        try:
            response = requests.post(
                api_url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Не удалось отправить уведомление админам (%s): %s", chat_id, exc)


def _build_message(
    previous: Decimal,
    current: Decimal,
    metrics: RaceMetrics,
    target: Optional[Decimal] = None,
) -> str:
    delta = current - previous
    direction = "⬆️" if delta > 0 else "⬇️"
    delta_text = format_money(delta.copy_abs())
    if delta == 0:
        direction = "➖"
    previous_text = format_money(previous)
    current_text = format_money(current)
    lines = [
        f"💰 Доход изменился для гонки <b>{metrics.title}</b> (ID {metrics.race_id}).",
        f"{direction} Было: {previous_text} → Стало: {current_text} ₽ (Δ {delta_text}).",
        f"👥 Участников: {metrics.participants}",
    ]
    if target is not None:
        target_text = format_money(target)
        remaining = target - current
        if remaining > 0:
            remaining_text = format_money(remaining)
            lines.append(f"🎯 Цель: {target_text} ₽ (осталось {remaining_text} ₽).")
        else:
            lines.append(f"🎯 Цель: {target_text} ₽ достигнута или превышена!")
    return "\n".join(lines)


def run_monitor() -> None:
    _configure_logging()
    interval_env = os.getenv("MYRACE_WATCH_INTERVAL", "").strip()
    try:
        interval = max(60, int(interval_env)) if interval_env else DEFAULT_INTERVAL
    except ValueError:
        LOGGER.warning("Некорректное значение MYRACE_WATCH_INTERVAL=%s, используем %s", interval_env, DEFAULT_INTERVAL)
        interval = DEFAULT_INTERVAL

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        LOGGER.error("Не указан TELEGRAM_BOT_TOKEN, уведомления недоступны.")
        sys.exit(2)
    admin_ids = _parse_admin_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    if not admin_ids:
        LOGGER.error("Список TELEGRAM_ADMIN_IDS пуст — некому отправлять уведомления.")
        sys.exit(2)

    cookies_path = Path(os.getenv("MYRACE_COOKIES_PATH", DEFAULT_COOKIES)).expanduser()
    state_path = Path(os.getenv("MYRACE_WATCH_STATE_PATH", DEFAULT_STATE_PATH)).expanduser()
    state = _read_state(state_path)
    goals_path = get_income_goals_path()
    LOGGER.info("Запускаем мониторинг каждые %s секунд.", interval)

    stop_requested = False

    def _handle_signal(signum: int, _frame) -> None:  # type: ignore[override]
        LOGGER.info("Получен сигнал %s, завершаем после текущей итерации.", signum)
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = _build_session(cookies_path)
    last_reported_ids: Optional[Tuple[str, ...]] = None

    while True:
        start_ts = time.monotonic()
        try:
            session.cookies = _load_cookies(cookies_path)
        except FileNotFoundError as exc:
            LOGGER.error("%s", exc)
            time.sleep(interval)
            continue

        race_ids = _load_race_ids()
        if not race_ids:
            LOGGER.error("Не найден список гонок (MYRACE_WATCH_RACE_IDS / races.json / MYRACE_RACE_ID). Ждём и пробуем снова.")
            if stop_requested:
                break
            time.sleep(interval)
            continue
        race_ids_tuple = tuple(race_ids)
        if race_ids_tuple != last_reported_ids:
            LOGGER.info("Мониторим гонки: %s", ", ".join(race_ids))
            last_reported_ids = race_ids_tuple

        income_goals = load_income_goals(goals_path)
        state_changed = False
        for race_id in race_ids:
            try:
                metrics = fetch_race_metrics(session, race_id)
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.error("Не удалось обновить гонку %s: %s", race_id, exc)
                continue
            previous_entry = state.get(race_id)
            current_value = str(metrics.revenue)
            if not previous_entry:
                LOGGER.info("Добавляем в наблюдение гонку %s с доходом %s ₽.", race_id, format_money(metrics.revenue))
                state[race_id] = {
                    "revenue": current_value,
                    "participants": str(metrics.participants),
                    "updated_at": str(int(time.time())),
                }
                state_changed = True
                continue
            previous_revenue = Decimal(previous_entry.get("revenue", "0"))
            if metrics.revenue == previous_revenue:
                # Обновляем вспомогательные показатели для истории.
                previous_entry["participants"] = str(metrics.participants)
                previous_entry["updated_at"] = str(int(time.time()))
                state_changed = True
                continue
            target_income = income_goals.get(race_id)
            message = _build_message(previous_revenue, metrics.revenue, metrics, target=target_income)
            LOGGER.info(
                "Доход гонки %s изменился: %s ₽ -> %s ₽.",
                race_id,
                format_money(previous_revenue),
                format_money(metrics.revenue),
            )
            _send_notification(bot_token, admin_ids, message)
            state[race_id] = {
                "revenue": current_value,
                "participants": str(metrics.participants),
                "updated_at": str(int(time.time())),
            }
            state_changed = True

        if state_changed:
            try:
                _write_state(state_path, state)
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.error("Не удалось записать состояние %s: %s", state_path, exc)

        if stop_requested:
            break
        elapsed = time.monotonic() - start_ts
        sleep_for = max(1.0, interval - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run_monitor()
