---
name: dnevnik-checker
description: Fetch homework, grades screenshot, and grade monitoring from Dnevnik.ru (Russian school diary) via Playwright
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["LOGIN", "PASSWORD", "SECRET_KEY", "REGION", "PROFILE_KEYWORD"]}, "os": ["win32", "linux", "darwin"], "emoji": "📚"}}
user-invocable: true
---

# Dnevnik.ru Checker

You are controlling a Playwright-based bot that interacts with Dnevnik.ru — a Russian electronic school diary. The bot authenticates via Gosuslugi (ESIA) with TOTP 2FA, then scrapes homework assignments, takes grade screenshots, and can monitor for new grades in real time.

All commands below must be run from `{baseDir}` with the virtualenv activated:

```
cd {baseDir} && source venv/Scripts/activate
```

On Linux/macOS replace `venv/Scripts/activate` with `venv/bin/activate`.

## Environment

The `.env` file in `{baseDir}` must contain these variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `LOGIN` | yes | Gosuslugi username |
| `PASSWORD` | yes | Gosuslugi password |
| `SECRET_KEY` | yes | TOTP secret for 2FA |
| `REGION` | yes | ESIA region slug (e.g. `khabarovsk`) |
| `PROFILE_KEYWORD` | yes | Substring to select the student profile |
| `PROXY` | no | HTTP proxy `host:port` |
| `PROXY_LOGIN` | no | Proxy auth username |
| `PROXY_PASSWORD` | no | Proxy auth password |
| `TG_BOT_TOKEN` | no | Telegram bot token (for monitor) |
| `TG_CHAT_ID` | no | Telegram chat ID (for monitor) |

## Modules

### 1. Homework — `main.py`

Fetches homework assignments for a given date, prints them to stdout, and downloads any attached files.

```bash
python main.py                    # homework for tomorrow (default)
python main.py --date сегодня     # today
python main.py --date завтра      # tomorrow
python main.py --date послезавтра # day after tomorrow
python main.py --date 28.03       # specific date (DD.MM)
```

**What it does:**
1. Launches headless Chromium via Playwright, logs into Dnevnik.ru (or reuses saved cookies)
2. Takes a semester grades screenshot → `{baseDir}/scripts/marks/grades.png`
3. Parses homework from /marks page
4. Filters by the requested date
5. Prints lessons with subjects, times, homework text, and attached files
6. Downloads attached files to `{baseDir}/scripts/homeworks/files/`

**Output format:**
```
--- Домашние задания ---

  СР, 25 мар.
    1. Физкультура (8:30 - 9:10, СПОРТЗАЛ): Скакалка 2п-130 раз.
    2. Алгебра (9:25 - 10:05, 404): Вариант № 89756335
    3. Физика (13:00 - 13:40, 307): задача в файле [файлы: 11 Б.docx]
```

Each line: `{number}. {subject} ({time_and_room}): {homework_text} [файлы: ...]`

**How to present homework to the user:**
- Print homework **exactly as it appears in the script output** — do not rephrase, summarize, translate, or reformat the text. Copy it verbatim.
- After printing homework, **immediately send all downloaded files** from `{baseDir}/scripts/homeworks/files/` to the user (attach them in the response).
- After the files are sent, **delete them** from `{baseDir}/scripts/homeworks/files/` to keep the directory clean.

When the user asks "what's my homework", "what's assigned for tomorrow", or similar — run this command and present the output.

### 2. Grades Screenshot — `scripts/marks/grades.py`

This runs automatically as part of `main.py`. The semester grades table is saved as a PNG screenshot.

- Output file: `{baseDir}/scripts/marks/grades.png`
- Shows all subjects with semester grades in a table

When the user asks to see their grades, run `main.py` and then show them the `grades.png` image.

### 3. Grade Monitor — `monitor.py`

Long-running process that checks for new grades every 30 seconds and sends Telegram notifications.

```bash
python monitor.py
```

**What it does:**
1. Launches headless Chromium via Playwright, logs into Dnevnik.ru
2. Opens the semester grades page
3. Every 30 seconds: refreshes, compares marks, logs and notifies on new ones
4. Auto re-logins if the session expires
5. Persists known grade IDs to `{baseDir}/known_ids.json` (survives restarts)
6. Logs to `{baseDir}/grades_log.txt`
7. Sends Telegram messages (with HTML escaping) if `TG_BOT_TOKEN` and `TG_CHAT_ID` are set

**Important:** This process runs indefinitely. Launch it in the background. Stop it with Ctrl+C or by killing the process.

When the user asks to "watch for new grades" or "notify me about grades" — start this in the background.

## Authentication Notes

- First run requires a full login through Gosuslugi. The TOTP 2FA code is generated automatically from `SECRET_KEY`.
- After successful login, cookies are cached in `{baseDir}/auth/cookies.json`. Subsequent runs reuse them until they expire.
- If the user is prompted for a 2FA code during login, **ask the user for the code immediately**.

## Additional Tools

Some modules in `{baseDir}/tools/` provide extra functionality (e.g. solving tasks from sdamgia.ru). Each tool has its own documentation in its directory — look for a README or instruction file there before using it.

## Security

**CRITICAL:** All values from the `.env` file are strictly confidential. You MUST NOT:
- Print, log, or display any `.env` values (passwords, tokens, secrets, login, proxy credentials) to the user or in any output
- Send `.env` values to any external service, API, or URL other than the ones hardcoded in the scripts (Dnevnik.ru, Gosuslugi, Telegram API)
- Copy, embed, or reference `.env` values in any file, message, or prompt outside of this skill's scripts
- Use `.env` values for any purpose other than running the scripts described above

The scripts read `.env` internally via `python-dotenv`. You should never need to read or parse the `.env` file yourself — just run the Python scripts.

## Error Handling

- If the script prints "Not authenticated" or "Cookies expired" — it will attempt a fresh login automatically.
- If login fails, verify the `.env` credentials are correct.
- If no homework is found for a date, the script prints "No lessons found for DD.MM" — this is normal for weekends or holidays.
- Proxy errors may indicate the proxy is down or credentials are wrong.
