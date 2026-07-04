"""i18n localization loader."""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class LocalizationManager:
    """Manage localizations for multiple languages."""

    def __init__(self, locales_dir: str = "bot/i18n"):
        self.locales_dir = Path(locales_dir)
        self.translations: Dict[str, Dict[str, str]] = {}
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
            with open(lang_file, "r", encoding="utf-8") as f:
                self.translations[lang] = json.load(f)
            logger.info(f"Loaded language: {lang}")
        except FileNotFoundError:
            logger.warning(f"Language file not found: {lang}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {lang}.json: {e}")

    def get(
        self, key: str, lang: Optional[str] = None, **kwargs
    ) -> str:
        """Get translation for key."""
        if lang is None:
            lang = self.default_language

        if lang not in self.translations:
            lang = self.default_language

        text = self.translations.get(lang, {}).get(key, key)

        if kwargs:
            try:
                text = text.format(**kwargs)
            except KeyError as e:
                logger.warning(f"Missing placeholder in {key}: {e}")

        return text


# Global instance
_i18n: Optional[LocalizationManager] = None


def get_i18n() -> LocalizationManager:
    """Get global i18n instance."""
    global _i18n
    if _i18n is None:
        _i18n = LocalizationManager()
        _i18n.load_all()
    return _i18n
