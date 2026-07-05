"""i18n localization loader."""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalizationManager:
    """Manage localizations for multiple languages."""

    def __init__(self, locales_dir: str | None = None):
        # Default to this package's directory so it resolves regardless of cwd.
        self.locales_dir = (
            Path(locales_dir) if locales_dir else Path(__file__).resolve().parent
        )
        self.translations: dict[str, dict[str, str]] = {}
        self.default_language = "ru"

    def load_all(self) -> None:
        """Load all language files."""
        for lang_file in self.locales_dir.glob("*.json"):
            lang = lang_file.stem
            self._load_language(lang)

    def _load_language(self, lang: str) -> None:
        """Load single language file."""
        try:
            lang_file = self.locales_dir / f"{lang}.json"
            with open(lang_file, encoding="utf-8") as f:
                self.translations[lang] = json.load(f)
            logger.info(f"Loaded language: {lang}")
        except FileNotFoundError:
            logger.warning(f"Language file not found: {lang}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {lang}.json: {e}")

    def get(self, key: str, lang: str | None = None, **kwargs) -> str:
        """Get translation for key."""
        if lang is None:
            lang = self.default_language

        if not self._is_valid_language(lang):
            lang = self.default_language

        if lang not in self.translations:
            lang = self.default_language

        # Key missing in the chosen language → fall back to default language,
        # then to the key itself (so nothing crashes on an untranslated key).
        text = self.translations.get(lang, {}).get(key)
        if text is None:
            text = self.translations.get(self.default_language, {}).get(key, key)

        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(f"Cannot format translation {key}: {e}")

        return text

    @staticmethod
    def _is_valid_language(lang: str) -> bool:
        """Validate language code against whitelist pattern."""
        if not lang or not isinstance(lang, str):
            return False
        return bool(re.match(r"^[a-z]{2}$", lang))


# Global instance
_i18n: LocalizationManager | None = None


def get_i18n() -> LocalizationManager:
    """Get global i18n instance."""
    global _i18n
    if _i18n is None:
        _i18n = LocalizationManager()
        _i18n.load_all()
    return _i18n
