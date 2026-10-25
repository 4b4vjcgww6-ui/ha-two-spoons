"""Constants for the Two Spoons integration."""

from typing import Final

DOMAIN: Final = "two_spoons"

# The HA API lives under this path on the Two Spoons site.
API_PATH: Final = "/api/ha/v1"

CONF_API_TOKEN: Final = "api_token"
CONF_POLL_MINUTES: Final = "poll_minutes"

DEFAULT_POLL_MINUTES: Final = 5
MIN_POLL_MINUTES: Final = 2
MAX_POLL_MINUTES: Final = 60

REQUEST_TIMEOUT: Final = 30  # seconds

# Extra names Assist should know a store by, keyed by store code. Written
# once, when the store's list entity is first created.
STORE_ALIASES: Final[dict[str, str]] = {"WW": "Woolworths"}

# API error codes the integration reacts to.
ERR_READ_ONLY: Final = "ready_meal_child_readonly"
ERR_NOT_FOUND: Final = "not_found"
ERR_UNKNOWN_STORE: Final = "unknown_store"

# The API's limit on an item's text (POST /items), checked before sending.
MAX_ITEM_TEXT: Final = 200
