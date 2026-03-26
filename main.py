import sys
import argparse
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright
from auth import create_browser, create_context, ensure_logged_in
from scripts.marks import screenshot_grades
from scripts.homeworks import parse_homework, print_homework, download_files, filter_by_date

parser = argparse.ArgumentParser(description="DnevnikCheker")
parser.add_argument("--date", default="завтра",
                    help="Дата для домашки: 'сегодня', 'завтра', 'послезавтра' или ДД.ММ (по умолчанию: завтра)")
args = parser.parse_args()

with sync_playwright() as pw:
    browser = create_browser(pw)
    context = create_context(browser)
    page = context.new_page()

    try:
        ensure_logged_in(page, context)
        screenshot_grades(page)

        homework = parse_homework(page)
        filtered = filter_by_date(homework, args.date)
        print_homework(filtered)
        download_files(filtered)
    finally:
        browser.close()
