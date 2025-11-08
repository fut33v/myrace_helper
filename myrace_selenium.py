#!/usr/bin/env python3
"""Automate MyRace login and promo code creation using Selenium."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from bs4 import BeautifulSoup  # type: ignore
from selenium import webdriver  # type: ignore
from selenium.common.exceptions import NoSuchElementException, TimeoutException  # type: ignore
from selenium.webdriver import ChromeOptions, FirefoxOptions  # type: ignore
from selenium.webdriver.common.by import By  # type: ignore
from selenium.webdriver.common.keys import Keys  # type: ignore
from selenium.webdriver.support import expected_conditions as EC  # type: ignore
from selenium.webdriver.support.ui import Select, WebDriverWait  # type: ignore
from selenium.webdriver.chrome.service import Service as ChromeService  # type: ignore
from selenium.webdriver.firefox.service import Service as FirefoxService  # type: ignore

from myrace_login import (  # type: ignore
    BASE_URL,
    LOGIN_URL,
    parse_field_overrides,
    parse_html_form,
    parse_html_forms,
    build_form_payload,
    format_form_fields,
    has_password_field,
    guess_code_field,
)


DEFAULT_COUPON_LIST = "https://myrace.info/race/coupons/list/{race_id}"
COUPON_TYPES_URL = "https://myrace.info/coupon/races/{race_id}/types"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Selenium-скрипт для входа в MyRace и создания промокодов."
    )
    parser.add_argument("--email", help="Email для входа.")
    parser.add_argument("--password", help="Пароль для входа (если требуется).")
    parser.add_argument(
        "--otp",
        help="Одноразовый код подтверждения. Если не указан, скрипт запросит его вручную.",
    )
    parser.add_argument(
        "--reuse-cookies",
        action="store_true",
        help="Загрузить cookies из файла и пропустить шаг авторизации.",
    )
    parser.add_argument(
        "--cookies",
        default="cookies/myrace_cookies.txt",
        help="Путь к файлу cookies в формате Netscape (по умолчанию cookies/myrace_cookies.txt).",
    )
    parser.add_argument(
        "--save-cookies",
        action="store_true",
        help="Сохранить cookies браузера обратно в файл после завершения.",
    )
    parser.add_argument(
        "--browser",
        choices=("chrome", "firefox"),
        default="chrome",
        help="Какой браузер использовать (по умолчанию chrome).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить браузер в headless-режиме.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Не закрывать браузер по завершении (полезно для отладки).",
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
        help="ID забега, с которым работаем.",
    )
    parser.add_argument(
        "--coupon-type",
        help="Название (или часть URL) типа промокода, который нужно создать.",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Значение полей формы создания (можно повторять). Формат: имя=значение.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не отправлять форму создания промокода, только заполнить поля.",
    )
    parser.add_argument(
        "--show-fields",
        action="store_true",
        help="Показать список полей формы перед заполнением.",
    )
    parser.add_argument(
        "--check-url",
        default="https://myrace.info/race/coupons/list/1440",
        help="URL для проверки доступности после входа.",
    )
    return parser.parse_args()


def build_driver(browser: str, headless: bool) -> webdriver.Remote:
    chrome_binary = os.getenv("CHROME_BIN")
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    gecko_binary = os.getenv("FIREFOX_BIN")
    geckodriver_path = os.getenv("GECKODRIVER_PATH")

    if browser == "chrome":
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,900")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        if chrome_binary:
            options.binary_location = chrome_binary
        if chromedriver_path and Path(chromedriver_path).exists():
            service = ChromeService(executable_path=chromedriver_path)
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)
    if browser == "firefox":
        options = FirefoxOptions()
        if headless:
            options.add_argument("-headless")
        if gecko_binary:
            options.binary_location = gecko_binary
        if geckodriver_path and Path(geckodriver_path).exists():
            service = FirefoxService(executable_path=geckodriver_path)
            return webdriver.Firefox(service=service, options=options)
        return webdriver.Firefox(options=options)
    raise ValueError(f"Неподдерживаемый браузер: {browser}")


def read_netscape_cookies(path: Path) -> List[Dict[str, Union[str, int, bool]]]:
    if not path.exists():
        raise FileNotFoundError(f"Файл cookies {path} не найден.")
    cookies: List[Dict[str, Union[str, int, bool]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                continue
            domain, tailmatch, path_value, secure_flag, expires, name, value = parts
            http_only = False
            if domain.startswith("#HttpOnly_"):
                domain = domain[len("#HttpOnly_") :]
                http_only = True
            secure = secure_flag.upper() == "TRUE"
            expiry = int(expires) if expires and expires.isdigit() else None
            host_only = tailmatch.upper() == "FALSE"
            cookies.append(
                {
                    "domain": domain.lstrip("."),
                    "path": path_value or "/",
                    "secure": secure,
                    "expiry": expiry,
                    "name": name,
                    "value": value,
                    "httpOnly": http_only,
                    "hostOnly": host_only,
                }
            )
    return cookies


def add_cookies_to_driver(driver: webdriver.Remote, cookies: List[Dict[str, Union[str, int, bool]]]) -> None:
    driver.delete_all_cookies()
    driver.get(BASE_URL)
    time.sleep(1)
    for cookie in cookies:
        data = {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie["domain"],
            "path": cookie["path"],
            "secure": bool(cookie.get("secure", False)),
        }
        expiry = cookie.get("expiry")
        if isinstance(expiry, int) and expiry > 0:
            data["expiry"] = expiry
        try:
            driver.add_cookie(data)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Не удалось добавить cookie {cookie['name']}: {exc}", file=sys.stderr)
    driver.get(BASE_URL)


def export_cookies(driver: webdriver.Remote, path: Path) -> None:
    cookies = driver.get_cookies()
    lines = [
        "# Netscape HTTP Cookie File",
        "# Exported from Selenium session",
    ]
    for cookie in cookies:
        domain = cookie.get("domain", "")
        tailmatch = "FALSE" if domain and not domain.startswith(".") else "TRUE"
        domain_output = domain if domain else "myrace.info"
        if not domain_output.startswith("."):
            domain_output = "." + domain_output
        path_value = cookie.get("path", "/")
        secure_flag = "TRUE" if cookie.get("secure") else "FALSE"
        expiry = cookie.get("expiry", 0)
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        lines.append(
            "\t".join(
                [
                    domain_output,
                    tailmatch,
                    path_value,
                    secure_flag,
                    str(expiry or 0),
                    name,
                    value,
                ]
            )
        )
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    print(f"Cookies сохранены в {path}")


def submit_form(element) -> None:
    try:
        element.submit()
        return
    except Exception:
        pass
    try:
        button = element.find_element(By.CSS_SELECTOR, "button[type='submit']")
        button.click()
        return
    except NoSuchElementException:
        pass
    element.send_keys(Keys.RETURN)


def perform_email_step(driver: webdriver.Remote, wait: WebDriverWait, email: str) -> None:
    form = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form[action='/signup']")))
    email_input = form.find_element(By.NAME, "name")
    email_input.clear()
    email_input.send_keys(email)
    submit_form(form)
    wait.until(lambda d: d.current_url != LOGIN_URL)


def maybe_fill_password(driver: webdriver.Remote, wait: WebDriverWait, email: str, password: str) -> bool:
    if not password:
        return False
    try:
        password_form = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "form[action^='/login/']"))
        )
    except TimeoutException:
        return False
    soup = BeautifulSoup(driver.page_source, "html.parser")
    forms = parse_html_forms(str(soup), driver.current_url)
    password_info = None
    for info in forms:
        if has_password_field(info):
            password_info = info
            break
    if not password_info:
        return False
    overrides = build_login_overrides(password_info, email, password)
    payload, missing = build_form_payload(password_info, overrides)
    if missing:
        raise RuntimeError("Не заполнены обязательные поля формы пароля: " + ", ".join(missing))
    for name, value in payload.items():
        elements = password_form.find_elements(By.NAME, name)
        if not elements:
            continue
        field = elements[0]
        field.clear()
        field.send_keys(value)
    submit_form(password_form)
    return True


def maybe_fill_otp(driver: webdriver.Remote, wait: WebDriverWait, provided_code: Optional[str]) -> None:
    try:
        verify_form = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "form[action^='/verify/']"))
        )
    except TimeoutException:
        return
    soup = BeautifulSoup(driver.page_source, "html.parser")
    info = parse_html_form(str(soup), driver.current_url)
    code_field = guess_code_field(info)
    if not code_field:
        raise RuntimeError("Не удалось определить поле для ввода кода подтверждения.")
    verification_code = provided_code or input("Введите одноразовый код подтверждения: ").strip()
    if not verification_code:
        raise RuntimeError("Код подтверждения не указан.")
    field = verify_form.find_element(By.NAME, code_field)
    field.clear()
    field.send_keys(verification_code)
    submit_form(verify_form)
    wait.until(lambda d: "verify" not in d.current_url)


def ensure_access(driver: webdriver.Remote, wait: WebDriverWait, url: str) -> None:
    driver.get(url)
    wait.until(lambda d: d.current_url.startswith(url) or "/login" not in d.current_url)
    current = driver.current_url
    if "/login" in current:
        raise RuntimeError(f"Редирект на страницу логина ({current}).")


def select_coupon_type(driver: webdriver.Remote, wait: WebDriverWait, race_id: int, needle: str) -> None:
    target_url = COUPON_TYPES_URL.format(race_id=race_id)
    driver.get(target_url)
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a, button")))
    def expand_needles(value: str) -> List[str]:
        base = [segment.strip().lower() for segment in value.split("|") if segment.strip()]
        expanded = set(base)
        aliases = {
            "на определенную дистанцию": ["at a certain distance"],
            "на определенную дистанцию с выделением номера": [
                "at a certain distance with bib selection"
            ],
        }
        for key, variations in aliases.items():
            if key in expanded:
                expanded.update(var.lower() for var in variations)
        return list(expanded)

    needle_variants = expand_needles(needle)
    candidates = driver.find_elements(By.CSS_SELECTOR, "a, button, div")
    for element in candidates:
        text = (element.text or "").strip()
        inner = (element.get_attribute("innerText") or "").strip()
        href = element.get_attribute("href") or ""
        hx_get = element.get_attribute("hx-get") or element.get_attribute("data-hx-get") or ""
        data_action = element.get_attribute("data-action") or ""
        data_name = element.get_attribute("data-name") or ""
        combined = " ".join(
            filter(
                None,
                [
                    text,
                    inner,
                    href,
                    hx_get,
                    data_action,
                    data_name,
                ],
            )
        ).lower()
        if any(option in combined for option in needle_variants):
            try:
                driver.execute_script("arguments[0].click();", element)
            except Exception:
                element.click()
            time.sleep(1)
            return
    available: List[str] = []
    for el in candidates:
        label = (el.text or el.get_attribute("innerText") or "").strip()
        href = el.get_attribute("href") or ""
        hx_get = el.get_attribute("hx-get") or el.get_attribute("data-hx-get") or ""
        combined_label = label or href or hx_get
        if combined_label:
            available.append(combined_label)
    raise RuntimeError(
        f"Не найден тип промокода, содержащий '{needle}'. Доступные элементы: {available}"
    )


def get_visible_form(driver: webdriver.Remote) -> Optional[object]:
    forms = driver.find_elements(By.TAG_NAME, "form")
    for form in forms:
        if form.is_displayed():
            return form
    return None


def fill_form_fields(form, overrides: Dict[str, Union[str, List[str]]]) -> List[str]:
    missing: List[str] = []
    for name, value in overrides.items():
        elements = form.find_elements(By.NAME, name)
        if not elements:
            missing.append(name)
            continue
        element = elements[0]
        tag = element.tag_name.lower()
        input_type = (element.get_attribute("type") or "").lower()
        try:
            if tag == "select":
                select = Select(element)
                try:
                    select.select_by_value(str(value))
                except Exception:
                    select.select_by_visible_text(str(value))
            elif tag == "textarea":
                element.clear()
                element.send_keys(str(value))
            elif tag == "input" and input_type in ("checkbox", "radio"):
                should_check = str(value).lower() in ("1", "true", "yes", "on")
                current = element.is_selected()
                if should_check != current:
                    element.click()
            else:
                element.clear()
                element.send_keys(str(value))
        except Exception as exc:  # pylint: disable=broad-except
            missing.append(f"{name} ({exc})")
    return missing


def maybe_submit_form(form) -> None:
    try:
        button = form.find_element(By.CSS_SELECTOR, "button[type='submit']")
        button.click()
        return
    except NoSuchElementException:
        pass
    submit_form(form)


def main() -> None:
    args = parse_args()
    cookies_path = Path(args.cookies).expanduser()

    driver = build_driver(args.browser, args.headless)
    wait = WebDriverWait(driver, args.wait)

    try:
        if args.reuse_cookies:
            cookies = read_netscape_cookies(cookies_path)
            add_cookies_to_driver(driver, cookies)
        else:
            if not args.email:
                raise RuntimeError("Укажите --email или используйте --reuse-cookies.")
            driver.get(LOGIN_URL)
            perform_email_step(driver, wait, args.email)
            maybe_fill_password(driver, wait, args.email, args.password or "")
            maybe_fill_otp(driver, wait, args.otp)

        if args.save_cookies:
            export_cookies(driver, cookies_path)

        if args.check_url:
            print(f"Проверяем доступ к {args.check_url}")
            ensure_access(driver, wait, args.check_url)
            print("Доступ подтверждён.")

        if args.race_id and args.coupon_type:
            select_coupon_type(driver, wait, args.race_id, args.coupon_type)
            time.sleep(1)

            form = get_visible_form(driver)
            if not form:
                raise RuntimeError("Не удалось найти форму создания промокода.")

            soup = BeautifulSoup(driver.page_source, "html.parser")
            try:
                form_info = parse_html_form(str(soup), driver.current_url)
            except RuntimeError:
                form_info = None

            if args.show_fields and form_info:
                print("Поля формы:")
                print(format_form_fields(form_info))

            overrides = parse_field_overrides(args.field)
            if form_info:
                payload, missing_defaults = build_form_payload(form_info, overrides)
                if missing_defaults:
                    print(
                        "Внимание: обязательные поля остались пустыми: "
                        + ", ".join(missing_defaults),
                        file=sys.stderr,
                    )
            else:
                payload = overrides

            missing = fill_form_fields(form, payload)
            if missing:
                print("Не удалось заполнить поля:", ", ".join(missing), file=sys.stderr)

            if args.dry_run:
                print("Dry-run: форма не отправлена. Заполненные данные готовы к проверке.")
            else:
                maybe_submit_form(form)
                time.sleep(1)
                print(f"Форма отправлена. Текущий URL: {driver.current_url}")

    except Exception as exc:  # pylint: disable=broad-except
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise
    finally:
        if args.save_cookies and not cookies_path.exists():
            # Если сохранить не удалось, не оставляем пустой файл
            cookies_path.unlink(missing_ok=True)
        if not args.keep_open:
            driver.quit()


if __name__ == "__main__":
    main()
