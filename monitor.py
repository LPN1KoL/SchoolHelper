"""Run grade monitor in background. Checks for new grades every 30 seconds."""
import re
import sys
import os
import time
import logging
import logging.handlers
import urllib.request
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

from auth import create_driver, ensure_logged_in
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

LOG_FILE = os.path.join(os.path.dirname(__file__), "grades_log.txt")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=800*1024, backupCount=0, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("monitor")


def _send_tg(text):
    """Send message via Telegram bot. Silently fails if not configured."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def _parse_marks(driver):
    """Parse all marks from semester page. Returns dict {mark_id: info}."""
    marks = {}
    rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
    for row in rows:
        subj_el = row.find_elements(By.CSS_SELECTOR, '[data-test-id^="subject-name_"]')
        if not subj_el:
            continue
        subject = subj_el[0].text.strip()

        mark_els = row.find_elements(By.CSS_SELECTOR, '[data-test-id^="work_mark-"]')
        for mel in mark_els:
            grade = mel.text.strip()
            if not grade or not grade[0].isdigit():
                continue
            mid = mel.get_attribute("data-test-id")
            marks[mid] = {
                "grade": grade,
                "subject": subject,
                "element": mel,
            }
    return marks


def _get_mark_details(driver, element):
    """Hover over mark element to get tooltip with work type and date."""
    try:
        ActionChains(driver).move_to_element(element).perform()
        time.sleep(0.5)
        parent = element.find_element(By.XPATH, "./..")
        siblings = parent.find_elements(By.CSS_SELECTOR, "div")
        for s in siblings:
            text = s.text.strip()
            if text and s != element and ("урок" in text or "работа" in text.lower() or "20" in text):
                return text.replace("\u2068", "").replace("\u2069", "")
    except Exception:
        pass
    return ""


def run_monitor(driver):
    """Main monitor loop. Refreshes semester page every 30s, logs new grades."""
    driver.get("https://dnevnik.ru/marks")
    time.sleep(5)

    tab = driver.find_elements(By.CSS_SELECTOR, '[data-test-id="tab-period"]')
    if tab:
        driver.execute_script("arguments[0].click();", tab[0])
        time.sleep(3)

    log.info("Мониторинг оценок запущен. Обновление каждые 30 сек.")
    log.info(f"Лог: {LOG_FILE}")

    known_ids = set()

    current = _parse_marks(driver)
    known_ids = set(current.keys())
    log.info(f"Начальное состояние: {len(known_ids)} оценок")

    while True:
        time.sleep(30)
        driver.refresh()
        time.sleep(5)

        # Re-login if session expired
        if "login" in driver.current_url or "userfeed" not in driver.current_url and "marks" not in driver.current_url:
            log.info("Сессия истекла, перелогиниваюсь...")
            ensure_logged_in(driver)
            driver.get("https://dnevnik.ru/marks")
            time.sleep(5)

        tab = driver.find_elements(By.CSS_SELECTOR, '[data-test-id="tab-period"]')
        if tab:
            driver.execute_script("arguments[0].click();", tab[0])
            time.sleep(3)

        current = _parse_marks(driver)
        new_ids = set(current.keys()) - known_ids

        if new_ids:
            for mid in new_ids:
                info = current[mid]
                details = _get_mark_details(driver, info["element"])
                msg = f"НОВАЯ ОЦЕНКА: {info['grade']} | {info['subject']} | {details}"
                log.info(msg)
                details_short = re.sub(r'\.20(2[5-9]|3[0-9])', '', details)
                _send_tg(f"<b>{info['grade']}</b> — {info['subject']}\n{details_short}")

            known_ids = set(current.keys())
        else:
            log.info(f"Проверка — без изменений ({len(current)} оценок)")


if __name__ == "__main__":
    driver = create_driver()
    try:
        ensure_logged_in(driver)
        run_monitor(driver)
    except KeyboardInterrupt:
        print("\nМониторинг остановлен.", flush=True)
    finally:
        driver.quit()
