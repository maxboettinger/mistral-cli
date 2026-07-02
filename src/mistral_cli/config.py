from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final

import tomli_w

from mistral_cli.errors import ConfigError

_CONFIG_NAMES: Final = {"api-key": "api_key"}
_SUPPORTED_KEYS: Final = {"config_version", *_CONFIG_NAMES.values()}


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_version: int = 1
    api_key: str | None = None


class ConfigStore:
    def __init__(
        self,
        path: Path,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path
        self._environ = os.environ if environ is None else environ

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()

        try:
            with self.path.open("rb") as config_file:
                data = tomllib.load(config_file)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(
                f"Could not parse configuration file {self.path}: {error}"
            ) from error
        except OSError as error:
            raise ConfigError(
                f"Could not read configuration file {self.path}: {error}"
            ) from error

        unknown_keys = data.keys() - _SUPPORTED_KEYS
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ConfigError(f"Unknown configuration key: {names}")

        config_version = data.get("config_version", 1)
        if type(config_version) is not int or config_version != 1:
            raise ConfigError(
                f"Unsupported config_version {config_version!r}; expected 1."
            )

        api_key = data.get("api_key")
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key.strip()
        ):
            raise ConfigError("Configured API key must be a non-blank string.")

        return AppConfig(config_version=config_version, api_key=api_key)

    def resolve_api_key(self) -> str:
        environment_key = self._environ.get("MISTRAL_API_KEY")
        if environment_key is not None and environment_key.strip():
            return environment_key

        configured_key = self.load().api_key
        if configured_key is not None:
            return configured_key

        raise ConfigError(
            "No API key configured. Set MISTRAL_API_KEY or run "
            "`mistral config set api-key`."
        )

    def set(self, name: str, value: str) -> None:
        field_name = self._field_name(name)
        if not value.strip():
            raise ConfigError(f"{name} must not be blank.")

        config = replace(self.load(), **{field_name: value})
        self._write(config)

    def unset(self, name: str) -> bool:
        field_name = self._field_name(name)
        config = self.load()
        if getattr(config, field_name) is None:
            return False

        self._write(replace(config, **{field_name: None}))
        return True

    def redacted(self) -> dict[str, int | str]:
        config = self.load()
        result: dict[str, int | str] = {"config_version": config.config_version}
        if config.api_key is not None:
            result["api_key"] = "********"
        return result

    @staticmethod
    def _field_name(name: str) -> str:
        try:
            return _CONFIG_NAMES[name]
        except KeyError as error:
            raise ConfigError(f"Unknown configuration name: {name}") from error

    def _write(self, config: AppConfig) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent.chmod(0o700)
        except OSError as error:
            raise ConfigError(
                f"Could not prepare configuration directory {parent}: {error}"
            ) from error

        data = {
            key: value for key, value in asdict(config).items() if value is not None
        }
        encoded = tomli_w.dumps(data).encode("utf-8")
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                os.fchmod(temporary_file.fileno(), 0o600)

            os.replace(temporary_path, self.path)
            self.path.chmod(0o600)
        except OSError as error:
            raise ConfigError(
                f"Could not write configuration file {self.path}: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
