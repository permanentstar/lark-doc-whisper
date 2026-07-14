"""Normalize visible text and URLs from Lark rich-text style elements."""
from __future__ import annotations

from dataclasses import dataclass


def _get_field(obj, name: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _string_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _first_text(obj, field_names: tuple[str, ...], *, strip: bool = True) -> str:
    for name in field_names:
        raw = _string_value(_get_field(obj, name))
        text = raw.strip() if strip else raw
        if text.strip():
            return text
    return ""


def _element_kind(element) -> str:
    explicit = _string_value(_get_field(element, "type")).strip().lower()
    if explicit:
        return explicit
    for candidate in ("text_run", "docs_link", "link", "person", "mention_user", "mention_doc"):
        if _get_field(element, candidate) is not None:
            return candidate
    return ""


def _element_payload(element, kind: str):
    if kind:
        payload = _get_field(element, kind)
        if payload is not None:
            return payload
    for candidate in ("text_run", "docs_link", "link", "person", "mention_user", "mention_doc"):
        payload = _get_field(element, candidate)
        if payload is not None:
            return payload
    return None


def _mention_text(payload) -> str:
    name = _first_text(payload, ("name", "display_name", "text", "title"))
    if not name:
        return ""
    return name if name.startswith("@") else f"@{name}"


def _element_visible_text(element) -> str:
    kind = _element_kind(element)
    payload = _element_payload(element, kind)

    if kind == "text_run":
        return _first_text(payload, ("text", "content"), strip=False)
    if kind in {"person", "mention_user"}:
        return _mention_text(payload)
    if kind in {"link", "docs_link", "mention_doc"}:
        label = _first_text(payload, ("text", "title", "name", "display_name", "content"))
        if label:
            return label
        payload_text = _string_value(payload).strip()
        if payload_text and not _looks_like_url(payload_text):
            return payload_text
        return ""

    if payload is not None:
        text = _first_text(payload, ("text", "content", "title", "name", "display_name", "label"))
        if text:
            return text
    return _first_text(element, ("text", "content", "title", "name"))


def _element_url(element) -> str:
    kind = _element_kind(element)
    payload = _element_payload(element, kind)
    if kind in {"link", "docs_link", "mention_doc"}:
        payload_text = _string_value(payload).strip()
        if payload_text:
            return payload_text
        return _first_text(payload, ("url", "link"))
    if payload is not None:
        url = _first_text(payload, ("url", "link"))
        if url:
            return url
    return _first_text(element, ("url", "link"))


def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class ParsedRichText:
    visible_text: str = ""
    urls: tuple[str, ...] = ()

    def render(self, *, include_urls: bool) -> str:
        visible = self.visible_text.strip()
        if not include_urls or not self.urls:
            return visible

        parts = [visible] if visible else []
        for url in self.urls:
            if not url or url in visible:
                continue
            parts.append(url)
        return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def parse_rich_text_elements(elements) -> ParsedRichText:
    visible_parts: list[str] = []
    urls: list[str] = []
    for element in list(elements or []):
        text = _element_visible_text(element)
        if text:
            visible_parts.append(text)
        url = _element_url(element)
        if url:
            urls.append(url)
    return ParsedRichText(
        visible_text="".join(visible_parts).strip(),
        urls=_dedupe_strings(urls),
    )


def render_rich_text_elements(elements, *, include_urls: bool) -> str:
    return parse_rich_text_elements(elements).render(include_urls=include_urls)
