"""The Arabic catalog is complete: an Arabic-first product ships no English
fallbacks. `just messages` re-extracts; every new msgid must be translated
before the suite goes green again."""

import re
from pathlib import Path

from django.conf import settings

CATALOG = Path(settings.BASE_DIR) / "locale" / "ar" / "LC_MESSAGES" / "django.po"


def entries(po_text: str) -> list[tuple[str, list[str]]]:
    """(msgid, [msgstr...]) pairs, header excluded - a ~20-line parser
    instead of a polib dependency. A plural entry carries one msgstr per
    Arabic plural form (``msgstr[0]`` .. ``msgstr[5]``); all must be filled."""
    parsed: list[tuple[str, list[str]]] = []
    for block in po_text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith("#")]
        texts: dict[str, list[str]] = {}
        current = ""
        for line in lines:
            keyword, _, rest = line.partition(" ")
            if keyword == "msgid" or keyword.startswith("msgstr"):
                current = keyword
                texts[current] = [rest]
            elif keyword == "msgid_plural":
                current = keyword  # not a translation; skipped below
                texts[current] = [rest]
            elif line.startswith('"'):
                texts[current].append(line)
        if "msgid" in texts:
            unquote = lambda parts: "".join(  # noqa: E731
                re.sub(r'^"(.*)"$', r"\1", part) for part in parts
            )
            msgid = unquote(texts["msgid"])
            translations = [
                unquote(parts)
                for key, parts in texts.items()
                if key.startswith("msgstr")
            ]
            if msgid:
                parsed.append((msgid, translations))
    return parsed


def test_every_arabic_message_is_translated() -> None:
    catalog = CATALOG.read_text(encoding="utf-8")
    assert '"Language: ar\\n"' in catalog
    assert '"Plural-Forms: nplurals=6;' in catalog
    assert "#, fuzzy" not in catalog
    parsed = entries(catalog)
    assert parsed, "empty catalog"
    untranslated = [msgid for msgid, msgstrs in parsed if not all(msgstrs)]
    assert not untranslated, f"untranslated Arabic messages: {untranslated}"


def test_parser_reads_plural_entries() -> None:
    block = (
        'msgid "%(count)d zone"\n'
        'msgid_plural "%(count)d zones"\n'
        'msgstr[0] "لا مناطق"\n'
        'msgstr[1] "منطقة"\n'
        'msgstr[2] ""\n'
    )
    [(msgid, msgstrs)] = entries(block)
    assert msgid == "%(count)d zone"
    assert msgstrs == ["لا مناطق", "منطقة", ""]
    assert not all(msgstrs)  # one empty form = untranslated
