import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google import genai

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

SYSTEM_LABELS_TO_EXCLUDE = {
    "INBOX", "SENT", "DRAFT", "SPAM", "TRASH",
    "STARRED", "UNREAD", "IMPORTANT",
    "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
    "CHAT",
}

AUTO_SORTED_LABEL_NAME = "AutoSorted"
MAX_MESSAGES_PER_CYCLE = 10
GEMINI_DELAY_SECONDS = 7
DAILY_CALL_LIMIT = 240
RATE_LIMIT_WAIT_SECONDS = 60
LABEL_REFRESH_INTERVAL = 10

CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def authenticate_gmail() -> Credentials:
    """Load or create OAuth2 credentials, converting web type to installed."""
    token_path = Path(TOKEN_FILE)
    credentials = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            token_path.write_text(credentials.to_json())
            logger.info("Token refreshed successfully")
            return credentials
        except Exception as refreshError:
            logger.warning("Token refresh failed: %s", refreshError)
            credentials = None

    secret_path = Path(CLIENT_SECRET_FILE)
    if not secret_path.exists():
        logger.error("Client secret file not found: %s", CLIENT_SECRET_FILE)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    credentials = flow.run_local_server(port=0)
    token_path.write_text(credentials.to_json())
    logger.info("Authentication successful, token saved")
    return credentials


def build_gmail_service(credentials: Credentials):
    """Build and return Gmail API service."""
    return build("gmail", "v1", credentials=credentials)


