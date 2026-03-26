"""Run grade monitor in background. Checks for new grades every 30 seconds."""
import html
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

from auth import create_browser, create_context, ensure_logged_in, save_cookies

LOG_FILE = os.path.join(os.path.dirname(__file__), "grades_log.txt")
KNOWN_IDS_FILE = os.path.join(os.path.dirname(__file__), "known_ids.json")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
MARKS_URL = "https://dnevnik.ru/marks/period"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=800 * 1024, backupCount=3, encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("monitor")


# -- persistence ----------------------------------------------------------

def _load_known_ids():
    try:
        with open(KNOWN_IDS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_known_ids(ids):
    with open(KNOWN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


# -- telegram -------------------------------------------------------------

def _send_tg(text):
    """Send message via Telegram bot. Silently fails if not configured."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
        ).encode()
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


# -- marks parsing --------------------------------------------------------

def _session_expired(page):
    url = page.url
    if "login" in url:
        return True
    return "marks" not in url and "userfeed" not in url


def _parse_marks(page):
    """Parse all marks from semester page. Returns dict {mark_id: info}."""
    marks = {}
    for row in page.query_selector_all("table tr"):
        subj_el = row.query_selector('[data-test-id^="subject-name_"]')
        if not subj_el:
            continue
        subject = (subj_el.text_content() or "").strip()

        for mel in row.query_selector_all('[data-test-id^="work_mark-"]'):
            grade = (mel.text_content() or "").strip()
            if not grade or not grade[0].isdigit():
                continue
            mid = mel.get_attribute("data-test-id")
            marks[mid] = {"grade": grade, "subject": subject}
    return marks


def _get_mark_details(page, data_test_id):
    """Hover over mark element to read tooltip."""
    try:
        loc = page.locator(f'[data-test-id="{data_test_id}"]')
        if loc.count() == 0:
            return ""
        loc.first.hover()
        time.sleep(0.5)

        for sel in (
            '[class*="tooltip"]', '[class*="Tooltip"]',
            '[class*="popup"]', '[class*="Popup"]',
            '[role="tooltip"]',
        ):
            tips = page.query_selector_all(sel)
            for tip in tips:
                text = (tip.text_content() or "").strip()
                if text and len(text) > 3:
                    return text.replace("\u2068", "").replace("\u2069", "")

        # fallback: sibling divs
        el = loc.first.element_handle()
        parent = el.query_selector("xpath=./..")
        if parent:
            for s in parent.query_selector_all("div"):
                text = (s.text_content() or "").strip()
                if text and ("урок" in text.lower() or "работа" in text.lower() or "20" in text):
                    return text.replace("\u2068", "").replace("\u2069", "")
    except Exception as e:
        log.debug(f"Не удалось получить детали: {e}")
    return ""


# -- main loop ------------------------------------------------------------

def _go_to_marks(page, context):
    """Navigate to marks/period, re-login if needed."""
    page.goto(MARKS_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_selector('[data-test-id^="subject-name_"]', timeout=15000)
    except Exception:
        if _session_expired(page):
            log.info("Сессия истекла, перелогиниваюсь...")
            ensure_logged_in(page, context)
            save_cookies(context)
            page.goto(MARKS_URL, wait_until="domcontentloaded")
            page.wait_for_selector('[data-test-id^="subject-name_"]', timeout=15000)


def run_monitor():
    with sync_playwright() as pw:
        browser = create_browser(pw)
        context = create_context(browser)
        page = context.new_page()

        try:
            ensure_logged_in(page, context)
            _go_to_marks(page, context)

            log.info("Мониторинг оценок запущен. Обновление каждые 30 сек.")
            log.info(f"Лог: {LOG_FILE}")

            known_ids = _load_known_ids()
            current = _parse_marks(page)

            if not known_ids:
                known_ids = set(current.keys())
                _save_known_ids(known_ids)
                log.info(f"Первый запуск: запомнил {len(known_ids)} оценок")
            else:
                log.info(
                    f"Загружено {len(known_ids)} известных оценок, текущих: {len(current)}"
                )

            while True:
                time.sleep(30)
                try:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_selector(
                        '[data-test-id^="subject-name_"]', timeout=15000
                    )
                except Exception:
                    log.warning("Страница не загрузилась, проверяю сессию...")
                    try:
                        _go_to_marks(page, context)
                    except Exception as e:
                        log.error(f"Не удалось восстановить сессию: {e}")
                        time.sleep(60)
                        continue

                if _session_expired(page):
                    log.info("Сессия истекла, перелогиниваюсь...")
                    ensure_logged_in(page, context)
                    save_cookies(context)
                    _go_to_marks(page, context)

                current = _parse_marks(page)
                new_ids = set(current.keys()) - known_ids

                if new_ids:
                    for mid in new_ids:
                        info = current[mid]
                        details = _get_mark_details(page, mid)
                        msg = f"НОВАЯ ОЦЕНКА: {info['grade']} | {info['subject']} | {details}"
                        log.info(msg)
                        details_short = re.sub(r"[./]20\d{2}", "", details)
                        _send_tg(
                            f"<b>{html.escape(info['grade'])}</b> — "
                            f"{html.escape(info['subject'])}\n"
                            f"{html.escape(details_short)}"
                        )

                    known_ids = set(current.keys())
                    _save_known_ids(known_ids)
                else:
                    log.info(f"Проверка — без изменений ({len(current)} оценок)")

        except KeyboardInterrupt:
            log.info("Остановлен вручную")
        except Exception as e:
            log.error(f"Критическая ошибка: {e}", exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    run_monitor()
