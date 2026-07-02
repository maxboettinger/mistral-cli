class MistralCliError(Exception):
    """Base exception for expected mistral-cli failures."""


class ConfigError(MistralCliError):
    """Raised when configuration cannot be read, validated, or updated."""


class InputError(MistralCliError):
    """Raised when command input cannot be resolved or validated."""
