"""Read-only item collection core for current logged-in Goofish account."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse


GOOFISH_BASE_URL = "https://www.goofish.com"
PERSONAL_PAGE_URL = f"{GOOFISH_BASE_URL}/personal"
DEFAULT_SCROLL_DELTA = 900
DEFAULT_STALE_ROUNDS = 2

SECTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "selling": {
        "key": "selling",
        "label": "在售",
        "selectors": [
            ':text("在售")',
            'span:text-is("在售")',
            'div[class*="tab"]:has-text("在售")',
        ],
    },
    "offline": {
        "key": "offline",
        "label": "已下架",
        "selectors": [
            ':text("已下架")',
            'span:text-is("已下架")',
            'div[class*="tab"]:has-text("已下架")',
        ],
    },
    "draft": {
        "key": "draft",
        "label": "草稿",
        "selectors": [
            ':text("草稿箱")',
            ':text("草稿")',
            'span:text-is("草稿箱")',
            'div[class*="tab"]:has-text("草稿")',
        ],
    },
}

COLLECT_ITEM_CARDS_JS = """
() => {
  const rows = [];
  const anchors = document.querySelectorAll('a:has(img), a[href*="/item"], a[href*="item?id="]');
  anchors.forEach((a) => {
    const href = a.getAttribute('href') || '';
    if (!href) return;

    const img = a.querySelector('img');
    const imageUrl = (img && (img.getAttribute('src') || img.getAttribute('data-src'))) || '';

    const textNodes = Array.from(a.querySelectorAll('*'))
      .flatMap((el) => Array.from(el.childNodes))
      .filter((node) => node.nodeType === 3)
      .map((node) => (node.textContent || '').trim())
      .filter(Boolean);

    let title = a.getAttribute('title') || '';
    if (!title) {
      title = textNodes.find((t) => !t.startsWith('¥') && !/^[\\d.,]+$/.test(t)) || '';
    }
    if (!title) title = '(无标题)';

    let price = '';
    const priceNode =
      a.querySelector('[class*="price"]') ||
      Array.from(a.querySelectorAll('*')).find((el) => (el.textContent || '').trim().startsWith('¥'));
    if (priceNode) {
      price = (priceNode.textContent || '').trim();
    }

    rows.push({
      href,
      title: title.slice(0, 120),
      price: (price || '').slice(0, 40),
      image_url: imageUrl,
    });
  });
  return rows;
}
"""


def parse_item_id_from_href(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    for key in ("id", "itemId", "item_id"):
        values = query.get(key)
        if values:
            value = str(values[0]).strip()
            if value:
                return value
    if parsed.path:
        candidates = [chunk for chunk in parsed.path.split("/") if chunk]
        for chunk in reversed(candidates):
            if chunk.isdigit():
                return chunk
    return ""


def normalize_item_card(
    card: dict[str, Any],
    *,
    status_key: str,
    status_label: str,
    base_url: str = GOOFISH_BASE_URL,
) -> dict[str, Any] | None:
    href = str((card or {}).get("href") or "").strip()
    if not href:
        return None
    full_href = href if href.startswith("http") else urljoin(base_url, href)
    item_id = parse_item_id_from_href(full_href)
    title = str((card or {}).get("title") or "").strip() or "(无标题)"
    price = str((card or {}).get("price") or "").strip()
    image_url = str((card or {}).get("image_url") or "").strip()
    if image_url and image_url.startswith("//"):
        image_url = f"https:{image_url}"
    elif image_url and image_url.startswith("/"):
        image_url = urljoin(base_url, image_url)

    return {
        "item_id": item_id,
        "title": title[:120],
        "price": price[:40],
        "href": full_href,
        "image_url": image_url,
        "status": status_key,
        "status_label": status_label,
    }


def cookie_string_to_playwright_cookies(cookie_string: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for pair in cookie_string.split(";"):
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".goofish.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
            }
        )
    return cookies


def write_snapshot(output_path: str, payload: dict[str, Any]) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_sections(sections: list[str] | None) -> list[dict[str, Any]]:
    if sections is None:
        return [
            SECTION_DEFINITIONS["selling"],
            SECTION_DEFINITIONS["offline"],
            SECTION_DEFINITIONS["draft"],
        ]
    resolved: list[dict[str, Any]] = []
    for key in sections:
        clean_key = str(key).strip()
        if clean_key not in SECTION_DEFINITIONS:
            raise ValueError(f"unsupported section: {clean_key}")
        resolved.append(SECTION_DEFINITIONS[clean_key])
    if not resolved:
        raise ValueError("sections cannot be empty")
    return resolved


def _is_logged_in(page: Any) -> bool:
    current_url = str(page.url or "")
    if "login.taobao.com" in current_url or "passport" in current_url:
        return False

    for text in ("立即登录", "登录后可以更懂你", "请先登录", "请登录"):
        try:
            if page.get_by_text(text, exact=False).first.is_visible(timeout=300):
                return False
        except Exception:
            pass

    indicators = [
        'img[class*="avatar"]',
        '[class*="avatar"] img',
        '[class*="nickname"]',
        '[class*="user-info"]',
        '[class*="userInfo"]',
    ]
    for selector in indicators:
        try:
            if page.locator(selector).first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    return False


def _click_section_tab(page: Any, section: dict[str, Any]) -> None:
    for selector in section.get("selectors", []):
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=3000):
                locator.click()
                time.sleep(1.0)
                return
        except Exception:
            continue
    raise RuntimeError(f'未找到“{section["label"]}”标签，无法采集该状态商品。')


def _collect_items_for_section(
    page: Any,
    *,
    status_key: str,
    status_label: str,
    max_scroll_rounds: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_hrefs: set[str] = set()
    stale_rounds = 0

    for _ in range(max_scroll_rounds):
        raw_cards = page.evaluate(COLLECT_ITEM_CARDS_JS) or []
        prev_count = len(items)
        for raw in raw_cards:
            normalized = normalize_item_card(raw, status_key=status_key, status_label=status_label)
            if not normalized:
                continue
            href = normalized["href"]
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            items.append(normalized)

        if len(items) == prev_count:
            stale_rounds += 1
            if stale_rounds >= DEFAULT_STALE_ROUNDS:
                break
        else:
            stale_rounds = 0

        page.mouse.wheel(0, DEFAULT_SCROLL_DELTA)
        time.sleep(1.2)

    return items


def _extract_account_metadata(page: Any) -> dict[str, Any]:
    url = str(page.url or "")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    user_id = (query.get("userId") or [""])[0].strip()
    account: dict[str, Any] = {}
    if user_id:
        account["user_id"] = user_id
    return account


def collect_current_account_items(
    cookie_string: str,
    *,
    output_path: str | None = None,
    headless: bool = True,
    sections: list[str] | None = None,
    max_scroll_rounds: int = 8,
) -> dict[str, Any]:
    cookie_string = (cookie_string or "").strip()
    if not cookie_string:
        raise ValueError("cookie_string is required")

    section_defs = _resolve_sections(sections)
    section_keys = [section["key"] for section in section_defs]
    section_counts: dict[str, int] = {"selling": 0, "offline": 0, "draft": 0}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context()
            context.add_cookies(cookie_string_to_playwright_cookies(cookie_string))
            page = context.new_page()
            page.goto(PERSONAL_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.0)

            if not _is_logged_in(page):
                raise RuntimeError("not logged in or invalid cookie for personal page")

            account = _extract_account_metadata(page)
            all_items: list[dict[str, Any]] = []

            for section in section_defs:
                _click_section_tab(page, section)
                section_items = _collect_items_for_section(
                    page,
                    status_key=section["key"],
                    status_label=section["label"],
                    max_scroll_rounds=max_scroll_rounds,
                )
                section_counts[section["key"]] = len(section_items)
                all_items.extend(section_items)

            payload = {
                "ok": True,
                "item_count": len(all_items),
                "items": all_items,
                "section_counts": section_counts,
                "metadata": {
                    "source": "personal_page",
                    "account_verified": False,
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "sections": section_keys,
                    "profile_url": PERSONAL_PAGE_URL,
                    "headless": headless,
                    "account": account,
                },
            }
            if output_path:
                write_snapshot(output_path, payload)
            return payload
        finally:
            browser.close()
