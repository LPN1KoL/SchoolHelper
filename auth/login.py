import json
import os
import time

import pyotp
from playwright.sync_api import Playwright

COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")


def log(msg):
    print(msg, flush=True)


# -- browser / context ----------------------------------------------------

def create_browser(pw: Playwright):
    """Launch headless Chromium with optional proxy from env."""
    proxy_str = os.getenv("PROXY")
    proxy_login = os.getenv("PROXY_LOGIN")
    proxy_password = os.getenv("PROXY_PASSWORD")

    proxy = None
    if proxy_str:
        proxy = {"server": f"http://{proxy_str}"}
        if proxy_login and proxy_password:
            proxy["username"] = proxy_login
            proxy["password"] = proxy_password

    return pw.chromium.launch(headless=True, proxy=proxy)


def create_context(browser):
    """Create a new browser context with standard settings."""
    return browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
    )


# -- cookies ---------------------------------------------------------------

def _load_cookies():
    if not os.path.exists(COOKIES_FILE):
        return None
    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    cookies = []
    for c in raw:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        if c.get("expiry"):
            cookie["expires"] = c["expiry"]
        elif c.get("expires") and c["expires"] > 0:
            cookie["expires"] = int(c["expires"])
        if c.get("secure"):
            cookie["secure"] = c["secure"]
        ss = c.get("sameSite")
        if ss in ("Strict", "Lax", "None"):
            cookie["sameSite"] = ss
        cookies.append(cookie)
    return cookies


def save_cookies(context):
    """Save cookies from context to disk."""
    cookies = context.cookies()
    out = []
    for c in cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        if c.get("expires") and c["expires"] > 0:
            cookie["expiry"] = int(c["expires"])
        ss = c.get("sameSite")
        if ss in ("Strict", "Lax", "None"):
            cookie["sameSite"] = ss
        out.append(cookie)
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    log(f"Cookies saved ({len(out)} cookies)")


# -- login -----------------------------------------------------------------

def ensure_logged_in(page, context):
    """Try cookies, then full ESIA login if needed."""
    cookies = _load_cookies()
    if cookies:
        log("Trying saved cookies...")
        try:
            context.add_cookies(cookies)
        except Exception as e:
            log(f"Failed to load some cookies: {e}")

        page.goto("https://dnevnik.ru/userfeed", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        if "userfeed" in page.url and "login" not in page.url:
            log("Cookies valid! Logged in.")
            return

        log("Cookies expired, need fresh login.")

    _full_login(page)
    save_cookies(context)

    if "userfeed" not in page.url and "dnevnik.ru" in page.url:
        page.goto("https://dnevnik.ru/userfeed", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")


def _full_login(page):
    region = os.getenv("REGION")
    login_val = os.getenv("LOGIN")
    password = os.getenv("PASSWORD")
    secret_key = os.getenv("SECRET_KEY")
    profile_keyword = os.getenv("PROFILE_KEYWORD")

    esia_url = (
        f"https://login.dnevnik.ru/login/esia/{region}"
        f"?returnUrl=https%3A%2F%2Fdnevnik.ru%2Fuserfeed"
    )
    page.goto(esia_url, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    log(f"1. Region: {page.url}")

    gu_btn = page.locator('a[data-test-id="login-button-gu"]')
    gu_btn.wait_for(state="visible", timeout=10000)
    gu_btn.click()
    page.wait_for_load_state("networkidle")
    log(f"2. Gosuslugi: {page.url}")

    # Already authenticated on Gosuslugi → account selector
    if "selector" in page.url:
        log("Госуслуги уже авторизованы, выбираю профиль...")
        _select_profile(page, profile_keyword)
        page.wait_for_load_state("networkidle")
        return

    # Already redirected back to dnevnik
    if "dnevnik.ru" in page.url and "login" not in page.url:
        log("Уже авторизован!")
        return

    # Login form
    page.wait_for_selector("#login", state="visible")
    page.fill("#login", login_val)
    page.press("#login", "Enter")
    log("3. Login submitted")
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    # Password
    pwd_sel = '#password, input[type="password"]'
    page.wait_for_selector(pwd_sel, state="visible")
    page.fill(pwd_sel, password)
    page.press(pwd_sel, "Enter")
    log("4. Password submitted")
    page.wait_for_load_state("networkidle")
    time.sleep(5)

    # 2FA
    is_2fa = any(x in page.url for x in ("/otp", "/code", "/mfa"))
    if not is_2fa:
        sms = page.query_selector_all(
            'input[autocomplete="one-time-code"], .sms__input input, .otp input'
        )
        if sms:
            is_2fa = True
        else:
            body = (page.text_content("body") or "").lower()
            if any(
                x in body
                for x in ("введите код", "отправлен код", "код подтверждения")
            ):
                is_2fa = True

    if is_2fa:
        totp = pyotp.TOTP(secret_key)
        code = totp.now()
        log(f"Generated TOTP code: {code}")

        inputs = page.query_selector_all('input[type="tel"]')
        visible = [i for i in inputs if i.is_visible()]

        if len(visible) >= 6 and len(code) == 6:
            for i, digit in enumerate(code):
                visible[i].type(digit)
                time.sleep(0.1)
        elif visible:
            visible[0].fill(code)
            visible[0].press("Enter")

        log("5. 2FA code submitted")

        try:
            page.wait_for_url("**/dnevnik.ru/**", timeout=30000)
        except Exception:
            page.wait_for_load_state("networkidle")
            time.sleep(3)

    log(f"6. URL: {page.url}")
    if "selector" in page.url:
        _select_profile(page, profile_keyword)

    page.wait_for_load_state("networkidle")


def _select_profile(page, keyword):
    time.sleep(2)
    for link in page.query_selector_all("a"):
        try:
            text = link.text_content() or ""
            if keyword.lower() in text.lower():
                log(f"   Found profile: {text.strip()[:80]}")
                link.click()
                page.wait_for_load_state("networkidle")
                log(f"   Profile selected, URL: {page.url}")
                return
        except Exception:
            continue

    log("   Profile with keyword not found!")
    body = (page.text_content("body") or "")[:500]
    log(body)
