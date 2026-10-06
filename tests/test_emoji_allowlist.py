"""Эмодзи-иконки бота: только разрешённые наборы (TgAndroidIcons / tgiosicons / sfsymbols).

Список ID собран из трёх наборов (см. tests/data/emoji_allowed_ids.txt). Любая иконка
или премиум-эмодзи, которых там нет, — регресс: владелец разрешил только эти наборы.
"""

from __future__ import annotations

import pathlib
import re

from bot.emoji import IDS

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALLOWED = set(
    (ROOT / "tests" / "data" / "emoji_allowed_ids.txt")
    .read_text(encoding="utf-8")
    .split()
)

# ID из чужих наборов (remnawave / NewsEmoji / FinanceEmoji), которые были в коде
FOREIGN_PACK_IDS = {
    "5451682961831257285",
    "5424818078833715060",
    "5231012545799666522",
    "5267500801240092311",
    "5382194935057372936",
    "5197269100878907942",
    "5452013034362925287",
}


def test_ids_values_come_from_allowed_packs() -> None:
    bad = {glyph: iid for glyph, iid in IDS.items() if iid not in ALLOWED}
    assert not bad, f"премиум-эмодзи вне разрешённых наборов: {bad}"


def test_button_icons_come_from_allowed_packs() -> None:
    bad: list[str] = []
    for path in (ROOT / "bot").rglob("*.py"):
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for iid in re.findall(r'icon_custom_emoji_id\s*=\s*"(\d+)"', line):
                if iid not in ALLOWED:
                    bad.append(f"{path.relative_to(ROOT)}:{num} {iid}")
    assert not bad, "иконки кнопок вне разрешённых наборов: " + ", ".join(bad)


def test_no_foreign_pack_ids_left() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "bot").rglob("*")
        if path.is_file()
    )
    left = sorted(i for i in FOREIGN_PACK_IDS if i in text)
    assert not left, f"остались иконки из чужих наборов: {left}"
