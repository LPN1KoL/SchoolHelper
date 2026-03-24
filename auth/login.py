import base64
import os
import json
import socket
import threading
import time
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

COOKIES_DIR = os.path.dirname(__file__)
COOKIES_FILE = os.path.join(COOKIES_DIR, "cookies.json")


def log(msg):
    print(msg, flush=True)


class _ProxyForwarder:
    """Local proxy that forwards to upstream proxy with auth."""

    def __init__(self, upstream_host, upstream_port, username, password):
        self.upstream = (upstream_host, int(upstream_port))
        self.auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.server.listen(32)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                self.server.settimeout(1)
                client, _ = self.server.accept()
                threading.Thread(target=self._handle, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle(self, client):
        try:
            data = client.recv(65536)
            if not data:
                client.close()
                return

            # Connect to upstream proxy
            upstream = socket.create_connection(self.upstream, timeout=15)

            # Inject Proxy-Authorization header
            if data.startswith(b"CONNECT"):
                # HTTPS tunnel
                line_end = data.index(b"\r\n")
                rest = data[line_end:]
                first_line = data[:line_end]
                data = first_line + b"\r\nProxy-Authorization: Basic " + self.auth.encode() + rest
            else:
                # HTTP request
                line_end = data.index(b"\r\n")
                rest = data[line_end:]
                first_line = data[:line_end]
                data = first_line + b"\r\nProxy-Authorization: Basic " + self.auth.encode() + rest

            upstream.sendall(data)

            # Bidirectional forwarding
            def forward(src, dst):
                try:
                    while True:
                        d = src.recv(65536)
                        if not d:
                            break
                        dst.sendall(d)
                except Exception:
                    pass
                try:
                    dst.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

            t = threading.Thread(target=forward, args=(upstream, client), daemon=True)
            t.start()
            forward(client, upstream)
            t.join(timeout=5)

            upstream.close()
            client.close()
        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        self.server.close()


_proxy_forwarder = None


def create_driver():
    global _proxy_forwarder
    proxy = os.getenv("PROXY")
    proxy_login = os.getenv("PROXY_LOGIN")
    proxy_password = os.getenv("PROXY_PASSWORD")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1920,1080")

    if proxy:
        if proxy_login and proxy_password:
            host, port = proxy.split(":")
            _proxy_forwarder = _ProxyForwarder(host, port, proxy_login, proxy_password)
            chrome_options.add_argument(f"--proxy-server=http://127.0.0.1:{_proxy_forwarder.port}")
        else:
            chrome_options.add_argument(f"--proxy-server=http://{proxy}")

    return webdriver.Chrome(options=chrome_options)


def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False)
    log(f"Cookies saved ({len(cookies)} cookies)")


def load_cookies(driver):
    if not os.path.exists(COOKIES_FILE):
        return False
    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    driver.get("https://dnevnik.ru")
    time.sleep(2)
    for cookie in cookies:
        if "dnevnik.ru" in cookie.get("domain", ""):
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass
    return True


def login_with_cookies(driver):
    if not os.path.exists(COOKIES_FILE):
        return False
    log("Trying saved cookies...")
    load_cookies(driver)
    driver.get("https://dnevnik.ru/userfeed")
    time.sleep(3)
    if "userfeed" in driver.current_url and "login" not in driver.current_url:
        log("Cookies valid! Logged in.")
        return True
    log("Cookies expired, need fresh login.")
    return False


