# Email Sorter

Automated Gmail email classifier powered by Gemini 2.5 Flash. Polls your inbox every 60 seconds, reads subject and body, then assigns the most appropriate Gmail label using AI.

## How It Works

1. Fetches unprocessed emails from Gmail inbox
2. Extracts subject and plain text body (recursive MIME parsing)
3. Sends content to Gemini with your existing Gmail labels as options
4. Applies the chosen label + marks as processed with `AutoSorted` label
5. Skips already-processed emails on next cycle

## Features

- **Smart classification** — Uses Gemini 2.5 Flash to match emails to your existing Gmail labels
- **Rate limit protection** — 7s delay between API calls, 240/day cap, 60s backoff on 429
- **Auto-refresh** — Labels are re-fetched every 10 cycles to pick up new ones
- **System label filtering** — Only user-created labels are sent to the LLM
- **Resilient** — Handles empty bodies, parse failures, and API errors gracefully

## Setup

### Prerequisites

- Python 3.12+
- Google Cloud project with Gmail API enabled
- OAuth 2.0 Client ID (Desktop app type)
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
```

### First Run

```bash
python3 main.py
```

A browser window will open for Gmail OAuth authorization. After granting access, `token.json` is saved and reused automatically.

### Deploy on VPS

Run auth locally first, then copy `token.json` to the server:

```bash
nohup python3 main.py > email_sorter.log 2>&1 &
```

## Rate Limits (Gemini Free Tier)

| Constraint | Value |
|---|---|
| Max messages per cycle | 10 |
| Delay between Gemini calls | 7s |
| Daily call limit | 240 (of 250) |
| Backoff on 429 | 60s |

## Tech Stack

- **Gmail API** — Email fetching and label management
- **Gemini 2.5 Flash** — Email classification via `google-genai`
- **OAuth 2.0** — Secure Gmail access with automatic token refresh
