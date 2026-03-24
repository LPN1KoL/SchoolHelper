"""Image recognition via free APIs: Gemini (primary) + Groq (fallback).

Requires in .env:
  GEMINI_API_KEY (get free at https://ai.google.dev)
  GROQ_API_KEY   (get free at https://console.groq.com)

Handles SVG images by converting them to PNG via Selenium before sending.
"""
import base64
import json
import os
import tempfile
import time
import urllib.request
import urllib.error


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["meta-llama/llama-4-scout-17b-16e-instruct"]

PROMPT = (
    "Опиши содержимое этого изображения максимально подробно. "
    "Если это граф — перечисли все вершины и рёбра. "
    "Если это таблица — воспроизведи её. "
    "Если есть формулы или текст — перепиши их точно. "
    "Отвечай на русском."
)


# ---------------------------------------------------------------------------
# SVG handling
# ---------------------------------------------------------------------------

def _is_svg(data: bytes) -> bool:
    start = data[:256].lstrip()
    return start.startswith(b"<?xml") or start.startswith(b"<svg")


def _svg_to_png(svg_data: bytes) -> bytes:
    """Convert SVG to PNG using Selenium (headless Chrome)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        svg_text = svg_data.decode("utf-8", errors="replace")
        f.write(f"""<!DOCTYPE html>
<html><head><style>
body {{ margin: 0; background: white; display: inline-block; }}
</style></head><body>{svg_text}</body></html>""")
        tmp_path = f.name

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)

    try:
        driver.get("file:///" + tmp_path.replace("\\", "/"))
        time.sleep(0.5)
        body = driver.find_element("tag name", "body")
        w = body.size["width"]
        h = body.size["height"]
        driver.set_window_size(max(w + 20, 200), max(h + 20, 200))
        time.sleep(0.3)
        return body.screenshot_as_png
    finally:
        driver.quit()
        os.unlink(tmp_path)


def _prepare_image(image_data: bytes, mime_type: str) -> tuple[bytes, str]:
    if mime_type == "image/svg+xml" or _is_svg(image_data):
        image_data = _svg_to_png(image_data)
        mime_type = "image/png"
    return image_data, mime_type


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _call_gemini(b64: str, mime_type: str, model: str) -> str | None:
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ]
        }]
    }

    url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    candidates = result.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p["text"] for p in parts if "text" in p]
        return "\n".join(texts) if texts else None
    return None


def _try_gemini(b64: str, mime_type: str) -> str | None:
    if not GEMINI_API_KEY:
        return None

    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                return _call_gemini(b64, mime_type, model)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    wait = (attempt + 1) * 15
                    print(f"  [Rate limit ({model}), жду {wait}с...]", flush=True)
                    time.sleep(wait)
                    continue
                if e.code == 429:
                    print(f"  [Квота {model} исчерпана]", flush=True)
                    break
                print(f"  [Gemini {model}: ошибка {e.code}]", flush=True)
                break
            except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
                print(f"  [Gemini {model}: {e}]", flush=True)
                break

    return None


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

def _call_groq(b64: str, mime_type: str, model: str) -> str | None:
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{b64}"
                }},
            ]
        }],
        "max_tokens": 2048,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL, data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    choices = result.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content")
    return None


def _try_groq(b64: str, mime_type: str) -> str | None:
    if not GROQ_API_KEY:
        return None

    for model in GROQ_MODELS:
        for attempt in range(3):
            try:
                result = _call_groq(b64, mime_type, model)
                if result:
                    return result
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    wait = (attempt + 1) * 15
                    print(f"  [Rate limit ({model}), жду {wait}с...]", flush=True)
                    time.sleep(wait)
                    continue
                if e.code == 429:
                    print(f"  [Квота {model} исчерпана]", flush=True)
                    break
                print(f"  [Groq {model}: ошибка {e.code}]", flush=True)
                break
            except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
                print(f"  [Groq {model}: {e}]", flush=True)
                break

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recognize_image(image_data: bytes, mime_type: str = "image/png") -> str | None:
    """Recognize image via Gemini, falling back to Groq."""
    if not GEMINI_API_KEY and not GROQ_API_KEY:
        return None

    image_data, mime_type = _prepare_image(image_data, mime_type)
    b64 = base64.b64encode(image_data).decode("ascii")

    # Try Gemini first
    result = _try_gemini(b64, mime_type)
    if result:
        return result

    # Fallback to Groq
    result = _try_groq(b64, mime_type)
    if result:
        return result

    return None


def recognize_image_from_url(image_url: str) -> str | None:
    """Download image by URL and recognize it."""
    try:
        with urllib.request.urlopen(image_url, timeout=15) as resp:
            ct = resp.headers.get("Content-Type", "image/png")
            data = resp.read()
        mime = ct.split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/png"
        return recognize_image(data, mime)
    except Exception as e:
        print(f"  [Ошибка загрузки изображения: {e}]", flush=True)
        return None
