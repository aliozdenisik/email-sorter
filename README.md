# Email Sorter

Automated Gmail toolkit powered by Gemini 2.5 Flash. Runs 24/7 on a VPS with two independent services:

- **`email_sorter.py`** — Polls inbox every 60 seconds and assigns Gmail labels using AI
- **`spam_cleaner.py`** — Permanently deletes all spam messages every 12 hours

## How It Works

### Email Sorter
1. Fetches unprocessed emails from Gmail inbox
2. Extracts subject and plain text body (recursive MIME parsing)
3. Sends content to Gemini with your existing Gmail labels as options
4. Applies the chosen label and marks as processed
5. Skips already-processed emails on next cycle

### Spam Cleaner
1. Fetches all message IDs in the SPAM folder (paginated)
2. Permanently deletes them in batches of 1000
3. Sleeps for 12 hours, then repeats

## Features

- **Smart classification** — Uses Gemini 2.5 Flash to match emails to your existing Gmail labels
- **Rate limit protection** — 7s delay between API calls, 240/day cap, 60s backoff on 429
- **Auto-refresh** — Labels are re-fetched every 10 cycles to pick up new ones
- **System label filtering** — Only user-created labels are sent to the LLM
- **Resilient** — Handles empty bodies, parse failures, and API errors gracefully
- **Spam auto-delete** — Spam folder is wiped every 12 hours (configurable via `SPAM_CLEAN_INTERVAL`)

## Setup

### Prerequisites

- Python 3.12+
- Google Cloud project with Gmail API enabled
- OAuth 2.0 Client ID (Desktop app type) with `https://mail.google.com/` scope
- Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)

### Installation

```bash
git clone https://github.com/aliozdenisik/email-sorter.git
cd email-sorter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Create a `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
CLIENT_SECRET_FILE=your_client_secret.json
TOKEN_FILE=token.json
POLL_INTERVAL=60
GEMINI_MODEL=gemini-2.5-flash
SPAM_CLEAN_INTERVAL=43200
```

### Authentication (local machine)

```bash
python3 email_sorter.py
```

A browser window will open for Gmail OAuth authorization. After granting access, `token.json` is saved and reused automatically. Run this locally before deploying to VPS.

### Deploy on VPS (systemd)

Copy all files to the server, then create two systemd services:

**`/etc/systemd/system/email-sorter.service`**
```ini
[Unit]
Description=Email Sorter Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/email-soreter
ExecStart=/root/email-soreter/venv/bin/python email_sorter.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/spam-cleaner.service`**
```ini
[Unit]
Description=Spam Cleaner Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/email-soreter
ExecStart=/root/email-soreter/venv/bin/python spam_cleaner.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now email-sorter spam-cleaner
```

### View Logs

```bash
journalctl -fu email-sorter
journalctl -fu spam-cleaner
```

## Rate Limits (Gemini Free Tier)

| Constraint | Value |
|---|---|
| Max messages per cycle | 10 |
| Delay between Gemini calls | 7s |
| Daily call limit | 240 (of 250) |
| Backoff on 429 | 60s |

## Tech Stack

- **Gmail API** — Email fetching, label management, and spam deletion
- **Gemini 2.5 Flash** — Email classification via `google-genai`
- **OAuth 2.0** — Secure Gmail access with `https://mail.google.com/` scope
