#!/usr/bin/env python3
"""Generate `_data/glyphs.yaml` from the bundled Ubuntu web fonts.

The glyph specimen page renders the real character coverage of the fonts we
ship in `static/fonts`, so the data has to be derived from those files rather
than hand-maintained. Re-run this script whenever the font files are updated:

    pip install fonttools brotli
    python scripts/generate_glyph_data.py

The output is committed to the repository so that neither the build nor the
running app needs fontTools as a dependency.
"""

import unicodedata
from pathlib import Path

import yaml
from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = REPO_ROOT / "static" / "fonts"
OUTPUT = REPO_ROOT / "_data" / "glyphs.yaml"

# Subsets that make up each family. The roman Ubuntu face is split across
# several subsets; Ubuntu Mono ships as a single Latin file.
ROMAN_SUBSETS = [
    "Ubuntu-latin-v0.896a",
    "Ubuntu-latin-extended-v0.896a",
    "Ubuntu-greek-v0.896a",
    "Ubuntu-greek-extended-v0.896a",
    "Ubuntu-cyrillic-v0.896a",
    "Ubuntu-cyrillic-extended-v0.896a",
]
ITALIC_SUBSETS = ["Ubuntu-Italic-latin-v0.896a"]
MONO_SUBSETS = ["UbuntuMono-latin-v0.869"]

# Displayed in this order. Everything else falls through to "Symbols".
SCRIPT_ORDER = [
    ("latin", "Latin"),
    ("greek", "Greek"),
    ("cyrillic", "Cyrillic"),
    ("numerals", "Numerals"),
    ("punctuation", "Punctuation"),
    ("symbols", "Symbols"),
]


def load(subset):
    return TTFont(FONT_DIR / f"{subset}.woff2")


def codepoints(subsets):
    covered = set()
    for subset in subsets:
        covered |= set(load(subset).getBestCmap())
    return covered


def feature_tags(subsets):
    """Union of GSUB/GPOS feature tags across a family's subsets."""
    tags = set()
    for subset in subsets:
        font = load(subset)
        for table in ("GSUB", "GPOS"):
            if table in font:
                tags |= {
                    record.FeatureTag
                    for record in font[table].table.FeatureList.FeatureRecord
                }
    return tags


def _substitution_sources(subtable):
    """Glyph names a GSUB subtable substitutes away, unwrapping extensions."""
    subtable = getattr(subtable, "ExtSubTable", None) or subtable
    sources = set()

    mapping = getattr(subtable, "mapping", None)
    if mapping:
        sources |= set(mapping)

    alternates = getattr(subtable, "alternates", None)
    if alternates:
        sources |= set(alternates)

    return sources


def feature_codepoints(subsets, tags):
    """Codepoints a set of OpenType features actually substitutes.

    A feature being listed in GSUB says nothing about how much of the
    character set it reaches. `onum` only touches digits and a handful of
    currency symbols, so the specimen needs to know precisely which glyphs a
    toggle changes in order to show only those.
    """
    affected = set()

    for subset in subsets:
        font = load(subset)
        if "GSUB" not in font:
            continue

        table = font["GSUB"].table
        by_name = {}
        for codepoint, name in font.getBestCmap().items():
            by_name.setdefault(name, codepoint)

        for record in table.FeatureList.FeatureRecord:
            if record.FeatureTag not in tags:
                continue
            for index in record.Feature.LookupListIndex:
                for subtable in table.LookupList.Lookup[index].SubTable:
                    for name in _substitution_sources(subtable):
                        if name in by_name:
                            affected.add(by_name[name])

    return affected


def metrics(subset):
    """Vertical metrics normalised to a 1000-unit em so the front end can
    scale them to any rendered font size."""
    font = load(subset)
    upm = font["head"].unitsPerEm
    os2 = font["OS/2"]
    hhea = font["hhea"]

    cap_height = getattr(os2, "sCapHeight", None) or 0
    x_height = getattr(os2, "sxHeight", None) or 0

    def scale(value):
        return round(value * 1000 / upm)

    return {
        "ascender": scale(hhea.ascender),
        "capHeight": scale(cap_height),
        "xHeight": scale(x_height),
        "descender": scale(hhea.descender),
    }


