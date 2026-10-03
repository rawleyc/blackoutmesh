"""
WP 1.1 / 1.2 — Canonical Templated Codebook & Multilingual Message Renderer

Enforces the design principles:
1. Relay without decoding: The mesh forwards verified packets even if local codebook lacks the template ID.
2. Localization for free: Same compact template ID renders in PL, EN, UA.
3. Unknown code fallback: Fallback text ensures user is alerted even on outdated codebook.
4. Presets & coordinates: Resolves shelter IDs or formats raw coordinates.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class RenderedAlert:
    """Human-readable alert rendered from a compact templated packet."""
    category: str
    severity: str
    text_pl: str
    text_en: str
    text_ua: str
    location_name_pl: str
    location_name_en: str
    location_name_ua: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_fallback: bool = False


class Codebook:
    """Manages the emergency message template registry and shelter directory."""

    def __init__(self, data: dict):
        self.codebook_version: int = data.get("codebook_version", 1)
        self.issued_at: int = data.get("issued_at", 0)
        self.authority_id: str = data.get("authority_id", "")
        self.authority_name: str = data.get("authority_name", "")
        self.templates: dict[str, dict] = data.get("templates", {})
        self.shelters: dict[str, dict] = data.get("shelters", {})

    @classmethod
    def load_default(cls) -> "Codebook":
        """Load default bundled codebook from package directory."""
        path = Path(__file__).resolve().parent / "codebook.json"
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    def resolve_location(
        self, loc_type: int, loc_ref: Any
    ) -> tuple[str, str, str, Optional[float], Optional[float]]:
        """Resolve location descriptor into trilingual strings and coordinates.

        Args:
            loc_type: 0 = None, 1 = Shelter ID string, 2 = [lat, lon] tuple
            loc_ref: Shelter ID string or [lat, lon] list
        """
        if loc_type == 1 and isinstance(loc_ref, str):
            shelter = self.shelters.get(loc_ref)
            if shelter:
                return (
                    shelter["name_pl"],
                    shelter["name_en"],
                    shelter["name_ua"],
                    shelter.get("lat"),
                    shelter.get("lon"),
                )
            # Unknown shelter ID fallback
            desc = f"Punkt: {loc_ref}"
            return desc, f"Point: {loc_ref}", f"Пункт: {loc_ref}", None, None

        elif loc_type == 2 and isinstance(loc_ref, (list, tuple)) and len(loc_ref) >= 2:
            lat, lon = float(loc_ref[0]), float(loc_ref[1])
            coord_str = f"{lat:.4f}°N, {lon:.4f}°E"
            return coord_str, coord_str, coord_str, lat, lon

        return "obszar zagrożenia", "hazard zone", "зона небезпеки", None, None

    def render(
        self,
        template_id: int,
        loc_type: int = 0,
        loc_ref: Any = "",
        custom_text: Optional[str] = None,
    ) -> RenderedAlert:
        """Render a templated alert into all supported languages."""
        tid_str = str(template_id)
        loc_pl, loc_en, loc_ua, lat, lon = self.resolve_location(loc_type, loc_ref)

        if tid_str in self.templates:
            tpl = self.templates[tid_str]
            category = tpl.get("category", "EMERGENCY")
            severity = tpl.get("severity", "CRITICAL")

            # Interpolate parameters
            text_pl = tpl.get("pl", "").format(
                location=loc_pl, custom_text=custom_text or ""
            )
            text_en = tpl.get("en", "").format(
                location=loc_en, custom_text=custom_text or ""
            )
            text_ua = tpl.get("ua", "").format(
                location=loc_ua, custom_text=custom_text or ""
            )

            return RenderedAlert(
                category=category,
                severity=severity,
                text_pl=text_pl,
                text_en=text_en,
                text_ua=text_ua,
                location_name_pl=loc_pl,
                location_name_en=loc_en,
                location_name_ua=loc_ua,
                lat=lat,
                lon=lon,
                is_fallback=False,
            )

        # UNKNOWN TEMPLATE ID FALLBACK (Relay without decoding / Future codebook)
        fallback_pl = (
            f"[OFICJALNY KOMUNIKAT KRYZYSOWY (Kod {template_id})] "
            f"Lokalizacja: {loc_pl}. Zaktualizuj aplikację lub sprawdź oficjalne kanały."
        )
        fallback_en = (
            f"[OFFICIAL EMERGENCY ALERT (Code {template_id})] "
            f"Location: {loc_en}. Please check official broadcast channels."
        )
        fallback_ua = (
            f"[ОФІЦІЙНЕ ПОВІДОМЛЕННЯ (Код {template_id})] "
            f"Локація: {loc_ua}. Перевірте офіційні джерела або оновіть додаток."
        )

        return RenderedAlert(
            category="UNKNOWN_TEMPLATE",
            severity="CRITICAL",
            text_pl=fallback_pl,
            text_en=fallback_en,
            text_ua=fallback_ua,
            location_name_pl=loc_pl,
            location_name_en=loc_en,
            location_name_ua=loc_ua,
            lat=lat,
            lon=lon,
            is_fallback=True,
        )
