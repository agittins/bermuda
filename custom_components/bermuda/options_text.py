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
        "calibration_results_intro": (
            "Recent distances, calculated using `ref_power = {ref_power}`"
            " and `attenuation = {attenuation}` (values from new...old):"
        ),
        "calibration_row_estimate": "Estimate (m)",
        "calibration_row_rssi": "RSSI Actual",
        "calibration_submit_hint": "After you click Submit, the new distances will be shown here.",
        "filter_active": "🔍 **Filtering by:** '{filter_text}'",
        "found_devices": (
            "**Found {ibeacon_count} iBeacon(s), {standard_count} standard"
            " device(s), {random_count} random MAC device(s)**"
        ),
        "pagination_warning": (
            "⚠️ *Too many devices! Showing first {max_count} per category."
            " Use the filters below to narrow down the list.*"
        ),
        "scanner_table_col_address": "Address",
        "scanner_table_col_last_ad": "Last advertisement",
        "scanner_table_col_scanner": "Scanner",
        "scanner_table_title": "Status of scanners:",
        "seconds_ago": "seconds ago.",
    },
    "fr": {
        "calibration_results_intro": (
            "Distances récentes, calculées avec `ref_power = {ref_power}`"
            " et `attenuation = {attenuation}` (valeurs du plus récent au plus ancien) :"
        ),
        "calibration_row_estimate": "Estimation (m)",
        "calibration_row_rssi": "RSSI réel",
        "calibration_submit_hint": "Après avoir cliqué sur Soumettre, les nouvelles distances seront affichées ici.",
        "filter_active": "🔍 **Filtrage par :** '{filter_text}'",
        "found_devices": (
            "**{ibeacon_count} iBeacon, {standard_count} appareil(s) standard,"
            " {random_count} appareil(s) MAC aléatoire trouvé(s)**"
        ),
        "pagination_warning": (
            "⚠️ *Trop d'appareils ! Affichage des {max_count} premiers par catégorie."
            " Utilisez les filtres ci-dessous pour affiner la liste.*"
        ),
        "scanner_table_col_address": "Adresse",
        "scanner_table_col_last_ad": "Dernière annonce",
        "scanner_table_col_scanner": "Scanner",
        "scanner_table_title": "État des scanners :",
        "seconds_ago": "secondes.",
    },
}
