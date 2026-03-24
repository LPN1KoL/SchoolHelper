import sys
import argparse
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from auth import create_driver, ensure_logged_in
from scripts.marks import screenshot_grades
from scripts.homeworks import parse_homework, print_homework, download_files, filter_by_date

parser = argparse.ArgumentParser(description="DnevnikCheker")
parser.add_argument("--date", default="завтра",
                    help="Дата для домашки: 'сегодня', 'завтра', 'послезавтра' или ДД.ММ (по умолчанию: завтра)")
args = parser.parse_args()

driver = create_driver()

try:
    ensure_logged_in(driver)
    screenshot_grades(driver)

    homework = parse_homework(driver)
    filtered = filter_by_date(homework, args.date)
    print_homework(filtered)
    download_files(filtered)
finally:
    driver.quit()
