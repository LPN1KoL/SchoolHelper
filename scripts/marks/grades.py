import os
import time
from selenium.webdriver.common.by import By

GRADES_FILE = os.path.join(os.path.dirname(__file__), "grades.png")


def screenshot_grades(driver):
    """Navigate to semester marks tab and screenshot the grades table."""
    print("Taking semester grades screenshot...", flush=True)
    driver.get("https://dnevnik.ru/marks")
    time.sleep(5)

    if "login" in driver.current_url:
        print("Not authenticated, cannot access /marks", flush=True)
        return None

    # Click "По семестрам" tab
    tab = driver.find_elements(By.CSS_SELECTOR, '[data-test-id="tab-period"]')
    if tab:
        driver.execute_script("arguments[0].click();", tab[0])
        time.sleep(3)

    # Screenshot the subjects table container
    container = driver.find_elements(By.CSS_SELECTOR, '[data-test-id="subjects-container"]')
    if container:
        driver.execute_script("arguments[0].scrollIntoView({block: 'start'});", container[0])
        time.sleep(1)
        height = driver.execute_script("return arguments[0].scrollHeight", container[0])
        top = driver.execute_script("return arguments[0].getBoundingClientRect().top + window.scrollY", container[0])
        driver.set_window_size(1920, int(top + height + 100))
        time.sleep(1)

        container[0].screenshot(GRADES_FILE)
        print(f"Grades screenshot saved: {GRADES_FILE}", flush=True)
        driver.set_window_size(1920, 1080)
        return GRADES_FILE

    # Fallback: screenshot full page
    driver.save_screenshot(GRADES_FILE)
    print(f"Full page screenshot saved (fallback): {GRADES_FILE}", flush=True)
    return GRADES_FILE
