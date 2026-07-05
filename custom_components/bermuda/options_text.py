"""
Inline en/fr UI text for the options/calibration flow.

These strings are NOT part of Home Assistant's translation schema (hassfest
rejects custom sections), so they live here and are looked up by the options
flow's _get_options_translation helper.
"""

from __future__ import annotations

# Dynamic UI text that is NOT part of HA's translation schema (hassfest rejects
# custom sections like "description_text").  These are used to build markdown
# tables and dynamic descriptions in the options flow.
_DESCRIPTION_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "scanner_table_col_address": "Address",
        "scanner_table_col_last_ad": "Last advertisement",
        "scanner_table_col_scanner": "Scanner",
        "scanner_table_title": "Status of scanners:",
        "seconds_ago": "seconds ago.",
    },
    "fr": {
        "scanner_table_col_address": "Adresse",
        "scanner_table_col_last_ad": "Dernière annonce",
        "scanner_table_col_scanner": "Scanner",
        "scanner_table_title": "État des scanners :",
        "seconds_ago": "secondes.",
    },
}
