#!/usr/bin/env python3
"""Shared helpers for fetching revenue/participants of MyRace races."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup  # type: ignore

SUMMARY_URL = "https://myrace.info/entities/races/{race_id}"
HOME_URL = "https://myrace.info/"
DEFAULT_FETCH_RETRIES = 3
RETRY_DELAY = 1.0


@dataclass
class RaceMetrics:
    race_id: str
    title: str
    participants: int
    revenue: Decimal


def _normalize_number(value: str) -> str:
    normalized = value.replace("\xa0", " ").replace(",", ".")
    allowed = "".join(ch for ch in normalized if ch.isdigit() or ch in ".- ")
    return allowed.replace(" ", "")


def _parse_revenue(value: str) -> Decimal:
    candidate = _normalize_number(value)
    if not candidate:
        raise ValueError("Не удалось извлечь числовое значение дохода.")
    try:
        amount = Decimal(candidate)
    except InvalidOperation as exc:  # pragma: no cover
        raise ValueError(f"Некорректный формат дохода: {value}") from exc
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_participants(value: str) -> int:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError(f"Не удалось извлечь число участников из '{value}'.")
    return int(digits)


def _collect_stat_pairs(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for item in soup.select("div.list-item"):
        cells = item.find_all("div", recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        value = cells[1].get_text(strip=True)
        if not label or not value:
            continue
        pairs.append((label, value))
    return pairs


def _extract_metric(pairs: Iterable[Tuple[str, str]], keyword: str) -> Optional[str]:
    for label, value in pairs:
        if keyword in label.lower():
            return value
    return None


def format_money(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{quantized:,.2f}".replace(",", " ")
    if text.endswith(".00"):
        text = text[:-3]
    return text


def _looks_like_login(resp: requests.Response) -> bool:
    candidate_url = resp.url.lower()
    return "/login" in candidate_url or "/account/login" in candidate_url


def fetch_race_metrics(
    session: requests.Session,
    race_id: str,
    retries: int = DEFAULT_FETCH_RETRIES,
    retry_delay: float = RETRY_DELAY,
) -> RaceMetrics:
    url = SUMMARY_URL.format(race_id=race_id)
    expected_url = url.split("?", 1)[0].rstrip("/")
    last_error: Optional[str] = None

    for attempt in range(retries):
        response = session.get(url, timeout=60)
        response.raise_for_status()
        final_url = response.url.split("?", 1)[0].rstrip("/")

        login_redirect = any(_looks_like_login(prev) for prev in response.history) or _looks_like_login(response)
        if login_redirect:
            last_error = "Получена страница входа. Проверьте cookie-файл."
            try:
                session.get(HOME_URL, timeout=30)
            except Exception:
                pass
            time.sleep(retry_delay)
            continue

        if final_url != expected_url:
            last_error = (
                f"Ожидался URL {expected_url}, а пришёл {final_url}. Возможно, сессия истекла или нет доступа."
            )
            time.sleep(retry_delay)
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        pairs = _collect_stat_pairs(soup)
        participants_raw = _extract_metric(pairs, "участ")
        income_raw = _extract_metric(pairs, "доход")
        if not participants_raw or not income_raw:
            raise RuntimeError("Не удалось найти блоки с участниками или доходом.")
        participants = _parse_participants(participants_raw)
        revenue = _parse_revenue(income_raw)
        title_tag = soup.select_one(".card h2") or soup.select_one("h1")
        title = title_tag.get_text(strip=True) if title_tag else f"Гонка {race_id}"
        return RaceMetrics(race_id=race_id, title=title, participants=participants, revenue=revenue)

    raise RuntimeError(last_error or "Не удалось получить страницу гонки после повторных попыток.")
