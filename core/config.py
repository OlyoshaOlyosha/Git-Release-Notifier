"""Central configuration constants. Reads the bot token from .env."""

import os

from dotenv import load_dotenv

load_dotenv()  # loads .env from the current working directory (project root)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")  # Optional token to raise GitHub API limits to 5000 req/h
SUBSCRIPTIONS_FILE: str = "subscriptions.json"
CHECK_INTERVAL_SEC: int = 3600  # 1 hour between full check cycles
API_DELAY_SEC: float = 3.0  # Delay between GitHub API requests to respect rate limits

# Pagination
REPOS_PER_PAGE: int = 10  # Number of repos per page in the list

# Logging
LOG_DIR: str = "logs"
LOG_FILE: str = "bot.log"
LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT: int = 3  # Keep up to 3 rotated files

PROXY_URL: str = os.getenv("PROXY_URL", "")
ADMIN_USER_ID: int = int(os.getenv("ADMIN_USER_ID", "0"))  # 0 = disabled; admin receives API-failure alerts