def build_gemini_client() -> genai.Client:
    """Build and return Gemini client."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set in .env")
        sys.exit(1)
    return genai.Client(api_key=GEMINI_API_KEY)


def ensure_auto_sorted_label(service) -> str:
    """Find or create the AutoSorted label, return its ID."""
    results = service.users().labels().list(userId="me").execute()
    allLabels = results.get("labels", [])

    for label in allLabels:
        if label["name"] == AUTO_SORTED_LABEL_NAME:
            logger.info("Found existing AutoSorted label: %s", label["id"])
            return label["id"]

    labelBody = {
        "name": AUTO_SORTED_LABEL_NAME,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=labelBody).execute()
    logger.info("Created AutoSorted label: %s", created["id"])
    return created["id"]


def fetch_user_labels(service) -> dict[str, str]:
    """Fetch user labels, excluding system labels and AutoSorted. Returns {name: id}."""
    results = service.users().labels().list(userId="me").execute()
    allLabels = results.get("labels", [])

    userLabels = {}
    for label in allLabels:
        labelName = label["name"]
        labelType = label.get("type", "")
        if labelType != "user":
            continue
        if labelName == AUTO_SORTED_LABEL_NAME:
            continue
        if labelName.upper() in SYSTEM_LABELS_TO_EXCLUDE:
            continue
        userLabels[labelName] = label["id"]

    logger.info("Fetched %d user labels", len(userLabels))
    return userLabels


def fetch_unprocessed_messages(service) -> list[dict]:
    """Fetch unprocessed inbox messages (max MAX_MESSAGES_PER_CYCLE)."""
    query = "in:inbox -label:AutoSorted"
    try:
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=MAX_MESSAGES_PER_CYCLE)
            .execute()
        )
    except Exception as fetchError:
        logger.error("Failed to fetch messages: %s", fetchError)
        return []

    messages = results.get("messages", [])
    logger.info("Found %d unprocessed messages", len(messages))
    return messages


def extract_email_content(service, messageId: str) -> tuple[str, str]:
    """Extract subject and plain text body from an email. Returns (subject, body)."""
    try:
        message = (
            service.users()
            .messages()
            .get(userId="me", id=messageId, format="full")
            .execute()
        )
    except Exception as getError:
        logger.error("Failed to get message %s: %s", messageId, getError)
        return "", ""

    headers = message.get("payload", {}).get("headers", [])
    subject = ""
    for header in headers:
        if header["name"].lower() == "subject":
            subject = header["value"]
            break

    body = _extract_plain_text(message.get("payload", {}))
    if body and len(body) > 2000:
        body = body[:2000]

    return subject, body


def _extract_plain_text(payload: dict) -> str:
    """Recursively search MIME parts for text/plain content."""
    mimeType = payload.get("mimeType", "")

    if mimeType == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return ""

    parts = payload.get("parts", [])
    for part in parts:
        result = _extract_plain_text(part)
        if result:
            return result

    return ""


def classify_email(
    geminiClient: genai.Client,
    subject: str,
    body: str,
    labelNames: list[str],
) -> str | None:
    """Ask Gemini to classify the email. Returns label name or None."""
    prompt = (
        "You are an email classifier. Pick the single most appropriate label.\n\n"
        f"Available labels: {json.dumps(labelNames)}\n\n"
        f"Email subject: {subject}\n"
        f"Email body: {body}\n\n"
        'Respond with ONLY a JSON object: {"label": "ExactLabelName"}\n'
        'If no label fits: {"label": "NONE"}'
    )

    try:
        response = geminiClient.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
    except Exception as geminiError:
        errorMessage = str(geminiError)
        if "429" in errorMessage or "RESOURCE_EXHAUSTED" in errorMessage:
            logger.warning("Rate limited by Gemini, waiting %ds", RATE_LIMIT_WAIT_SECONDS)
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            return None
        logger.error("Gemini API error: %s", geminiError)
        return None

    rawText = response.text.strip()
    rawText = rawText.removeprefix("```json").removesuffix("```").strip()

    try:
        parsed = json.loads(rawText)
        labelChoice = parsed.get("label", "NONE")
        if labelChoice == "NONE" or labelChoice not in labelNames:
            logger.info("Gemini returned no valid label: %s", labelChoice)
            return None
        return labelChoice
    except json.JSONDecodeError:
        logger.warning("Failed to parse Gemini response: %s", rawText)
        return None


def apply_labels(service, messageId: str, labelIds: list[str]) -> bool:
    """Apply label IDs to a message."""
    try:
        service.users().messages().modify(
            userId="me",
            id=messageId,
            body={"addLabelIds": labelIds},
        ).execute()
        return True
    except Exception as labelError:
        logger.error("Failed to apply labels to %s: %s", messageId, labelError)
        return False


def process_single_message(
    service,
    geminiClient: genai.Client,
    messageId: str,
    userLabels: dict[str, str],
    autoSortedLabelId: str,
) -> bool:
    """Process a single email: extract, classify, apply labels. Returns True if a Gemini call was made."""
    subject, body = extract_email_content(service, messageId)
    if not subject and not body:
        logger.warning("Empty email content for %s, marking as processed", messageId)
        apply_labels(service, messageId, [autoSortedLabelId])
        return False

    labelNames = list(userLabels.keys())
    if not labelNames:
        logger.warning("No user labels available for classification")
        apply_labels(service, messageId, [autoSortedLabelId])
        return False

    chosenLabel = classify_email(geminiClient, subject, body, labelNames)

    labelsToApply = [autoSortedLabelId]
    if chosenLabel:
        labelsToApply.append(userLabels[chosenLabel])
        logger.info("Email '%s' → label '%s'", subject[:50], chosenLabel)
    else:
        logger.info("Email '%s' → no matching label", subject[:50])

    apply_labels(service, messageId, labelsToApply)
    return True


def run_poll_cycle(
    service,
    geminiClient: genai.Client,
    userLabels: dict[str, str],
    autoSortedLabelId: str,
    dailyCallCount: int,
) -> int:
    """Process all unprocessed messages in one cycle. Returns updated daily call count."""
    messages = fetch_unprocessed_messages(service)
    if not messages:
        return dailyCallCount

    for index, message in enumerate(messages):
        if dailyCallCount >= DAILY_CALL_LIMIT:
            logger.warning("Daily Gemini call limit reached (%d), skipping remaining", DAILY_CALL_LIMIT)
            break

        madeCall = process_single_message(
            service, geminiClient, message["id"], userLabels, autoSortedLabelId,
        )
        if madeCall:
            dailyCallCount += 1

        if index < len(messages) - 1:
            time.sleep(GEMINI_DELAY_SECONDS)

    return dailyCallCount


def main():
    logger.info("Starting Email Sorter")

    credentials = authenticate_gmail()
    service = build_gmail_service(credentials)
    geminiClient = build_gemini_client()

    autoSortedLabelId = ensure_auto_sorted_label(service)
    userLabels = fetch_user_labels(service)
    logger.info("Available labels: %s", list(userLabels.keys()))

    dailyCallCount = 0
    cycleCount = 0
    dailyResetTime = time.time()

    while True:
        try:
            elapsed = time.time() - dailyResetTime
            if elapsed >= 86400:
                dailyCallCount = 0
                dailyResetTime = time.time()
                logger.info("Daily call counter reset")

            if cycleCount % LABEL_REFRESH_INTERVAL == 0 and cycleCount > 0:
                userLabels = fetch_user_labels(service)
                logger.info("Labels refreshed: %s", list(userLabels.keys()))

            dailyCallCount = run_poll_cycle(
                service, geminiClient, userLabels, autoSortedLabelId, dailyCallCount,
            )
            cycleCount += 1

            logger.info(
                "Cycle %d complete. Daily calls: %d/%d. Next poll in %ds.",
                cycleCount, dailyCallCount, DAILY_CALL_LIMIT, POLL_INTERVAL,
            )
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutting down")
            break
        except Exception as loopError:
            logger.error("Error in poll cycle: %s", loopError, exc_info=True)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
