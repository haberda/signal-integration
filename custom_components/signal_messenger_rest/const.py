"""Integration constants."""

DOMAIN = "signal_messenger_rest"
EVENT_MESSAGE = f"{DOMAIN}_message_received"
CONF_ACCOUNT = "account"
CONF_DESTINATIONS = "destinations"
CONF_RECEIVE = "receive"
CONF_SENDERS = "allowed_senders"
CONF_GROUPS = "allowed_groups"
CONF_INTERVAL = "poll_interval"
DEFAULT_INTERVAL = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MODES = {"normal", "native", "json-rpc", "json-rpc-native"}

EVENT_REACTION = f"{DOMAIN}_reaction_received"

CONF_READ_RECEIPTS = "send_read_receipts"
