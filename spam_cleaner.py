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

load_dotenv()

SCOPES = ["https://mail.google.com/"]

CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")
CLEAN_INTERVAL_SECONDS = int(os.getenv("SPAM_CLEAN_INTERVAL", str(12 * 3600)))
BATCH_SIZE = 1000  # Gmail batchDelete max

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def authenticate_gmail() -> Credentials:
    """Load or create OAuth2 credentials."""
    tokenPath = Path(TOKEN_FILE)
    credentials = None

    if tokenPath.exists():
        credentials = Credentials.from_authorized_user_file(str(tokenPath), SCOPES)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            tokenPath.write_text(credentials.to_json())
            logger.info("Token refreshed successfully")
            return credentials
        except Exception as refreshError:
            logger.warning("Token refresh failed: %s", refreshError)
            credentials = None

    secretPath = Path(CLIENT_SECRET_FILE)
    if not secretPath.exists():
        logger.error("Client secret file not found: %s", CLIENT_SECRET_FILE)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(secretPath), SCOPES)
    credentials = flow.run_local_server(port=0)
    tokenPath.write_text(credentials.to_json())
    logger.info("Authentication successful, token saved")
    return credentials


def build_gmail_service(credentials: Credentials):
    """Build and return Gmail API service."""
    return build("gmail", "v1", credentials=credentials)


def fetch_all_spam_ids(service) -> list[str]:
    """Fetch all message IDs in the SPAM folder, handling pagination."""
    messageIds: list[str] = []
    pageToken: str | None = None

    while True:
        try:
            kwargs: dict = {
                "userId": "me",
                "labelIds": ["SPAM"],
                "maxResults": 500,
            }
            if pageToken:
                kwargs["pageToken"] = pageToken

            result = service.users().messages().list(**kwargs).execute()
        except Exception as fetchError:
            logger.error("Failed to list spam messages: %s", fetchError)
            break

        messages = result.get("messages", [])
        messageIds.extend(m["id"] for m in messages)

        pageToken = result.get("nextPageToken")
        if not pageToken:
            break

    return messageIds


def batch_delete_messages(service, messageIds: list[str]) -> int:
    """Permanently delete messages in batches. Returns count deleted."""
    totalDeleted = 0

    for offset in range(0, len(messageIds), BATCH_SIZE):
        chunk = messageIds[offset : offset + BATCH_SIZE]
        try:
            service.users().messages().batchDelete(
                userId="me",
                body={"ids": chunk},
            ).execute()
            totalDeleted += len(chunk)
            logger.info("Deleted batch of %d messages (%d total so far)", len(chunk), totalDeleted)
        except Exception as deleteError:
            logger.error("Batch delete failed for chunk at offset %d: %s", offset, deleteError)

    return totalDeleted


def clean_spam(service) -> None:
    """Fetch and permanently delete all spam messages."""
    logger.info("Starting spam cleanup...")
    spamIds = fetch_all_spam_ids(service)

    if not spamIds:
        logger.info("Spam folder is already empty")
        return

    logger.info("Found %d spam messages to delete", len(spamIds))
    deleted = batch_delete_messages(service, spamIds)
    logger.info("Spam cleanup complete. %d messages permanently deleted.", deleted)


def main() -> None:
    logger.info("Starting Spam Cleaner (interval: %ds)", CLEAN_INTERVAL_SECONDS)

    credentials = authenticate_gmail()
    service = build_gmail_service(credentials)

    while True:
        try:
            clean_spam(service)
        except Exception as cycleError:
            logger.error("Error during spam cleanup: %s", cycleError, exc_info=True)

        logger.info("Next cleanup in %d hours.", CLEAN_INTERVAL_SECONDS // 3600)
        time.sleep(CLEAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
