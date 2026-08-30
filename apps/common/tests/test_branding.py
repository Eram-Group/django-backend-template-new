"""config/branding.py is hand-edited per project - keep it honest."""

import re
from typing import cast

from django.conf import settings

from config.branding import BRAND_COLOR
from config.branding import PALETTES
from config.branding import SHADES
from config.branding import SITE_SYMBOL


def test_brand_color_is_a_known_palette() -> None:
    assert BRAND_COLOR in PALETTES


def test_every_palette_is_a_full_tailwind_ramp() -> None:
    for name, ramp in PALETTES.items():
        assert tuple(ramp) == SHADES, name
        for shade, value in ramp.items():
            assert re.fullmatch(r"[0-9a-f]{6}", value), (name, shade, value)


def test_site_symbol_is_a_material_symbol_name() -> None:
    assert re.fullmatch(r"[a-z0-9_]+", SITE_SYMBOL)


def test_admin_and_emails_share_the_ramp() -> None:
    hex_600 = PALETTES[BRAND_COLOR]["600"]
    r, g, b = (int(hex_600[i : i + 2], 16) for i in (0, 2, 4))
    colors = cast("dict[str, dict[str, str]]", settings.UNFOLD["COLORS"])
    assert colors["primary"]["600"] == f"{r} {g} {b}"
    assert settings.EMAIL_BRAND["gradient_start"] == f"#{hex_600}"
    assert settings.UNFOLD["SITE_SYMBOL"] == SITE_SYMBOL
