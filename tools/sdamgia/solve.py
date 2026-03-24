"""Solve sdamgia.ru tests.

Usage: python tools/sdamgia/solve.py <url>

1. Opens test, logs in if needed
2. Finds answers from sdamgia database (by direct link or search)
3. Fills answers into the form and submits
4. Saves unsolved problems to tools/sdamgia/unsolved/ for AI solving
"""
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from tools.sdamgia.image_recognition import recognize_image_from_url

DIR = os.path.dirname(__file__)
UNSOLVED_DIR = os.path.join(DIR, "unsolved")


def _create_driver():
    """Create headless Chrome without proxy (sdamgia is blocked by proxy)."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=opts)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean_text(text):
    """Remove soft hyphens, LaTeX fragments, split glued words, normalize whitespace."""
    text = text.replace('\u00AD', '')
    text = re.sub(r'\\[a-zA-Z_]+', ' ', text)
    text = re.sub(r'(?<=\w)(?=\d)', ' ', text)
    text = re.sub(r'(?<=\d)(?=\w)', ' ', text)
    text = re.sub(r'([а-яёa-z])([А-ЯЁA-Z]{2})', r'\1 \2', text)
    text = re.sub(r'([А-ЯЁA-Z]{2,})([а-яёa-z])', r'\1 \2', text)
    return ' '.join(text.split())


def _get_problem_text(driver, element):
    """Extract problem body text from a .prob_maindiv element."""
    text = driver.execute_script("""
        var el = arguments[0];
        var imgs = el.querySelectorAll('img.tex');
        imgs.forEach(function(img) {
            if (img.alt && !img.dataset.altInjected) {
                var span = document.createElement('span');
                span.textContent = ' ' + img.alt + ' ';
                span.className = '_alt_injected';
                img.parentNode.insertBefore(span, img.nextSibling);
                img.dataset.altInjected = '1';
            }
        });
        var text = el.innerText;
        el.querySelectorAll('._alt_injected').forEach(function(s) { s.remove(); });
        imgs.forEach(function(img) { delete img.dataset.altInjected; });
        return text;
    """, element)

    lines = text.split('\n')
    body_lines = []
    skip = True
    for line in lines:
        stripped = line.strip()
        if skip:
            if re.match(r'^(Тип\s+\d+(\s+№.*)?|№.*|\d+|i|)\s*$', stripped):
                continue
            skip = False
        body_lines.append(stripped)
    return _clean_text(' '.join(body_lines))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _login_sdamgia(driver, url):
    """Log in to sdamgia if credentials are available and page requires auth."""
    login = os.getenv('SDAMGIA_LOGIN')
    password = os.getenv('SDAMGIA_PASSWORD')
    if not login or not password:
        return

    user_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[name="user"]')
    pwd_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[name="password"]')
    if not user_inputs or not pwd_inputs:
        return

    print("  Авторизация на sdamgia...", flush=True)
    user_inputs[0].send_keys(login)
    pwd_inputs[0].send_keys(password)
    pwd_inputs[0].send_keys(Keys.ENTER)
    time.sleep(5)

    driver.get(url)
    time.sleep(5)


# ---------------------------------------------------------------------------
# Answer extraction
# ---------------------------------------------------------------------------

def _extract_answer(driver):
    """Extract the answer from a sdamgia problem page."""
    sol_els = driver.find_elements(By.CSS_SELECTOR, '.solution')
    if not sol_els:
        return None

    text = sol_els[0].text

    m = re.search(r'[Оо]т[\u00AD]?в[\u00AD]?е[\u00AD]?т\s*:\s*(.+?)\.?\s*$', text, re.MULTILINE)
    if m:
        answer = m.group(1).strip().rstrip('.')
        answer = answer.replace('\u00AD', '')
        if answer:
            return answer

    tables = sol_els[0].find_elements(By.CSS_SELECTOR, 'table')
    if tables:
        last_table = tables[-1]
        rows = last_table.find_elements(By.TAG_NAME, 'tr')
        if len(rows) == 2:
            headers = [td.text.strip() for td in rows[0].find_elements(By.TAG_NAME, 'td')]
            values = [td.text.strip() for td in rows[1].find_elements(By.TAG_NAME, 'td')]
            if headers and values:
                pairs = [f"{h}={v}" for h, v in zip(headers, values) if h and v]
                if pairs:
                    return ', '.join(pairs)

    return None


# ---------------------------------------------------------------------------
# Search fallback
# ---------------------------------------------------------------------------

def _search_and_extract(driver, base_url, prob_text, file_ids=None):
    """Search for a problem by text and extract its answer."""
    if not prob_text:
        return None, None
    if file_ids is None:
        file_ids = set()

    NOISE = {
        'левая', 'правая', 'круглая', 'скобка', 'скобки', 'дробь', 'дроби',
        'числитель', 'знаменатель', 'конец', 'начало', 'аргумента', 'степени',
        'степень', 'корень', 'квадратный', 'кубический',
    }
    all_words = prob_text.split()
    candidates = []
    for w in all_words:
        clean_w = re.sub(r'[^\w]', '', w)
        if clean_w and len(clean_w) >= 4 and clean_w.lower() not in NOISE:
            candidates.append(clean_w)

    if not candidates:
        candidates = [re.sub(r'[^\w]', '', w) for w in all_words if w][:5]

    n = len(candidates)
    first_quarter = candidates[:n // 4] if n > 8 else candidates[:2]
    second_half = candidates[n // 2:] if n > 8 else candidates[2:]

    head = first_quarter[:4]
    if len(second_half) > 8:
        step = max(1, len(second_half) // 8)
        tail = second_half[::step][:8]
    else:
        tail = second_half

    selected = head + tail

    query_words = []
    query_len = 0
    for w in selected:
        encoded_len = len(urllib.parse.quote(w))
        if query_len + encoded_len + 1 > 500:
            break
        query_words.append(w)
        query_len += encoded_len + 1

    query = ' '.join(query_words)
    encoded_query = urllib.parse.quote(query)

    max_pages = 3 if file_ids else 1
    best_match = None
    best_ratio = 0
    clean_original = _clean_text(prob_text).lower()
    orig_words = clean_original.split()
    orig_set = set(orig_words)
    orig_bigrams = set(zip(orig_words, orig_words[1:]))

    for page in range(1, max_pages + 1):
        search_url = f"{base_url}/search?search={encoded_query}&page={page}"
        driver.get(search_url)
        time.sleep(3)

        results = driver.find_elements(By.CSS_SELECTOR, '.prob_maindiv')
        if not results:
            break

        for r in results:
            if file_ids:
                cand_file_ids = set(re.findall(r'get_file\?id=(\d+)', r.get_attribute('innerHTML')))
                if file_ids & cand_file_ids:
                    best_match = r
                    best_ratio = 1.0
                    break

            if page == 1:
                candidate_text = _get_problem_text(driver, r)
                clean_candidate = _clean_text(candidate_text).lower()

                cand_words = clean_candidate.split()
                cand_set = set(cand_words)
                if not orig_set or not cand_set:
                    continue

                common = orig_set & cand_set
                set_ratio = len(common) / max(len(orig_set), len(cand_set))

                cand_bigrams = set(zip(cand_words, cand_words[1:]))
                if orig_bigrams and cand_bigrams:
                    bigram_ratio = len(orig_bigrams & cand_bigrams) / max(len(orig_bigrams), len(cand_bigrams))
                else:
                    bigram_ratio = set_ratio

                ratio = (set_ratio + bigram_ratio) / 2

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = r

        if best_ratio >= 1.0:
            break

    if not best_match or best_ratio < 0.2:
        return None, None

    links = best_match.find_elements(By.CSS_SELECTOR, 'a[href*="problem?id="]')
    if not links:
        return None, None

    href = links[0].get_attribute('href')
    m = re.search(r'problem\?id=(\d+)', href)
    if not m:
        return None, None

    problem_id = m.group(1)

    driver.get(f"{base_url}/problem?id={problem_id}")
    time.sleep(2)
    answer = _extract_answer(driver)

    return answer, problem_id


# ---------------------------------------------------------------------------
# Unsolved problems export
# ---------------------------------------------------------------------------

def _read_ods_as_csv(data):
    """Parse ODS binary data into tab-separated text."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            content_xml = zf.read('content.xml')
        root = ET.fromstring(content_xml)
        lines = []
        for table in root.iter('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table'):
            for row in table.iter('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row'):
                cells = []
                for cell in row.iter('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell'):
                    repeat = int(cell.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated', '1'))
                    text_parts = []
                    for p in cell.iter('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p'):
                        text_parts.append(p.text or '')
                    cell_text = ' '.join(text_parts).strip()
                    cells.extend([cell_text] * min(repeat, 100))
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    lines.append('\t'.join(cells))
        return '\n'.join(lines)
    except Exception as e:
        return f"[Ошибка чтения ODS: {e}]"


def _download_file(url):
    """Download a file and return (filename, content_type, data)."""
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        cd = resp.headers.get('Content-Disposition', '')
        ct = resp.headers.get('Content-Type', '')
        data = resp.read()
        resp.close()
        fn_match = re.search(r'filename="?([^"]+)"?', cd)
        fn = fn_match.group(1) if fn_match else url.rsplit('/', 1)[-1]
        return fn, ct, data
    except Exception:
        return None, None, None


def _save_unsolved(unsolved, base_url):
    """Save unsolved problems to a text file for AI solving."""
    os.makedirs(UNSOLVED_DIR, exist_ok=True)

    m = re.match(r'https?://([^.]+)', base_url)
    subject = m.group(1) if m else "unknown"

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(UNSOLVED_DIR, f"{subject}_{timestamp}.txt")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Нерешённые задания ({subject})\n")
        f.write(f"Источник: {base_url}\n")
        f.write("=" * 60 + "\n\n")

        for prob in unsolved:
            f.write(f"--- {prob['number']} (#{prob['problem_id']}) ---\n\n")
            f.write(prob['prob_text'] + "\n")

            for dl in prob.get('download_links', []):
                url = dl['url']
                fn, ct, data = _download_file(url)
                if not data:
                    f.write(f"\n[Файл не скачан: {url}]\n")
                    continue

                ext = fn.rsplit('.', 1)[-1].lower() if fn and '.' in fn else ''

                if ext == 'txt':
                    text = data.decode('utf-8', errors='replace')
                    f.write(f"\n--- Файл: {fn} ---\n")
                    f.write(text + "\n")
                elif ext == 'ods':
                    csv_text = _read_ods_as_csv(data)
                    f.write(f"\n--- Файл: {fn} (таблица) ---\n")
                    f.write(csv_text + "\n")
                elif ext == 'odt':
                    try:
                        with zipfile.ZipFile(io.BytesIO(data)) as zf:
                            content_xml = zf.read('content.xml')
                        root = ET.fromstring(content_xml)
                        texts = []
                        for p in root.iter('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p'):
                            texts.append(p.text or '')
                        f.write(f"\n--- Файл: {fn} (документ) ---\n")
                        f.write('\n'.join(texts) + "\n")
                    except Exception:
                        saved_path = os.path.join(UNSOLVED_DIR, f"{subject}_{timestamp}_{fn}")
                        with open(saved_path, 'wb') as bf:
                            bf.write(data)
                        f.write(f"\n[Файл сохранён: {saved_path}]\n")
                else:
                    saved_path = os.path.join(UNSOLVED_DIR, f"{subject}_{timestamp}_{fn}")
                    with open(saved_path, 'wb') as bf:
                        bf.write(data)
                    f.write(f"\n[Файл сохранён: {saved_path}]\n")

            img_only = [u for u in prob.get('img_urls', [])
                        if not any(u == dl['url'] for dl in prob.get('download_links', []))]
            if img_only:
                f.write("\nИзображения:\n")
                for url in img_only:
                    if url.startswith('/'):
                        url = base_url + url
                    elif not url.startswith('http'):
                        url = base_url + '/' + url
                    f.write(f"  {url}\n")
                    description = recognize_image_from_url(url)
                    if description:
                        f.write(f"  [Описание изображения]:\n")
                        for line in description.strip().split('\n'):
                            f.write(f"    {line}\n")
                    else:
                        f.write(f"  [Не удалось распознать изображение]\n")

            f.write("\n\n")

    print(f"  Нерешённые сохранены: {filepath}", flush=True)
    return filepath


# ---------------------------------------------------------------------------
# Parse answers from database
# ---------------------------------------------------------------------------

def _parse_answers(driver, url):
    """Parse answers from sdamgia database for a test.

    Returns list of dicts: number, problem_id, answer, prob_text, img_urls, download_links.
    """
    print(f"Открываю тест: {url}", flush=True)
    driver.get(url)
    time.sleep(5)

    probs = driver.find_elements(By.CSS_SELECTOR, '.prob_maindiv')
    if not probs:
        _login_sdamgia(driver, url)
        probs = driver.find_elements(By.CSS_SELECTOR, '.prob_maindiv')

    if not probs:
        print("  Задания не найдены на странице", flush=True)
        return [], None

    base_url = re.match(r'(https?://[^/]+)', url).group(1)

    # Collect info about each problem
    problems = []
    for p in probs:
        nums_el = p.find_elements(By.CSS_SELECTOR, '.prob_nums')
        num_text = " ".join(nums_el[0].text.split()) if nums_el else ""

        # Extract task number for form filling (e.g. "Тип 5" -> "5")
        num_match = re.search(r'Тип\s+(\d+)', num_text)
        task_num = num_match.group(1) if num_match else ""

        links = p.find_elements(By.CSS_SELECTOR, 'a[href*="problem?id="]')
        problem_id = None
        if links:
            href = links[0].get_attribute('href')
            m = re.search(r'problem\?id=(\d+)', href)
            if m:
                problem_id = m.group(1)

        prob_text = _get_problem_text(driver, p)
        innerHTML = p.get_attribute('innerHTML')
        file_ids = set(re.findall(r'get_file\?id=(\d+)', innerHTML))
        img_urls = list(set(re.findall(r'(?:src|href)=["\']([^"\']*get_file\?id=\d+[^"\']*)["\']', innerHTML)))

        download_links = []
        for a in p.find_elements(By.TAG_NAME, 'a'):
            href = a.get_attribute('href') or ''
            if 'get_file' in href:
                download_links.append({'text': a.text.strip(), 'url': href})

        problems.append({
            'number': num_text,
            'task_num': task_num,
            'problem_id': problem_id,
            'prob_text': prob_text,
            'file_ids': file_ids,
            'img_urls': img_urls,
            'download_links': download_links,
        })

    print(f"  Найдено заданий: {len(problems)}", flush=True)
    print("  Ищу ответы в базе...", flush=True)

    # Get answers for each problem
    results = []
    for i, prob in enumerate(problems):
        if prob['problem_id']:
            problem_url = f"{base_url}/problem?id={prob['problem_id']}"
            driver.get(problem_url)
            time.sleep(2)
            answer = _extract_answer(driver)
        else:
            answer, found_id = _search_and_extract(driver, base_url, prob['prob_text'], prob['file_ids'])
            prob['problem_id'] = found_id or '?'

        status = answer if answer else "—"
        print(f"    {prob['number']}: {status}", flush=True)

        results.append({
            'number': prob['number'],
            'task_num': prob['task_num'],
            'problem_id': prob['problem_id'],
            'answer': answer,
            'prob_text': prob['prob_text'],
            'img_urls': prob['img_urls'],
            'download_links': prob['download_links'],
        })

    return results, base_url


# ---------------------------------------------------------------------------
# Fill and submit
# ---------------------------------------------------------------------------

def _fill_and_submit(driver, url, results):
    """Navigate to test page, fill answers and submit."""
    # Go back to test page
    driver.get(url)
    time.sleep(5)

    # May need login again (session could expire)
    probs = driver.find_elements(By.CSS_SELECTOR, '.prob_maindiv')
    if not probs:
        _login_sdamgia(driver, url)

    filled = 0
    for r in results:
        if not r['answer'] or not r['task_num']:
            continue
        inputs = driver.find_elements(By.CSS_SELECTOR, f"input[name^='answer_{r['task_num']}_']")
        if not inputs:
            print(f"  Поле ввода не найдено: {r['number']}", flush=True)
            continue
        inputs[0].clear()
        inputs[0].send_keys(str(r['answer']))
        filled += 1

    print(f"\n  Заполнено {filled}/{len(results)} ответов", flush=True)

    if filled == 0:
        print("  Нечего отправлять", flush=True)
        return

    # Submit
    try:
        driver.execute_script("submit_form()")
        time.sleep(8)
    except Exception as e:
        print(f"  Ошибка отправки: {e}", flush=True)
        return

    print(f"  Результаты: {driver.current_url}", flush=True)

    body = driver.find_element(By.TAG_NAME, "body").text
    body = body.replace('\u00AD', '').replace('\u200b', '')

    summary = re.search(r'Решено\s+(\d+)\s+из\s+(\d+).*?набрано\s+(\d+)', body)
    if summary:
        print(f"  Решено {summary.group(1)}/{summary.group(2)}, баллов: {summary.group(3)}", flush=True)

    for match in re.finditer(r'Ваш ответ:\s*(.+?)\.\s*Правильный ответ:\s*(.+)', body):
        your = match.group(1).strip()
        correct = match.group(2).strip()
        if your != correct:
            print(f"  ОШИБКА: ваш={your}, верный={correct}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

MANUAL_ANSWERS_FILE = os.path.join(DIR, "manual_answers.json")


def _cleanup():
    """Remove unsolved dir and downloaded files after the run."""
    import shutil
    if os.path.isdir(UNSOLVED_DIR):
        shutil.rmtree(UNSOLVED_DIR, ignore_errors=True)
    if os.path.exists(MANUAL_ANSWERS_FILE):
        os.remove(MANUAL_ANSWERS_FILE)


def main():
    if len(sys.argv) < 2:
        print("Использование: python tools/sdamgia/solve.py <ссылка на тест>")
        print("  --no-submit  только найти ответы, не отправлять")
        sys.exit(1)

    url = sys.argv[1]
    no_submit = "--no-submit" in sys.argv

    driver = _create_driver()

    try:
        results, base_url = _parse_answers(driver, url)

        if not results:
            print("Не удалось распарсить тест", flush=True)
            return

        solved = [r for r in results if r['answer']]
        unsolved = [r for r in results if not r['answer']]
        print(f"\n  Найдено ответов: {len(solved)}/{len(results)}", flush=True)

        # Save unsolved for manual solving
        if unsolved:
            unsolved_path = _save_unsolved(unsolved, base_url)

            # Wait for manual answers
            print(f"\n  Жду ручные ответы в {MANUAL_ANSWERS_FILE}", flush=True)
            print(f"  Формат: {{\"7\": \"198\", \"13\": \"DEFA\", ...}}", flush=True)

            while not os.path.exists(MANUAL_ANSWERS_FILE):
                time.sleep(2)

            import json
            with open(MANUAL_ANSWERS_FILE, "r", encoding="utf-8") as f:
                manual = json.load(f)

            # Merge manual answers
            merged = 0
            for r in results:
                if not r['answer'] and r['task_num'] in manual:
                    r['answer'] = str(manual[r['task_num']])
                    merged += 1
                    print(f"    Тип {r['task_num']}: {r['answer']} (ручной)", flush=True)

            print(f"  Добавлено ручных ответов: {merged}", flush=True)

            # Cleanup
            os.remove(MANUAL_ANSWERS_FILE)

        total_solved = sum(1 for r in results if r['answer'])
        print(f"\n  Итого ответов: {total_solved}/{len(results)}", flush=True)

        # Fill and submit
        if not no_submit and total_solved:
            print("  Отправляю ответы...", flush=True)
            _fill_and_submit(driver, url, results)
        elif no_submit:
            print("\n--- Ответы ---", flush=True)
            for r in results:
                ans = r['answer'] if r['answer'] else "—"
                safe = ans.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
                print(f"  {r['number']}: {safe}", flush=True)

    except Exception as e:
        print(f"Ошибка: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()
        _cleanup()


if __name__ == "__main__":
    main()
