import os

GRADES_FILE = os.path.join(os.path.dirname(__file__), "grades.png")


def screenshot_grades(page):
    """Navigate to semester marks and screenshot the grades table."""
    print("Taking semester grades screenshot...", flush=True)
    page.goto("https://dnevnik.ru/marks/period", wait_until="domcontentloaded")

    try:
        page.wait_for_selector(
            '[data-test-id="subjects-container"]', timeout=15000
        )
    except Exception:
        if "login" in page.url:
            print("Not authenticated, cannot access /marks", flush=True)
            return None
        # Try full page screenshot as fallback
        page.screenshot(path=GRADES_FILE, full_page=True)
        print(f"Full page screenshot saved (fallback): {GRADES_FILE}", flush=True)
        return GRADES_FILE

    container = page.locator('[data-test-id="subjects-container"]')

    # Resize viewport to fit the entire table
    height = container.evaluate("el => el.scrollHeight")
    top = container.evaluate(
        "el => el.getBoundingClientRect().top + window.scrollY"
    )
    page.set_viewport_size({"width": 1920, "height": int(top + height + 100)})

    container.screenshot(path=GRADES_FILE)
    print(f"Grades screenshot saved: {GRADES_FILE}", flush=True)

    page.set_viewport_size({"width": 1920, "height": 1080})
    return GRADES_FILE
