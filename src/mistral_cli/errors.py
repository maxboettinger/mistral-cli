class MistralCliError(Exception):
    """Base exception for expected mistral-cli failures."""


class ConfigError(MistralCliError):
    """Raised when configuration cannot be read, validated, or updated."""
