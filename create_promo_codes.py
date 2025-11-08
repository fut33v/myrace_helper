#!/usr/bin/env python3
"""Batch creation of MyRace promo codes via Selenium."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

logger = logging.getLogger("create_promo")
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

from bs4 import BeautifulSoup  # type: ignore
from selenium.common.exceptions import NoSuchElementException, TimeoutException  # type: ignore
from selenium.webdriver.common.by import By  # type: ignore
from selenium.webdriver.support import expected_conditions as EC  # type: ignore
from selenium.webdriver.support.ui import WebDriverWait  # type: ignore

from myrace_login import build_form_payload, format_form_fields, parse_html_form
from myrace_selenium import (  # type: ignore
    add_cookies_to_driver,
    build_driver,
    export_cookies,
    fill_form_fields,
    maybe_submit_form,
    parse_field_overrides,
    read_netscape_cookies,
)

SLOTS_FORM_URL = "https://myrace.info/promo/races/{race_id}/slots/new"
COUPON_LIST_URL = "https://myrace.info/race/coupons/list/{race_id}"

TYPE_SLUGS = {
    "на определенную дистанцию": "distance",
    "at a certain distance": "distance",
    "на определенную дистанцию с выделением номера": "distance_with_bib",
    "at a certain distance with bib selection": "distance_with_bib",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Создаёт серию промокодов на MyRace через Selenium."
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        default=[f"tipacyclo{i}" for i in range(3, 8)],
        help="Список кодов, которые нужно создать (по умолчанию tipacyclo3..tipacyclo7).",
    )
    parser.add_argument(
        "--cookies",
        default="cookies/myrace_cookies.txt",
        help="Файл cookies в формате Netscape (по умолчанию cookies/myrace_cookies.txt).",
    )
    parser.add_argument(
        "--save-cookies",
        action="store_true",
        help="Сохранить cookies после завершения сессии.",
    )
    parser.add_argument(
        "--browser",
        choices=("chrome", "firefox"),
        default="chrome",
        help="Браузер для Selenium (по умолчанию chrome).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Запускать браузер в headless-режиме.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Не закрывать браузер после завершения (для отладки).",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=15,
        help="Таймаут ожидания элементов (секунды).",
    )
    parser.add_argument(
        "--race-id",
        type=int,
        default=1440,
        help="ID забега, для которого создаются промокоды (по умолчанию 1440).",
    )
    parser.add_argument(
        "--coupon-type",
        default="Скидка 100%",
        help="Название типа промокода. Можно указать несколько вариантов через '|'.",
    )
    parser.add_argument(
        "--discount",
        type=int,
        default=100,
        help="Скидка в процентах (по умолчанию 100).",
    )
    parser.add_argument(
        "--deduction",
        type=int,
        default=0,
        help="Фиксированная скидка (рубли). Для 100%% скидки оставить 0.",
    )
    parser.add_argument(
        "--usage-limit",
        type=int,
        default=1,
        help="Максимальное количество использований (по умолчанию 1).",
    )
    parser.add_argument(
        "--slot-value",
        default="all",
        help="Значение поля слотов (по умолчанию 'all'; зависит от формы).",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Переопределить значение поля формы вручную (формат имя=значение).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Заполнить форму, но не отправлять (для проверки).",
    )
    parser.add_argument(
        "--show-fields",
        action="store_true",
        help="Показать найденные поля формы перед заполнением.",
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=0.0,
        help="Пауза (в секундах) между шагами, чтобы наблюдать процесс в браузере.",
    )
    return parser.parse_args()




def extract_actual_code(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for selector in ({"id": "code"}, {"name": "code"}):
        field = soup.find("input", selector)
        if field and field.get("value"):
            value = field["value"].strip()
            if value:
                return value
    anchor = soup.select_one("table.items td.text-strong a[href*='/promo/view/']")
    if anchor and anchor.get_text():
        text_value = anchor.get_text(strip=True)
        if text_value:
            return text_value
    import re
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

def derive_overrides(
    form_fields: Dict[str, object],
    code_value: str,
    discount: int,
    deduction: int,
    usage_limit: int,
    slot_value: Optional[str],
) -> Dict[str, Union[str, List[str]]]:
    overrides: Dict[str, Union[str, List[str]]] = {}
    for name in form_fields.keys():
        lower = name.lower()
        if "authenticity" in lower:
            continue
        if any(key in lower for key in ("code", "key")):
            overrides[name] = code_value
        elif any(key in lower for key in ("name", "title", "label")):
            overrides[name] = code_value
        elif "discount" in lower or "percent" in lower:
            overrides[name] = str(discount)
        elif "deduction" in lower:
            overrides[name] = str(deduction)
        elif any(key in lower for key in ("usage", "limit", "max", "count")) and "slot" not in lower:
            overrides[name] = str(usage_limit)
        elif "slot" in lower and slot_value is not None:
            overrides[name] = slot_value
    return overrides


def click_select_all_slots(form) -> None:
    driver = getattr(form, "_parent", None)

    def _safe_click(element) -> bool:
        try:
            if driver:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.click()
            logger.debug("Clicked element %s", element)
            return True
        except Exception:
            if driver:
                try:
                    driver.execute_script("arguments[0].click();", element)
                    logger.debug("Clicked element via JS %s", element)
                    return True
                except Exception:  # pylint: disable-broad-except
                    return False
            return False

    try:
        master_checkbox = form.find_element(By.ID, "chkAll")
    except NoSuchElementException:
        try:
            master_checkbox = form.find_element(By.CSS_SELECTOR, "input#chkAll")
        except NoSuchElementException:
            logger.error("Не найден чекбокс #chkAll для выбора всех слотов.")
            return

    if master_checkbox.is_selected():
        logger.info("#chkAll уже выбран.")
        return

    logger.info("Выбираем все слоты через чекбокс #chkAll.")
    if not _safe_click(master_checkbox):
        logger.error("Не удалось кликнуть по #chkAll. Проверьте вёрстку.")

def maybe_pause(step_delay: float, label: str) -> None:
    if step_delay > 0:
        print(f"Пауза {step_delay:.1f} c: {label}")
        time.sleep(step_delay)


def ensure_authorized(driver) -> None:
    current = driver.current_url
    if "/login" in current:
        raise RuntimeError(
            "Cookies недействительны (редирект на страницу входа). Обновите их через /setcookies."
        )


def resolve_type_slug(name: str) -> Optional[str]:
    candidates = [part.strip().lower() for part in name.split("|") if part.strip()]
    for candidate in candidates:
        if candidate in TYPE_SLUGS:
            return TYPE_SLUGS[candidate]
        for alias, slug in TYPE_SLUGS.items():
            if candidate == alias or candidate in alias or alias in candidate:
                return slug
    return None


def open_slots_form(
    driver, wait: WebDriverWait, race_id: int, coupon_type_query: str, step_delay: float
) -> None:
    base_url = SLOTS_FORM_URL.format(race_id=race_id)
    slug = resolve_type_slug(coupon_type_query)
    if slug:
        target = f"{base_url}?type={slug}"
    else:
        target = base_url
        logger.warning(
            "Не удалось сопоставить тип '%s' со slug. Используем базовый URL.",
            coupon_type_query,
        )
    logger.info("Открываем форму по адресу %s", target)
    logger.info("Step: opening slots form %s", target)
    driver.get(target)
    try:
        wait.until(lambda d: d.current_url.startswith(target) or "/login" not in d.current_url)
    except TimeoutException:
        logger.warning("Таймаут ожидания при загрузке %s", target)
    current_url = driver.current_url
    logger.info("Текущий URL после загрузки: %s", current_url)

    if any(fragment in current_url for fragment in ("/events/", "/race/list")):
        logger.info(
            "Похоже, нас перенаправили на %s. Переходим через список промокодов %s.",
            current_url,
            list_url,
        )
        logger.info("Step: redirected; opening %s", list_url)
        driver.get(list_url)
        ensure_authorized(driver)
        try:
            wait.until(lambda d: "/coupons/list" in d.current_url or "/login" not in d.current_url)
        except TimeoutException:
            logger.warning("Таймаут ожидания при загрузке %s", list_url)
        logger.info("Step: retrying slots form %s", target)
        driver.get(target)
        current_url = driver.current_url
        logger.info("URL после повторного открытия: %s", current_url)

    ensure_authorized(driver)
    base_target = target.split("#")[0]
    if base_target.rstrip("/") not in current_url:
        logger.info(
            "Не удалось оказаться на странице формы (%s). Пробуем открыть её в новой вкладке.",
            target,
        )
        logger.info("Step: opening slots form in new tab %s", target)
        driver.execute_script("window.open(arguments[0], '_blank');", target)
        driver.switch_to.window(driver.window_handles[-1])
        try:
            wait.until(lambda d: "/promo/races" in d.current_url or "/login" not in d.current_url)
        except TimeoutException:
            logger.warning("Новая вкладка с %s не загрузилась вовремя.", target)
        current_url = driver.current_url
        logger.info("URL после открытия новой вкладки: %s", current_url)
        ensure_authorized(driver)
    if "/coupon/races/" in current_url and "/types" in current_url:
        logger.warning(
            "Сервер вернул страницу '/types'. Проверьте корректность slug и cookies."
        )
    maybe_pause(step_delay, f"открыта {target}")


def create_single_coupon(
    driver,
    wait: WebDriverWait,
    code_value: str,
    discount: int,
    deduction: int,
    usage_limit: int,
    slot_value: Optional[str],
    manual_overrides: Dict[str, Union[str, List[str]]],
    preview_fields: bool,
    dry_run: bool,
    step_delay: float,
) -> None:
    maybe_pause(step_delay, "перед заполнением формы")

    def _locate_form(_driver):
        forms = _driver.find_elements(By.TAG_NAME, "form")
        for candidate in forms:
            if not candidate.is_displayed():
                continue
            action = (candidate.get_attribute("action") or "").lower()
            classes = (candidate.get_attribute("class") or "").lower()
            if any(keyword in action for keyword in ("/promo", "/coupons")):
                return candidate
            if "promo" in classes:
                return candidate
            try:
                if candidate.find_elements(By.NAME, "code"):
                    return candidate
            except Exception:
                continue
        return False

    try:
        form = wait.until(_locate_form)
        logger.info("Найдена форма с action=%s", form.get_attribute("action"))
    except TimeoutException as exc:
        logger.error("Форма создания не найдена. Текущий URL: %s", driver.current_url)
        raise RuntimeError("Не удалось найти форму создания промокода.") from exc

    soup = BeautifulSoup(driver.page_source, "html.parser")
    form_info = parse_html_form(str(soup), driver.current_url)

    if preview_fields:
        print("Поля формы:")
        print(format_form_fields(form_info))

    auto = derive_overrides(
        form_info.fields,
        code_value,
        discount,
        deduction,
        usage_limit,
        slot_value,
    )
    auto.update(manual_overrides)

    payload, missing_defaults = build_form_payload(form_info, auto)
    if missing_defaults:
        print(
            "Предупреждение: обязательные поля остались пустыми: "
            + ", ".join(missing_defaults),
            file=sys.stderr,
        )

    if slot_value and slot_value.lower() == "all":
        click_select_all_slots(form)

    missing = fill_form_fields(form, payload)
    if missing:
        logger.warning("Не удалось заполнить поля: %s", ", ".join(missing))

    maybe_pause(step_delay, "перед отправкой формы")

    if dry_run:
        print(f"[dry-run] Форма для {code_value} заполнена, но не отправлена.")
        logger.info("[dry-run] Форма для %s заполнена, отправка пропущена", code_value)
        return

    maybe_submit_form(form)
    try:
        wait.until(lambda d: "promo/view" in d.current_url or "race/coupons/list" in d.current_url)
    except TimeoutException:
        logger.warning(
            "Не удалось подтвердить успешное создание %s. Текущий URL: %s",
            code_value,
            driver.current_url,
        )
        print(
            f"Предупреждение: не удалось подтвердить успешное создание {code_value}. Проверьте вручную.",
            file=sys.stderr,
        )
    else:
        actual_code = extract_actual_code(driver.page_source)
        if actual_code:
            message = f"Промокод создан: `{actual_code}`"
            logger.info(
                "Промокод %s создан успешно (URL: %s, фактический код: %s)",
                code_value,
                driver.current_url,
                actual_code,
            )
            print(message)
            print(f"ACTUAL_CODE:{actual_code}")
        else:
            message = f"Промокод {code_value} создан (не удалось определить фактический код)."
            logger.info(
                "Промокод %s создан успешно, но не удалось извлечь фактический код (URL: %s)",
                code_value,
                driver.current_url,
            )
            print(message)


def main() -> None:
    args = parse_args()
    cookies_path = Path(args.cookies).expanduser()
    driver = build_driver(args.browser, args.headless)
    wait = WebDriverWait(driver, args.wait)

    logger = logging.getLogger("create_promo")
    logger.setLevel(logging.DEBUG)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)

    try:
        logger.info("Используем cookies из %s", cookies_path)
        if not cookies_path.exists():
            raise RuntimeError(
                f"Файл cookies {cookies_path} не найден. Добавьте его через /setcookies."
            )
        cookies = read_netscape_cookies(cookies_path)
        logger.info("Загружено %d cookie", len(cookies))
        add_cookies_to_driver(driver, cookies)

        open_slots_form(driver, wait, args.race_id, args.coupon_type, args.step_delay)

        manual_overrides = parse_field_overrides(args.field)
        if manual_overrides:
            logger.info("Ручные переопределения полей: %s", manual_overrides)

        for code_value in args.codes:
            print(f"Создаём промокод {code_value}…")
            logger.info("Создаём промокод %s", code_value)
            create_single_coupon(
                driver=driver,
                wait=wait,
                code_value=code_value,
                discount=args.discount,
                deduction=args.deduction,
                usage_limit=args.usage_limit,
                slot_value=args.slot_value,
                manual_overrides=manual_overrides,
                preview_fields=args.show_fields,
                dry_run=args.dry_run,
                step_delay=args.step_delay,
            )
            time.sleep(1)

        if args.save_cookies:
            export_cookies(driver, cookies_path)
    finally:
        if not args.keep_open:
            driver.quit()


if __name__ == "__main__":
    main()
