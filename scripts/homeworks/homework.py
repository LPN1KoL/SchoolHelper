import sys
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By

FILES_DIR = os.path.join(os.path.dirname(__file__), "files")

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8,
    "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def parse_homework(driver):
    """Navigate to /marks and parse homework assignments grouped by day."""
    print("Parsing homework from /marks...", flush=True)
    driver.get("https://dnevnik.ru/marks")
    time.sleep(5)

    if "login" in driver.current_url:
        print("Not authenticated, cannot access /marks", flush=True)
        return []

    result = []

    days = driver.find_elements(By.CSS_SELECTOR, '[data-test-id^="day-"]:not([data-test-id="day-cards"]):not([data-test-id="day-selector"])')
    for day in days:
        date_el = day.find_elements(By.CSS_SELECTOR, '[data-test-id^="card-date-"]')
        date_text = date_el[0].text.strip() if date_el else ""

        lessons = [el for el in day.find_elements(By.CSS_SELECTOR, '[data-test-id^="lesson-"]')
                   if el.get_attribute("data-test-id").count("-") == 1]

        for lesson in lessons:
            lesson_id = lesson.get_attribute("data-test-id")

            subj_el = lesson.find_elements(By.CSS_SELECTOR, '[data-test-id^="subject-name_"]')
            subject = subj_el[0].text.strip() if subj_el else ""

            hw_el = lesson.find_elements(By.CSS_SELECTOR, f'[data-test-id="{lesson_id}-homework-text"]')
            hw_text = hw_el[0].text.strip() if hw_el else ""

            time_el = lesson.find_elements(By.CSS_SELECTOR, f'[data-test-id="{lesson_id}-timeAndPlace"]')
            time_text = time_el[0].text.strip() if time_el else ""

            num_el = lesson.find_elements(By.CSS_SELECTOR, f'[data-test-id="{lesson_id}-number"]')
            num_text = num_el[0].text.strip() if num_el else ""

            # Collect attached files
            files = []
            file_container = lesson.find_elements(By.CSS_SELECTOR, f'[data-test-id="{lesson_id}-files"]')
            if file_container:
                for link in file_container[0].find_elements(By.TAG_NAME, "a"):
                    href = link.get_attribute("href") or ""
                    name = link.text.strip()
                    if href:
                        files.append({"name": name, "url": href})

            if subject or hw_text:
                result.append({
                    "date": date_text,
                    "number": num_text,
                    "subject": subject,
                    "time": time_text,
                    "homework": hw_text,
                    "files": files,
                })

    if result:
        print(f"Parsed {len(result)} lessons", flush=True)
        return result

    body = driver.find_element(By.TAG_NAME, "body").text
    print(f"Could not parse homework. Page text preview:\n{body[:500]}", flush=True)
    return []


def _parse_page_date(date_text):
    """Parse date like 'ПН, 16 мар.' or 'ПТ, 20 мар., сегодня' into (day, month)."""
    m = re.search(r'(\d{1,2})\s+(\w{3})', date_text.lower())
    if not m:
        return None
    day = int(m.group(1))
    month = MONTHS.get(m.group(2))
    if not month:
        return None
    return (day, month)


def filter_by_date(homework, date_str="завтра"):
    """Filter homework entries by date string.

    date_str: 'сегодня', 'завтра', 'послезавтра', or 'ДД.ММ' / 'ДД.ММ.ГГГГ'
    """
    today = datetime.now()

    if date_str == "сегодня":
        target = today
    elif date_str == "завтра":
        target = today + timedelta(days=1)
    elif date_str == "послезавтра":
        target = today + timedelta(days=2)
    else:
        parts = date_str.split(".")
        try:
            day = int(parts[0])
            month = int(parts[1])
            year = int(parts[2]) if len(parts) >= 3 else today.year
            target = today.replace(year=year, month=month, day=day)
        except (ValueError, IndexError):
            print(f"Неверный формат даты: {date_str}. Используйте ДД.ММ или ДД.ММ.ГГГГ", flush=True)
            return homework

    target_pair = (target.day, target.month)

    filtered = [e for e in homework if _parse_page_date(e["date"]) == target_pair]

    if filtered:
        print(f"Filtered: {len(filtered)} lessons for {target.strftime('%d.%m')}", flush=True)
    else:
        print(f"No lessons found for {target.strftime('%d.%m')}", flush=True)

    return filtered


def download_files(homework):
    """Download attached files from homework entries to files/ directory."""
    os.makedirs(FILES_DIR, exist_ok=True)

    count = 0
    for entry in homework:
        if not entry["files"]:
            continue

        subject = entry["subject"]
        for file in entry["files"]:
            url = file["url"]
            original_name = file["name"] if file["name"] else url.rsplit("/", 1)[-1]
            name = f"({subject}) {original_name}"
            filepath = os.path.join(FILES_DIR, name)

            if os.path.exists(filepath):
                continue

            try:
                urllib.request.urlretrieve(url, filepath)
                print(f"  Downloaded: {name}", flush=True)
                count += 1
            except Exception as e:
                print(f"  Failed to download {name}: {e}", flush=True)

    if count:
        print(f"Downloaded {count} files to {FILES_DIR}", flush=True)


def print_homework(homework):
    """Print parsed homework to stdout."""
    if not homework:
        print("\nДомашние задания не найдены.", flush=True)
        return

    print("\n--- Домашние задания ---", flush=True)
    current_date = ""
    for entry in homework:
        if entry["date"] and entry["date"] != current_date:
            current_date = entry["date"]
            print(f"\n  {current_date}", flush=True)
        hw = entry["homework"] if entry["homework"] else "—"
        line = f"    {entry['number']} {entry['subject']} ({entry['time']}): {hw}"
        if entry.get("files"):
            filenames = ", ".join(f["name"] or f["url"].rsplit("/", 1)[-1] for f in entry["files"])
            line += f" [файлы: {filenames}]"
        print(line, flush=True)
