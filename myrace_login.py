#!/usr/bin/env python3
"""Shared utilities for working with MyRace HTML forms and cookies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup  # type: ignore

BASE_URL = "https://myrace.info"
LOGIN_URL = f"{BASE_URL}/login/"

HIDDEN_INPUT_RE = re.compile(r'name="(?P<name>[^"]+)"[^>]*value="(?P<value>[^"]*)"')
HEADING_RE = re.compile(r"<h1[^>]*>([^<]+)</h1>")


@dataclass
class FormField:
    name: str
    value: Union[str, List[str], None]
    field_type: str
    required: bool = False
    multiple: bool = False
    options: List[str] = field(default_factory=list)


@dataclass
class FormInfo:
    action: str
    method: str
    fields: Dict[str, FormField]


def extract_hidden_value(html: str, field_name: str) -> Optional[str]:
    for match in HIDDEN_INPUT_RE.finditer(html):
        if match.group("name") == field_name:
            return match.group("value")
    return None


def extract_heading(html: str) -> Optional[str]:
    match = HEADING_RE.search(html)
    if not match:
        return None
    return unescape(match.group(1)).strip()


def _extract_form_info(form, base_url: str) -> FormInfo:
    action = form.get("action") or base_url
    action = urljoin(base_url, action)
    method = form.get("method", "post").lower()

    fields: Dict[str, FormField] = {}

    def add_field(
        name: Optional[str],
        value: Union[str, List[str], None],
        field_type: str,
        required: bool = False,
        multiple: bool = False,
        options: Optional[List[str]] = None,
    ) -> None:
        if not name:
            return
        options = options or []
        multiple = multiple or field_type in {"checkbox", "radio"}

        if name in fields:
            field = fields[name]
            field.multiple = field.multiple or multiple
            field.required = field.required or required
            if field.multiple:
                existing = field.value if isinstance(field.value, list) else []
                if not isinstance(existing, list):
                    existing = [] if existing in (None, "", []) else [existing]
                if isinstance(value, list):
                    existing.extend([item for item in value if item not in (None, "")])
                elif value not in (None, ""):
                    existing.append(value)
                field.value = existing
            elif isinstance(value, list):
                field.value = value[-1] if value else field.value
            elif value is not None:
                field.value = value
            for option in options:
                if option not in field.options:
                    field.options.append(option)
            return

        if multiple:
            if isinstance(value, list):
                base_value = [item for item in value if item not in (None, "")]
            elif value in (None, ""):
                base_value = []
            else:
                base_value = [value]
        else:
            if isinstance(value, list):
                base_value = value[-1] if value else ""
            elif value is None:
                base_value = ""
            else:
                base_value = value

        fields[name] = FormField(
            name=name,
            value=base_value,
            field_type=field_type,
            required=required,
            multiple=multiple,
            options=list(options),
        )

    for input_tag in form.find_all("input"):
        name = input_tag.get("name")
        field_type = input_tag.get("type", "text").lower()
        required = input_tag.has_attr("required")

        if field_type == "checkbox":
            value = input_tag.get("value", "on") if input_tag.has_attr("checked") else None
        elif field_type == "radio":
            value = input_tag.get("value", "")
            if not input_tag.has_attr("checked"):
                value = None
        else:
            value = input_tag.get("value", "")

        add_field(name, value, field_type, required=required)

    for textarea in form.find_all("textarea"):
        name = textarea.get("name")
        required = textarea.has_attr("required")
        value = textarea.text or ""
        add_field(name, value, "textarea", required=required)

    for select in form.find_all("select"):
        name = select.get("name")
        required = select.has_attr("required")
        multiple = select.has_attr("multiple")
        options: List[str] = []
        selected_values: List[str] = []
        for option in select.find_all("option"):
            opt_value = option.get("value")
            if opt_value is None:
                opt_value = option.text
            options.append(opt_value)
            if option.has_attr("selected"):
                selected_values.append(opt_value)

        if not selected_values:
            if multiple:
                value = []
            else:
                value = options[0] if options else ""
        else:
            value = selected_values if multiple else selected_values[0]

        add_field(name, value, "select", required=required, multiple=multiple, options=options)

    return FormInfo(action=action, method=method, fields=fields)


def parse_html_forms(
    html: str,
    base_url: str,
    predicate: Optional[Callable[[FormInfo], bool]] = None,
) -> List[FormInfo]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[FormInfo] = []
    for form in soup.find_all("form"):
        info = _extract_form_info(form, base_url)
        if predicate and not predicate(info):
            continue
        results.append(info)
    return results


def parse_html_form(
    html: str,
    base_url: str,
    predicate: Optional[Callable[[FormInfo], bool]] = None,
) -> FormInfo:
    forms = parse_html_forms(html, base_url, predicate=predicate)
    if not forms:
        raise RuntimeError("Не удалось найти подходящую форму в ответе сервера.")
    return forms[0]


def parse_field_overrides(items: List[str]) -> Dict[str, Union[str, List[str]]]:
    overrides: Dict[str, Union[str, List[str]]] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"Параметр '{raw}' должен быть в формате имя=значение.")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError("Имя поля не может быть пустым.")
        if key in overrides:
            existing = overrides[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                overrides[key] = [existing, value]
        else:
            overrides[key] = value
    return overrides


def build_form_payload(
    form_info: FormInfo,
    overrides: Dict[str, Union[str, List[str]]],
) -> Tuple[Dict[str, Union[str, List[str]]], List[str]]:
    payload: Dict[str, Union[str, List[str]]] = {}
    missing: List[str] = []
    remaining_overrides = dict(overrides)

    for name, field in form_info.fields.items():
        override_value = remaining_overrides.pop(name, None)
        value: Union[str, List[str]]
        if override_value is None:
            value = field.value if field.value is not None else ""
        else:
            value = override_value

        if field.multiple:
            if isinstance(value, list):
                filtered = [item for item in value if item != ""]
            elif value in (None, ""):
                filtered = []
            else:
                filtered = [value]
            payload[name] = filtered
            if field.required and not filtered:
                missing.append(name)
        else:
            if isinstance(value, list):
                final_value = value[-1] if value else ""
            else:
                final_value = value if value is not None else ""
            payload[name] = final_value
            if field.required and final_value == "":
                missing.append(name)

    for name, value in remaining_overrides.items():
        payload[name] = value

    return payload, missing


def format_form_fields(form_info: FormInfo) -> str:
    lines: List[str] = []
    for field in form_info.fields.values():
        value = field.value
        if isinstance(value, list):
            display_value = ", ".join(value) if value else "[]"
        else:
            display_value = value if value not in (None, "") else '""'
        suffix_parts: List[str] = []
        if field.required:
            suffix_parts.append("required")
        if field.multiple:
            suffix_parts.append("multiple")
        if field.options:
            suffix_parts.append(f"options={len(field.options)}")
        suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(f"- {field.name}{suffix}: {display_value}")
    return "\n".join(lines)


def has_password_field(form_info: FormInfo) -> bool:
    for field in form_info.fields.values():
        if field.field_type == "password":
            return True
    return False


def guess_code_field(form_info: FormInfo) -> Optional[str]:
    preferred = ("code", "token", "otp", "pin", "verificationcode", "verifycode")
    for field in form_info.fields.values():
        if field.name.lower() in preferred:
            return field.name
    for field in form_info.fields.values():
        if field.field_type in {"text", "number", "tel", "password"}:
            value = field.value
            if isinstance(value, list):
                if not value:
                    return field.name
            elif value in (None, ""):
                return field.name
    return None


def build_login_overrides(*_args, **_kwargs) -> Dict[str, str]:
    """Compatibility stub retained for legacy imports."""
    return {}
