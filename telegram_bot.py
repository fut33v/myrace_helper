#!/usr/bin/env python3
"""Telegram bot for creating MyRace promo codes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import sys
import time
from html import escape
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple
from collections import deque
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup  # type: ignore
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

BASE_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = BASE_DIR / "create_promo_codes.py"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RACE_ID = os.getenv("MYRACE_RACE_ID", "1440")
COUPON_TYPE = os.getenv("MYRACE_COUPON_TYPE", "На определенную дистанцию")
DEFAULT_SLOT_VALUE = os.getenv("MYRACE_SLOT_VALUE", "all")
DEFAULT_USAGE_LIMIT = int(os.getenv("MYRACE_USAGE_LIMIT", "1"))
DEFAULT_STEP_DELAY = os.getenv("MYRACE_STEP_DELAY")

COOKIES_PATH = os.getenv("MYRACE_COOKIES_PATH", "myrace_cookies.txt")
RACES_STORE_PATH = Path(os.getenv("MYRACE_RACES_PATH", "races.json"))
MAX_RACE_BUTTONS = int(os.getenv("MYRACE_RACE_BUTTONS", "12"))
MAX_PROMO_PAGES = int(os.getenv("MYRACE_MAX_PAGES", "30"))
PROMO_LIST_URLS = [
    "https://myrace.info/promo/races/{race_id}/slots",
    "https://myrace.info/promo/races/{race_id}",
    "https://myrace.info/race/coupons/list/{race_id}",
    "https://myrace.info/races/{race_id}/coupons/",
    "https://myrace.info/races/{race_id}/coupons/items/",
]
PROMO_STATUS_FILTERS = [
    "all",
]
PROMO_TYPE_SLUGS = {
    "distance",
    "distance_with_bib",
}
HX_HEADERS = {
    "HX-Request": "true",
    "X-Requested-With": "XMLHttpRequest",
}
_admin_env = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
ADMIN_IDS = {
    int(part)
    for part in _admin_env.split(",")
    if part.strip().lstrip("-").isdigit()
}

_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)
logger.setLevel(_log_level)


def _build_command(code: str, discount: int, usage_limit: int, race_id: str) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        str(SCRIPT_PATH),
        "--codes",
        code,
        "--discount",
        str(discount),
        "--usage-limit",
        str(usage_limit),
        "--slot-value",
        DEFAULT_SLOT_VALUE,
        "--coupon-type",
        COUPON_TYPE,
        "--race-id",
        race_id,
        "--headless",
    ]

    if COOKIES_PATH:
        cmd.extend(["--cookies", COOKIES_PATH])
    if DEFAULT_STEP_DELAY:
        cmd.extend(["--step-delay", DEFAULT_STEP_DELAY])
    return cmd


async def _run_command(cmd: List[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode("utf-8", "ignore")
    stderr = stderr_bytes.decode("utf-8", "ignore")
    return process.returncode, stdout, stderr


def _current_race_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    value = context.chat_data.get("race_id") or RACE_ID
    return str(value)


async def _handle_create(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    code: str,
    discount: int,
    usage_limit: int,
) -> None:
    user = update.effective_user
    user_id = user.id if user else None
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ пошел на хуй пидарас")
        logger.warning("User %s attempted to create promo without permissions", user_id)
        return
    cookies_file = Path(COOKIES_PATH)
    if not cookies_file.exists():
        await update.message.reply_text(
            "⚠️ Cookie-файл не найден. Сначала выполните /setcookies с актуальными данными."
        )
        return
    await update.message.reply_text(
        f"⏳ Создаю промокод {code} (скидка {discount}%, ограничение {usage_limit})…"
    )
    race_id = _current_race_id(context)
    cmd = _build_command(code, discount, usage_limit, race_id)
    logger.info("Executing: %s", " ".join(shlex.quote(part) for part in cmd))
    returncode, stdout, stderr = await _run_command(cmd)

    if returncode == 0:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        actual_code = None
        filtered: List[str] = []
        for line in lines:
            if line.startswith("ACTUAL_CODE:"):
                actual_code = line.split(":", 1)[1].strip()
            else:
                filtered.append(line)
        if actual_code:
            if filtered:
                await update.message.reply_text("\n".join(filtered))
            from html import escape as _html_escape
            await update.message.reply_text(
                f"🎉 Промокод создан: <code>{_html_escape(actual_code)}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            message = "\n".join(filtered) if filtered else "✅ Готово."
            await update.message.reply_text(message)
    else:
        combined = (stderr.strip() or stdout.strip() or "Неизвестная ошибка")
        await update.message.reply_text(
            f"❌ Ошибка при создании промокода {code} (exit {returncode}):\n{combined}"
        )


def _parse_args(args: List[str], expected: int, optional: int = 0) -> Optional[List[str]]:
    if len(args) < expected or len(args) > expected + optional:
        return None
    return args


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Привет! Я помогу создать промокоды MyRace.\n\n"
        "📋 Команды:\n"
        "• /promo100 <код> [лимит] — скидка 100%, лимит по умолчанию 1.\n"
        "• /promo <код> <скидка> [лимит] — произвольные значения.\n"
        "• /checkpromos — показать промокоды с оставшимся лимитом.\n"
        f"🍪 Используются cookies из {COOKIES_PATH}. Тип по умолчанию: {COUPON_TYPE}."
    )
    await update.message.reply_text(text)



async def promo100(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _parse_args(context.args, expected=1, optional=1)
    if args is None:
        await update.message.reply_text("ℹ️ Использование: /promo100 <код> [лимит]")
        return
    code = args[0]
    usage_limit = DEFAULT_USAGE_LIMIT
    if len(args) == 2:
        try:
            usage_limit = max(1, int(args[1]))
        except ValueError:
            await update.message.reply_text("⚠️ Лимит должен быть числом.")
            return
    await _handle_create(update, context, code, discount=100, usage_limit=usage_limit)


async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _parse_args(context.args, expected=2, optional=1)
    if args is None:
        await update.message.reply_text("ℹ️ Использование: /promo <код> <скидка> [лимит]")
        return
    code = args[0]
    try:
        discount = int(args[1])
    except ValueError:
        await update.message.reply_text("⚠️ Скидка должна быть числом.")
        return
    usage_limit = DEFAULT_USAGE_LIMIT
    if len(args) == 3:
        try:
            usage_limit = max(1, int(args[2]))
        except ValueError:
            await update.message.reply_text("⚠️ Лимит должен быть числом.")
            return
    await _handle_create(update, context, code, discount=discount, usage_limit=usage_limit)


def _load_cookies() -> MozillaCookieJar:
    jar_path = Path(COOKIES_PATH)
    if not jar_path.exists():
        raise FileNotFoundError(f"Файл cookies {jar_path} не найден")
    jar = MozillaCookieJar(str(jar_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _fetch_races() -> List[Tuple[str, str]]:
    session = requests.Session()
    session.cookies = _load_cookies()
    races: List[Tuple[str, str]] = []
    seen = set()

    for race_id, title in _load_manual_races():
        if race_id not in seen:
            races.append((race_id, title))
            seen.add(race_id)

    target = "https://myrace.info/race/list"
    parsed_any = False
    for attempt in range(2):
        try:
            response = session.get(target, timeout=30)
            response.raise_for_status()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Не удалось загрузить список гонок (попытка %s): %s", attempt + 1, exc)
            if attempt == 0:
                continue
            return races

        if response.url.rstrip('/') != target.rstrip('/') and attempt == 0:
            logger.info("Получен ответ с URL %s, пробуем повторно запросить список гонок.", response.url)
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        new_items = 0
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            match = re.search(r"/races/(\d+)", href)
            if not match:
                match = re.search(r"/entities/races/(\d+)", href)
            if not match:
                continue
            race_id = match.group(1)
            if race_id in seen:
                continue
            title = tag.get_text(strip=True)
            if not title or _looks_like_placeholder(title):
                fetched = _fetch_race_title(session, race_id)
                if fetched:
                    title = fetched
            if not title:
                continue
            races.append((race_id, title))
            seen.add(race_id)
            new_items += 1
        if new_items == 0 and attempt == 0:
            logger.info("Список гонок пуст, пробуем повторно запросить страницу.")
            continue
        parsed_any = parsed_any or new_items > 0
        break

    if not parsed_any:
        logger.warning("Список гонок с сайта получить не удалось, используем сохранённые значения.")
    return races


def _format_races_response(
    races_list: List[Tuple[str, str]],
    current: str,
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    lines = ["🏁 Доступные гонки:"]
    for race_id, title in races_list[:50]:
        marker = " ⭐️" if race_id == current else ""
        lines.append(f"• {race_id}: {title}{marker}")

    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for race_id, title in races_list[:MAX_RACE_BUTTONS]:
        prefix = "⭐️" if race_id == current else "🏁"
        display = title if len(title) <= 20 else f"{title[:17]}…"
        label = f"{prefix} {race_id} · {display}"
        button = InlineKeyboardButton(label, callback_data=f"race:{race_id}")
        row.append(button)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(lines), markup


async def races(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        races_list = await asyncio.to_thread(_fetch_races)
    except Exception as exc:  # pylint: disable=broad-except
        await update.message.reply_text(f"❌ Не удалось получить список гонок: {exc}")
        return

    if not races_list:
        await update.message.reply_text("⚠️ Список гонок пуст или недоступен.")
        return

    current = _current_race_id(context)
    context.chat_data["races_last_list"] = races_list
    text, markup = _format_races_response(races_list, current)
    await update.message.reply_text(text, reply_markup=markup)


async def handle_race_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    if not data.startswith("race:"):
        await query.answer()
        return

    race_id = data.split(":", 1)[1]
    context.chat_data["race_id"] = race_id

    races_list = context.chat_data.get("races_last_list")
    title: Optional[str] = None
    if isinstance(races_list, list):
        for rid, name in races_list:
            if rid == race_id:
                title = name
                break
    else:
        races_list = None

    if races_list is None:
        try:
            races_list = await asyncio.to_thread(_fetch_races)
            context.chat_data["races_last_list"] = races_list
            for rid, name in races_list:
                if rid == race_id:
                    title = name
                    break
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Не удалось обновить список гонок: %s", exc)
            races_list = None

    current = _current_race_id(context)
    if query.message and races_list:
        text, markup = _format_races_response(races_list, current)
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Не удалось обновить сообщение списка гонок: %s", exc)

    toast = f"🏁 Выбрана гонка {race_id}"
    if title:
        short_title = title if len(title) <= 40 else f"{title[:37]}…"
        toast = f"🏁 {race_id} — {short_title}"
    await query.answer(text=toast)


async def setrace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("ℹ️ Использование: /setrace <id>")
        return
    race_id = context.args[0].strip()
    if not race_id.isdigit():
        await update.message.reply_text("⚠️ ID гонки должен быть числом.")
        return
    context.chat_data["race_id"] = race_id
    await update.message.reply_text(f"✅ Текущая гонка установлена: {race_id}.")


async def add_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else None
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ пошел на хуй пидарас")
        logger.warning("User %s attempted to add race without permissions", user_id)
        return
    if not context.args:
        await update.message.reply_text("ℹ️ Использование: /addrace https://myrace.info/events/<id>")
        return
    url = context.args[0].strip()

    match = re.search(r"(\d+)", url)
    if not match:
        await update.message.reply_text("⚠️ Не удалось определить ID гонки из ссылки.")
        return
    race_id = match.group(1)

    try:
        session = requests.Session()
        session.cookies = _load_cookies()
        response = session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else f"Гонка {race_id}"
    except Exception as exc:  # pylint: disable=broad-except
        await update.message.reply_text(f"❌ Не удалось загрузить страницу гонки: {exc}")
        return

    races = _load_manual_races()
    if all(existing_id != race_id for existing_id, _ in races):
        races.append((race_id, title))
        _save_manual_races(races)
        await update.message.reply_text(f"✅ Гонка добавлена: {race_id} — {title}")
    else:
        await update.message.reply_text(f"ℹ️ Гонка {race_id} уже есть в списке.")


async def checkpromos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else None
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ пошел на хуй пидарас")
        logger.warning("User %s attempted to inspect promos without permissions", user_id)
        return

    cookies_file = Path(COOKIES_PATH)
    if not cookies_file.exists():
        await update.message.reply_text(
            "⚠️ Cookie-файл не найден. Сначала выполните /setcookies с актуальными данными."
        )
        return

    race_id = _current_race_id(context)
    progress_message = await update.message.reply_text(
        f"🔍 Проверяю промокоды для гонки {race_id}…"
    )

    async def _set_progress(text: str) -> None:
        try:
            await progress_message.edit_text(text)
        except Exception as exc:  # pragma: no cover
            logger.debug("Не удалось обновить сообщение прогресса: %s", exc)

    loop = asyncio.get_running_loop()
    progress_state = {"last": 0.0}

    async def _edit_progress(step: int, pending: int, current_url: str) -> None:
        total = step + pending
        lines = [f"🔄 Загружаю промокоды ({step}{'/' + str(total) if total else ''})"]
        if current_url:
            lines.append(f"Последний ответ: {current_url}")
        lines.append("Это может занять пару минут…")
        await _set_progress("\n".join(lines))

    def progress_cb(step: int, pending: int, current_url: str) -> None:
        now = time.monotonic()
        if now - progress_state["last"] < 0.5:
            return
        progress_state["last"] = now
        loop.call_soon_threadsafe(
            asyncio.create_task,
            _edit_progress(step, pending, current_url),
        )

    try:
        promos = await asyncio.to_thread(_gather_promos_with_usage, race_id, progress_cb)
    except FileNotFoundError:
        await _set_progress("⚠️ Cookie-файл не найден.")
        await update.message.reply_text(
            "⚠️ Cookie-файл не найден. Сначала выполните /setcookies с актуальными данными."
        )
        return
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Не удалось получить список промокодов")
        await _set_progress("❌ Ошибка при загрузке промокодов.")
        await update.message.reply_text(f"❌ Не удалось получить список промокодов: {exc}")
        return

    if not promos:
        await _set_progress("ℹ️ Промокоды не найдены.")
        await update.message.reply_text("ℹ️ Не найдено промокодов для этой гонки.")
        return

    logger.debug(
        "Получено %s промокодов (до фильтрации): %s",
        len(promos),
        [(info.code, info.usage_left) for info in promos[:30]],
    )
    await _set_progress(f"📦 Найдено {len(promos)} промокодов, фильтрую…")

    active: List[PromoUsageInfo] = [
        info for info in promos if info.usage_left is None or info.usage_left != 0
    ]
    skipped_zero = [info for info in promos if info.usage_left == 0]
    if skipped_zero:
        logger.debug(
            "Отфильтровано по нулевому лимиту: %s",
            [(info.code, info.url) for info in skipped_zero],
        )
    if not active:
        await _set_progress("✅ Все промокоды израсходованы.")
        await update.message.reply_text("✅ Все промокоды израсходованы.")
        return

    def _code_text(info: PromoUsageInfo) -> str:
        return (info.code or _extract_code_from_url(info.url)).strip()

    def _sort_key(info: PromoUsageInfo) -> Tuple[int, str]:
        usage = info.usage_left
        usage_key = usage if usage is not None else -1
        return (usage_key, _code_text(info).lower())

    sorted_active = sorted(active, key=_sort_key, reverse=True)

    grouped: dict[Optional[int], List[PromoUsageInfo]] = {}
    for info in sorted_active:
        grouped.setdefault(info.discount_percent, []).append(info)

    ordered_keys = sorted([k for k in grouped.keys() if k is not None], reverse=True)
    if None in grouped:
        ordered_keys.append(None)

    def _discount_header(percent: Optional[int]) -> str:
        if percent is None:
            return "❔ Скидка неизвестна"
        if percent >= 100:
            icon = "💯"
        elif percent >= 70:
            icon = "🎯"
        else:
            icon = "🔹"
        return f"{icon} Скидка {percent}%"

    summary_totals: List[Tuple[Optional[int], int, int, int]] = []

    for key in ordered_keys:
        group_items = grouped.get(key, [])
        if not group_items:
            continue
        lines = [_discount_header(key) + ":"]
        unknown_count = 0
        for info in group_items:
            code_display = _code_text(info)
            url_html = escape(info.url)
            code_html = escape(code_display)
            usage = info.usage_left
            if usage is None:
                lines.append(f"• <a href=\"{url_html}\">{code_html}</a> — лимит не удалось определить")
                unknown_count += 1
                continue
            lines.append(f"• <a href=\"{url_html}\">{code_html}</a>: осталось {usage}")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        known_usage = sum(info.usage_left or 0 for info in group_items if info.usage_left is not None)
        summary_totals.append((key, len(group_items), known_usage, unknown_count))

    total_known_usage = sum(entry[2] for entry in summary_totals)
    total_codes = sum(entry[1] for entry in summary_totals)
    total_unknown = sum(entry[3] for entry in summary_totals)

    summary_lines = [
        "🧮 Итог по промокодам:",
        f"Всего кодов: {total_codes}, мест: {total_known_usage}, без данных: {total_unknown}",
    ]
    for key, count, usage_sum, unknown_count in summary_totals:
        label = _discount_header(key)
        line = f"{label}: кодов {count}, мест {usage_sum}"
        if unknown_count:
            line += f", без данных {unknown_count}"
        summary_lines.append(line)

    await update.message.reply_text(
        "\n".join(summary_lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    await _set_progress(f"✅ Готово! Доступных промокодов: {total_codes}")


def _cookies_to_netscape(cookies: Iterable[dict]) -> List[str]:
    lines: List[str] = [
        "# Netscape HTTP Cookie File",
        "# Generated by telegram bot",
    ]
    for item in cookies:
        domain = str(item.get("domain", "")).strip()
        if not domain:
            continue
        host_only = bool(item.get("hostOnly"))
        tailmatch = "FALSE" if host_only else "TRUE"
        path = item.get("path") or "/"
        secure_flag = "TRUE" if item.get("secure") else "FALSE"
        expiration = item.get("expirationDate")
        if item.get("session") or expiration is None:
            expires = "0"
        else:
            try:
                expires = str(int(float(expiration)))
            except (ValueError, TypeError):
                expires = "0"
        name = item.get("name")
        value = item.get("value", "")
        if not name:
            continue
        if host_only:
            domain_output = domain
        else:
            domain_output = domain if domain.startswith(".") else f".{domain}"
        line = "\t".join(
            [domain_output, tailmatch, path, secure_flag, expires, str(name), str(value)]
        )
        lines.append(line)
    return lines


SETCOOKIE_PENDING_KEY = "setcookies_pending"


@dataclass
class PromoUsageInfo:
    code: Optional[str]
    usage_left: Optional[int]
    url: str
    discount_percent: Optional[int] = None


def _load_manual_races() -> List[Tuple[str, str]]:
    if not RACES_STORE_PATH.exists():
        return []
    try:
        data = json.loads(RACES_STORE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Не удалось разобрать %s, игнорируем", RACES_STORE_PATH)
        return []
    races: List[Tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        race_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        if race_id and title:
            races.append((race_id, title))
    return races


def _save_manual_races(races: List[Tuple[str, str]]) -> None:
    payload = [{"id": race_id, "title": title} for race_id, title in races]
    RACES_STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def _looks_like_placeholder(title: str) -> bool:
    stripped = title.strip()
    if not stripped:
        return True
    return all(ch.isdigit() or ch in "-./ " for ch in stripped)


def _fetch_race_title(session: requests.Session, race_id: str) -> Optional[str]:
    url = f"https://myrace.info/events/{race_id}"
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Не удалось загрузить страницу гонки %s: %s", race_id, exc)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        return title_tag.get_text(strip=True)
    return None


USAGE_LABELS = [
    "Максимальное количество использования",
    "Максимальное количество использований",
    "Maximum number of use",
    "Maximum number of uses",
]


def _extract_first_int(text: str) -> Optional[int]:
    if text is None:
        return None
    cleaned = text.replace("\xa0", " ")
    match = re.search(r"-?\d+", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _extract_code_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"id": "code"}, {"name": "code"}):
        field = soup.find("input", attrs=attrs)
        if field:
            value = field.get("value", "").strip()
            if value:
                return value
    anchor = soup.select_one("table.items td.text-strong a[href*='/promo/view/']")
    if anchor:
        text_value = anchor.get_text(strip=True)
        if text_value and text_value.upper() != "MYRACE":
            return text_value
    for candidate in soup.find_all(string=True):
        stripped = candidate.strip()
        if not stripped:
            continue
        if not re.fullmatch(r"[A-Z0-9-]{4,16}", stripped):
            continue
        if stripped.upper() == "MYRACE":
            continue
        return stripped
    return None


def _extract_usage_value(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    for label in USAGE_LABELS:
        node = soup.find(string=re.compile(re.escape(label), re.IGNORECASE))
        if not node:
            continue
        element = node.parent
        if not element:
            continue
        row = element.find_parent("tr")
        if row:
            cells = row.find_all(["td", "th", "dd"])
            if len(cells) >= 2:
                value_text = cells[-1].get_text(" ", strip=True)
                value = _extract_first_int(value_text)
                if value is not None:
                    return value
        if element.name == "dt":
            dd = element.find_next_sibling("dd")
            if dd:
                value = _extract_first_int(dd.get_text(" ", strip=True))
                if value is not None:
                    return value
        parent_dt = element.find_parent("dt")
        if parent_dt:
            dd = parent_dt.find_next_sibling("dd")
            if dd:
                value = _extract_first_int(dd.get_text(" ", strip=True))
                if value is not None:
                    return value
        for sibling in element.next_siblings:
            if isinstance(sibling, str):
                text = sibling.strip()
            else:
                text = sibling.get_text(" ", strip=True)
            if not text:
                continue
            value = _extract_first_int(text)
            if value is not None:
                return value
        candidate = element.find_next(string=re.compile(r"\d"))
        if candidate:
            value = _extract_first_int(candidate.strip())
            if value is not None:
                return value
    return None


def _extract_code_from_url(url: str) -> str:
    match = re.search(r"/promo/view/(\d+)", url)
    if match:
        return f"promo-{match.group(1)}"
    return url


def _collect_promo_view_links(
    session: requests.Session,
    race_id: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, Optional[str], Optional[int]]]:
    Task = Tuple[str, str, Optional[Tuple[Tuple[str, str], ...]]]
    queue: deque[Task] = deque()
    visited: set[Task] = set()
    results_order: List[str] = []
    results_map: dict[str, Tuple[Optional[str], Optional[int]]] = {}

    def _normalize_data(data: Optional[dict[str, str]]) -> Optional[Tuple[Tuple[str, str], ...]]:
        if not data:
            return None
        return tuple(sorted((str(k), str(v)) for k, v in data.items()))

    def _enqueue(method: str, url: str, data: Optional[dict[str, str]] = None) -> None:
        normalized = _normalize_data(data)
        queue.append((method.upper(), url, normalized))
        logger.debug("Очередь➕ %s %s payload=%s", method.upper(), url, normalized)

    for template in PROMO_LIST_URLS:
        base_url = template.format(race_id=race_id)
        _enqueue("GET", base_url)
        for page in range(1, MAX_PROMO_PAGES + 1):
            separator = "&" if "?" in base_url else "?"
            _enqueue("GET", f"{base_url}{separator}page={page}")
        if "/coupons/" in base_url:
            for page in range(1, MAX_PROMO_PAGES + 1):
                _enqueue("POST", f"https://myrace.info/races/{race_id}/coupons/pages/{page}/")
            for page in range(1, MAX_PROMO_PAGES + 1):
                _enqueue("POST", f"https://myrace.info/races/{race_id}/coupons/items/", data={"page": str(page)})

    default_variants: set[str] = set()
    for slug in PROMO_TYPE_SLUGS:
        base_slots = f"https://myrace.info/promo/races/{race_id}/slots?type={slug}"
        variants = {
            base_slots,
            f"https://myrace.info/promo/races/{race_id}?type={slug}",
            f"https://myrace.info/races/{race_id}/coupons/?type={slug}",
            f"https://myrace.info/races/{race_id}/coupons/items/?type={slug}",
        }
        for status in PROMO_STATUS_FILTERS:
            variants.add(f"{base_slots}&status={status}")
            variants.add(f"https://myrace.info/races/{race_id}/coupons/items/?type={slug}&status={status}")
        for variant in list(variants):
            if variant not in default_variants:
                default_variants.add(variant)
                _enqueue("GET", variant)
            for page in range(1, MAX_PROMO_PAGES + 1):
                separator = "&" if "?" in variant else "?"
                _enqueue("GET", f"{variant}{separator}page={page}")

    request_count = 0
    max_requests = max(80, len(queue) * 2)

    while queue and request_count < max_requests:
        method, url, payload = queue.popleft()
        task_key = (method, url, payload)
        if task_key in visited:
            logger.debug("Очередь↻ пропускаем %s %s", method, url)
            continue
        visited.add(task_key)
        request_count += 1
        logger.debug("Запрос #%s: %s %s payload=%s", request_count, method, url, payload)

        data_dict = {k: v for k, v in payload} if payload else {}
        try:
            if method == "POST":
                response = session.post(url, data=data_dict, headers=HX_HEADERS, timeout=30)
            else:
                response = session.get(url, headers=HX_HEADERS, timeout=30)
            response.raise_for_status()
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Не удалось получить список промокодов по адресу %s (%s): %s", url, method, exc)
            continue

        try:
            text_plain = response.text
        except Exception:  # pragma: no cover
            text_plain = ""
        text_unescaped = text_plain.replace("\\/", "/")
        logger.debug("Ответ %s %s: %s байт", method, response.url, len(text_plain))
        soup = BeautifulSoup(text_plain, "html.parser")
        base = response.url
        found: List[Tuple[str, Optional[str], Optional[int]]] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if "/promo/view/" not in href:
                if ("page=" in href or "type=" in href) and "/promo/races/" in href:
                    page_url = urljoin(base, href)
                    if f"/races/{race_id}" in page_url:
                        logger.debug("─▶ обнаружили пагинацию %s", page_url)
                        _enqueue("GET", page_url)
                continue
            full = urljoin(base, href)
            text = tag.get_text(strip=True) or None
            discount = None
            row = tag.find_parent("tr")
            if row:
                cells = row.find_all("td")
                if len(cells) >= 3:
                    discount_value = _extract_first_int(cells[2].get_text(strip=True))
                    if discount_value is not None:
                        discount = discount_value
            found.append((full, text, discount))
        for attr in ("data-url", "data-href", "data-action"):
            for tag in soup.find_all(attrs={attr: True}):
                value = tag.get(attr)
                if not value or "/promo/view/" not in value:
                    continue
                full = urljoin(base, value.replace("\\/", "/"))
                text = tag.get_text(strip=True) or None
                found.append((full, text, None))
        for pattern_source in (text_plain, text_unescaped):
            for match in re.finditer(r"/promo/view/\d+(?:\?[^\s\"'>]*)?", pattern_source):
                full = urljoin(base, match.group(0))
                found.append((full, None, None))
            for match in re.finditer(r'"(?:viewUrl|view_url)"\s*:\s*"([^"]+)"', pattern_source):
                href = match.group(1)
                if "/promo/view/" not in href:
                    continue
                full = urljoin(base, href)
                found.append((full, None, None))
            for match in re.finditer(r"'(?:viewUrl|view_url)'\s*:\s*'([^']+)'", pattern_source):
                href = match.group(1)
                if "/promo/view/" not in href:
                    continue
                full = urljoin(base, href)
                found.append((full, None, None))

        for match in re.finditer(r"promoViewUrl\s*=\s*['\"]([^'\"]+)['\"]", text_unescaped):
            href = match.group(1)
            if "/promo/view/" not in href:
                continue
            full = urljoin(base, href)
            found.append((full, None, None))

        hx_attrs = ["hx-get", "hx-post", "data-hx-get", "data-hx-post"]
        for attr in hx_attrs:
            for tag in soup.find_all(attrs={attr: True}):
                raw = tag.get(attr)
                if not raw:
                    continue
                hx_url = urljoin(base, raw.replace("\\/", "/"))
                if f"/races/{race_id}" not in hx_url and "/promo/" not in hx_url:
                    continue
                hx_method = "POST" if "post" in attr.lower() else "GET"
                hx_data: Optional[dict[str, str]] = None
                if hx_method == "POST" and tag.name == "form":
                    form_data: dict[str, str] = {}
                    for input_tag in tag.find_all("input"):
                        name = input_tag.get("name")
                        if not name:
                            continue
                        input_type = (input_tag.get("type") or "text").lower()
                        if input_type in {"checkbox", "radio"} and not input_tag.has_attr("checked"):
                            continue
                        form_data[name] = input_tag.get("value", "")
                    for select in tag.find_all("select"):
                        name = select.get("name")
                        if not name:
                            continue
                        option = select.find("option", selected=True) or select.find("option")
                        if option:
                            form_data[name] = option.get("value") or option.text
                    hx_data = form_data or {}
                logger.debug("htmx %s %s payload=%s", hx_method, hx_url, hx_data)
                _enqueue(hx_method, hx_url, hx_data)

        for full, text, discount in found:
            if f"/races/{race_id}" not in full and f"/promo/view/" not in full:
                continue
            if full not in results_map:
                results_order.append(full)
                results_map[full] = (text, discount)
                logger.debug("Добавлена ссылка на промо: %s (%s)", full, text)
            else:
                prev_text, prev_discount = results_map[full]
                new_text = prev_text or text
                new_discount = prev_discount if prev_discount is not None else discount
                if new_text != prev_text or new_discount != prev_discount:
                    logger.debug(
                        "Обновлено промо %s: text=%s discount=%s",
                        full,
                        new_text,
                        new_discount,
                    )
                results_map[full] = (new_text, new_discount)

        if progress_cb:
            try:
                progress_cb(request_count, len(queue), response.url)
            except Exception as exc:  # pragma: no cover
                logger.debug("Ошибка progress_cb: %s", exc)

    summary = (
        f"Сбор ссылок завершён: найдено {len(results_order)}, "
        f"выполнено запросов {request_count}, осталось в очереди {len(queue)}"
    )
    if len(results_order) == 0:
        logger.warning(summary)
    else:
        logger.debug(summary)

    if progress_cb:
        try:
            progress_cb(request_count, len(queue), "")
        except Exception as exc:  # pragma: no cover
            logger.debug("Ошибка progress_cb: %s", exc)

    return [(link, results_map[link][0], results_map[link][1]) for link in results_order]


def _gather_promos_with_usage(
    race_id: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[PromoUsageInfo]:
    session = requests.Session()
    session.cookies = _load_cookies()
    links = _collect_promo_view_links(session, race_id, progress_cb=progress_cb)
    if not links:
        logger.error("Промокоды не найдены для гонки %s", race_id)
        try:
            fallback_resp = session.get(PROMO_LIST_URLS[0].format(race_id=race_id), timeout=30)
            logger.error(
                "Тело fallback-ответа (500 байт): %s",
                fallback_resp.text[:500] if fallback_resp.text else "<пусто>",
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Не удалось получить fallback-страницу: %s", exc)
        raise RuntimeError("Не удалось найти ссылки на промокоды для этой гонки.")
    results: List[PromoUsageInfo] = []
    for view_url, anchor_text, discount in links:
        try:
            response = session.get(view_url, timeout=30)
            response.raise_for_status()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Не удалось загрузить промокод %s: %s", view_url, exc)
            results.append(PromoUsageInfo(code=anchor_text, usage_left=None, url=view_url))
            continue
        html = response.text
        logger.debug("Страница промокода %s: %s байт", response.url, len(html))
        code = _extract_code_from_html(html) or anchor_text or _extract_code_from_url(view_url)
        usage = _extract_usage_value(html)
        logger.debug("Промокод разобран: code=%s usage=%s url=%s", code, usage, response.url)
        results.append(PromoUsageInfo(code=code, usage_left=usage, url=view_url, discount_percent=discount))
    return results

async def setcookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[SETCOOKIE_PENDING_KEY] = True
    await update.message.reply_text(
        "🍪 Пришлите cookies отдельным сообщением в формате JSON (в том же формате, что экспорт из браузера)."
    )


async def ingest_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.get(SETCOOKIE_PENDING_KEY)
    if not pending or not update.message or (update.message.text or "").startswith("/"):
        return

    json_text = update.message.text.strip()
    if not json_text:
        return

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        await update.message.reply_text(f"❌ Не удалось разобрать JSON: {exc}")
        return
    if isinstance(data, dict) and "cookies" in data:
        cookies = data["cookies"]
    elif isinstance(data, list):
        cookies = data
    else:
        await update.message.reply_text("⚠️ Ожидался массив объектов cookie.")
        return
    if not isinstance(cookies, list):
        await update.message.reply_text("⚠️ Поле cookies должно быть массивом.")
        return
    lines = _cookies_to_netscape(cookies)
    if len(lines) <= 2:
        await update.message.reply_text("⚠️ Не удалось получить ни одного cookie.")
        return
    cookies_path = Path(COOKIES_PATH)
    cookies_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    await update.message.reply_text(f"✅ Cookies сохранены в {cookies_path}.")
    context.user_data.pop(SETCOOKIE_PENDING_KEY, None)


def main() -> None:
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан в окружении.", file=sys.stderr)
        sys.exit(1)
    if not SCRIPT_PATH.exists():
        print(f"Не найден create_promo_codes.py по пути {SCRIPT_PATH}", file=sys.stderr)
        sys.exit(1)

    application: Application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("promo100", promo100))
    application.add_handler(CommandHandler("promo", promo))
    application.add_handler(CommandHandler("races", races))
    application.add_handler(CommandHandler("setrace", setrace))
    application.add_handler(CommandHandler("addrace", add_race))
    application.add_handler(CommandHandler("checkpromos", checkpromos))
    application.add_handler(CommandHandler("setcookies", setcookies))
    application.add_handler(CommandHandler("cancelcookies", start))
    application.add_handler(CallbackQueryHandler(handle_race_callback, pattern=r"^race:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ingest_cookies))

    logger.info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