def full_login(driver, wait):
    region = os.getenv("REGION")
    login = os.getenv("LOGIN")
    password = os.getenv("PASSWORD")
    secret_key = os.getenv("SECRET_KEY")
    profile_keyword = os.getenv("PROFILE_KEYWORD")

    driver.get("https://dnevnik.ru/userfeed")
    time.sleep(3)
    log(f"1. Page: {driver.current_url}")

    esia_link = driver.find_element(By.CSS_SELECTOR, f'a[href*="/login/esia/{region}"]')
    driver.execute_script("arguments[0].click();", esia_link)
    time.sleep(3)
    log(f"2. Region: {driver.current_url}")

    gosuslugi_btn = driver.find_element(By.CSS_SELECTOR, 'a[data-test-id="login-button-gu"]')
    gosuslugi_btn.click()
    log("3. Redirecting to Gosuslugi...")

    login_input = wait.until(EC.presence_of_element_located((By.ID, "login")))
    time.sleep(2)
    login_input.clear()
    login_input.send_keys(login)
    time.sleep(1)
    login_input.send_keys(Keys.ENTER)
    log("4. Login submitted")
    time.sleep(5)

    pwd_input = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, '#password, input[type="password"]')
    ))
    time.sleep(1)
    pwd_input.clear()
    pwd_input.send_keys(password)
    time.sleep(1)
    pwd_input.send_keys(Keys.ENTER)
    log("5. Password submitted")
    time.sleep(7)

    # Check for 2FA
    is_2fa = False
    if "/otp" in driver.current_url or "/code" in driver.current_url or "/mfa" in driver.current_url:
        is_2fa = True
    else:
        sms_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[autocomplete="one-time-code"], .sms__input input, .otp input')
        if sms_inputs:
            is_2fa = True
        else:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "введите код" in body_text.lower() or "отправлен код" in body_text.lower() or "код подтверждения" in body_text.lower():
                is_2fa = True

    if is_2fa:
        totp = pyotp.TOTP(secret_key)
        code = totp.now()
        log(f"Generated TOTP code: {code}")

        inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="tel"]')
        visible_inputs = [ci for ci in inputs if ci.is_displayed()]

        if len(visible_inputs) >= 6 and len(code) == 6:
            for i, digit in enumerate(code):
                visible_inputs[i].send_keys(digit)
        elif visible_inputs:
            visible_inputs[0].clear()
            visible_inputs[0].send_keys(code)
            visible_inputs[0].send_keys(Keys.ENTER)
        else:
            all_inputs = driver.find_elements(By.CSS_SELECTOR, 'input')
            for ci in all_inputs:
                if ci.is_displayed() and ci.get_attribute("type") in ("text", "number", "tel", ""):
                    driver.execute_script("arguments[0].value = arguments[1];", ci, code)
                    driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", ci)
                    break

        log("6. 2FA code submitted")
        time.sleep(7)

    # Account selector
    log(f"7. URL: {driver.current_url}")
    if "account/selector" in driver.current_url or "selector" in driver.current_url:
        _select_profile(driver, profile_keyword)

    time.sleep(3)
    save_cookies(driver)


def _select_profile(driver, keyword):
    time.sleep(3)
    links = driver.find_elements(By.CSS_SELECTOR, 'a')
    for link in links:
        try:
            text = link.text.lower()
            if keyword.lower() in text:
                log(f"   Found profile: {link.text.strip()[:80]}")
                link.click()
                time.sleep(5)
                log(f"   Profile selected, URL: {driver.current_url}")
                return
        except Exception:
            continue

    elements = driver.find_elements(By.CSS_SELECTOR, '[class*="account"], [class*="profile"], [class*="selector"] *')
    for el in elements:
        try:
            text = el.text.lower()
            if keyword.lower() in text and el.is_displayed():
                log(f"   Found profile element: {el.text.strip()[:80]}")
                driver.execute_script("arguments[0].click();", el)
                time.sleep(5)
                log(f"   Profile selected, URL: {driver.current_url}")
                return
        except Exception:
            continue

    log("   Profile with keyword not found! Page text:")
    body_text = driver.find_element(By.TAG_NAME, "body").text
    log(body_text[:500])


def ensure_logged_in(driver):
    """Ensure driver is authenticated. Returns WebDriverWait."""
    wait = WebDriverWait(driver, 20)
    if not login_with_cookies(driver):
        full_login(driver, wait)
    if "userfeed" not in driver.current_url:
        driver.get("https://dnevnik.ru/userfeed")
        time.sleep(3)
    return wait