def classify(char):
    """Bucket a character into one of the display groups."""
    category = unicodedata.category(char)

    if category.startswith("L"):
        try:
            name = unicodedata.name(char)
        except ValueError:
            return "symbols"
        for script in ("LATIN", "GREEK", "CYRILLIC"):
            if name.startswith(script):
                return script.lower()
        return "symbols"
    if category.startswith("N"):
        return "numerals"
    if category.startswith("P"):
        return "punctuation"
    return "symbols"


def describe(char):
    try:
        return unicodedata.name(char).title()
    except ValueError:
        return "Unnamed character"


def main():
    roman = codepoints(ROMAN_SUBSETS)
    italic = codepoints(ITALIC_SUBSETS)
    mono = codepoints(MONO_SUBSETS)

    roman_features = feature_tags(ROMAN_SUBSETS)
    italic_features = feature_tags(ITALIC_SUBSETS)
    mono_features = feature_tags(MONO_SUBSETS)

    # Which characters each toggle actually rewrites. Unioned across families:
    # every codepoint one family substitutes but another does not is absent
    # from the latter's cmap anyway, so the coverage flags already hide it.
    all_subsets = ROMAN_SUBSETS + ITALIC_SUBSETS + MONO_SUBSETS
    small_caps_glyphs = feature_codepoints(all_subsets, {"smcp", "c2sc"})
    text_figure_glyphs = feature_codepoints(all_subsets, {"onum"})

    buckets = {script_id: [] for script_id, _ in SCRIPT_ORDER}

    for codepoint in sorted(roman | mono):
        char = chr(codepoint)
        # Whitespace and control characters have no visible outline.
        if unicodedata.category(char) in ("Cc", "Cf", "Cn", "Zs", "Zl", "Zp"):
            continue

        buckets[classify(char)].append(
            {
                "char": char,
                "code": f"U+{codepoint:04X}",
                "name": describe(char),
                # Which faces can actually render this character.
                "roman": codepoint in roman,
                "italic": codepoint in italic,
                "mono": codepoint in mono,
                # Which toggles rewrite it.
                "smallCaps": codepoint in small_caps_glyphs,
                "textFigures": codepoint in text_figure_glyphs,
            }
        )

    data = {
        "families": [
            {
                "id": "ubuntu",
                "name": "Ubuntu",
                "stack": '"Ubuntu variable", sans-serif',
                "features": {
                    "smallCaps": "smcp" in roman_features,
                    "textFigures": "onum" in roman_features,
                    "italic": True,
                },
                "metrics": metrics("Ubuntu-latin-v0.896a"),
            },
            {
                "id": "ubuntu-mono",
                "name": "Ubuntu Mono",
                "stack": '"Ubuntu Mono variable", monospace',
                "features": {
                    "smallCaps": "smcp" in mono_features,
                    "textFigures": "onum" in mono_features,
                    # No italic Ubuntu Mono file is bundled.
                    "italic": False,
                },
                "metrics": metrics("UbuntuMono-latin-v0.869"),
            },
        ],
        "italicFeatures": {
            "smallCaps": "smcp" in italic_features,
            "textFigures": "onum" in italic_features,
        },
        "scripts": [
            {
                "id": script_id,
                "name": name,
                "glyphs": buckets[script_id],
            }
            for script_id, name in SCRIPT_ORDER
            if buckets[script_id]
        ],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as stream:
        stream.write("# Generated by scripts/generate_glyph_data.py - do not edit.\n")
        yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False, width=100)

    total = sum(len(group["glyphs"]) for group in data["scripts"])
    print(f"Wrote {total} glyphs to {OUTPUT.relative_to(REPO_ROOT)}")
    for group in data["scripts"]:
        print(f"  {group['name']:<12} {len(group['glyphs']):>5}")


if __name__ == "__main__":
    main()
