"""The Arabic catalog is complete: an Arabic-first product ships no English
fallbacks. `just messages` re-extracts; every new msgid must be translated
before the suite goes green again."""

import re
from pathlib import Path

from django.conf import settings

CATALOG = Path(settings.BASE_DIR) / "locale" / "ar" / "LC_MESSAGES" / "django.po"


def entries(po_text: str) -> list[tuple[str, str]]:
    """(msgid, msgstr) pairs, header excluded - a ~20-line parser instead of
    a polib dependency."""
    parsed: list[tuple[str, str]] = []
    for block in po_text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith("#")]
        texts: dict[str, list[str]] = {}
        current = ""
        for line in lines:
            keyword, _, rest = line.partition(" ")
            if keyword in ("msgid", "msgstr"):
                current = keyword
                texts[current] = [rest]
            elif line.startswith('"'):
                texts[current].append(line)
        if "msgid" in texts:
            msgid, msgstr = (
                "".join(re.sub(r'^"(.*)"$', r"\1", part) for part in texts[key])
                for key in ("msgid", "msgstr")
            )
            if msgid:
                parsed.append((msgid, msgstr))
    return parsed


def test_every_arabic_message_is_translated() -> None:
    catalog = CATALOG.read_text(encoding="utf-8")
    assert '"Language: ar\\n"' in catalog
    assert '"Plural-Forms: nplurals=6;' in catalog
    assert "#, fuzzy" not in catalog
    parsed = entries(catalog)
    assert parsed, "empty catalog"
    untranslated = [msgid for msgid, msgstr in parsed if not msgstr]
    assert not untranslated, f"untranslated Arabic messages: {untranslated}"
