"""Safe module-local errors; never expose credential, provider or database details."""

MESSAGES = {
    "CONFIGURATION_ERROR": "Account backend configuration is missing or invalid.",
    "INVALID_USERNAME": "The username does not meet the account rules.",
    "INVALID_PASSWORD": "The password does not meet the account rules.",
    "USERNAME_TAKEN": "This username is unavailable.",
    "INVALID_CREDENTIALS": "The username or password is incorrect.",
    "RATE_LIMITED": "Too many login attempts; try again later.",
    "INVALID_ARGUMENT": "Invalid conversation request.",
    "UNAUTHENTICATED": "A valid authenticated session is required.",
    "FORBIDDEN": "This conversation is not accessible.",
    "NOT_FOUND": "The conversation was not found.",
    "INVALID_STATE": "Saved conversation data is invalid; it was not reset.",
    "UNSUPPORTED_SCHEMA": "This saved-data version is not supported.",
    "UNSUPPORTED_VALUE": "The value cannot be safely stored.",
    "PAYLOAD_TOO_LARGE": "Saved conversation data exceeds the configured limit.",
    "CONFLICT": "The conversation changed; reload it before continuing.",
    "REQUEST_IN_PROGRESS": "This request is already in progress.",
    "RECONCILIATION_REQUIRED": "The request outcome must be checked before another execution.",
    "STORAGE_ERROR": "Conversation storage is unavailable.",
    "STORAGE_OUTCOME_UNKNOWN": "The save outcome is unknown; check using the same request ID.",
    "AGENT_ERROR": "The agent request did not complete; its outcome requires review.",
    "IDENTITY_CHANGED": "The authenticated session changed during the request.",
}


class ConversationError(Exception):
    """Machine-readable failure with a fixed user-safe message."""

    def __init__(self, code: str) -> None:
        self.code = code if code in MESSAGES else "AGENT_ERROR"
        super().__init__(MESSAGES[self.code])
