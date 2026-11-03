"""Palette, styles et petits composants visuels partagés."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# --------------------------------------------------------------------------
# Palette : bleu-gris profond, une seule couleur d'accent, aucune texture
# --------------------------------------------------------------------------
BG = "#0f1115"            # fond de la fenêtre
SURFACE = "#171a21"       # cartes et zones de contenu
SURFACE_HI = "#1f242d"    # éléments survolés ou sélectionnés
BORDER = "#2a303b"        # séparateurs fins
TEXT = "#e8ebf0"          # texte principal
TEXT_SOFT = "#b6bec9"     # texte secondaire
MUTED = "#7c8593"         # légendes discrètes
ACCENT = "#6c8cff"        # bleu unique de l'interface
ACCENT_HOVER = "#8099ff"
ACCENT_TEXT = "#ffffff"
OK = "#4ec18a"
WARN = "#d9a441"
ERROR = "#e5645f"

# Les quatre pistes conservent les couleurs des boutons du jouet, en sourdine.
TRACK_COLORS = ("#3f9e70", "#c96a6a", "#5b7fd4", "#c39a4b")
TRACK_COLORS_SOFT = ("#1b2a24", "#2a2020", "#1e2433", "#292318")

# Compatibilité avec le reste du code.
PANEL = SURFACE
PANEL_HI = SURFACE_HI

FONT_FAMILY = "Segoe UI"


def pick_font(root: tk.Misc, candidates: tuple[str, ...] = ("Segoe UI", "Inter", "DejaVu Sans")) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return "TkDefaultFont"


class Fonts:
    """Jeu de polices cohérent, ajusté une fois la fenêtre créée."""

    def __init__(self, root: tk.Misc) -> None:
        family = pick_font(root)
        self.family = family
        self.title = (family, 20, "bold")
        self.subtitle = (family, 10)
        self.body = (family, 10)
        self.small = (family, 9)
        self.tiny = (family, 8)
        self.button = (family, 10, "bold")
        self.mono = ("Consolas" if "Consolas" in tkfont.families(root) else "DejaVu Sans Mono", 10)
        self.big_time = (family, 15, "bold")


class RoundedButton(tk.Canvas):
    """Bouton plat dessiné à la main : coins arrondis, survol, état pressé."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command=None,
        *,
        width: int = 120,
        height: int = 36,
        radius: int = 9,
        fill: str = SURFACE_HI,
        hover: str | None = None,
        foreground: str = TEXT,
        outline: str = BORDER,
        font: tuple | None = None,
        accent: str | None = None,
    ) -> None:
        super().__init__(master, width=width, height=height, bg=bg_of(master), highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._width = width
        self._height = height
        self._radius = radius
        self._fill = fill
        self._hover = hover or _lighten(fill)
        self._fg = foreground
        self._outline = outline
        self._accent = accent
        self._font = font
        self._enabled = True
        self._pressed = False
        self._hovered = False
        self._glow = False
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")

    # -- dessin ------------------------------------------------------------
    def _rounded(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _draw(self) -> None:
        self.delete("all")
        fill = self._fill
        if not self._enabled:
            fill = _blend(self._fill, BG, 0.55)
        elif self._pressed:
            fill = _darken(self._hover)
        elif self._hovered:
            fill = self._hover
        r = self._radius
        self._rounded(1, 1, self._width - 1, self._height - 1, r, fill=fill, outline="")
        outline = self._accent if self._glow else self._outline
        width = 2 if self._glow else 1
        self._rounded(1, 1, self._width - 1, self._height - 1, r, fill="", outline=outline, width=width)
        fg = self._fg if self._enabled else MUTED
        self.create_text(
            self._width / 2,
            self._height / 2,
            text=self._text,
            fill=fg,
            font=self._font,
        )

    # -- états -------------------------------------------------------------
    @property
    def bg(self) -> str:
        return bg_of(self.master)

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._draw()

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "")
        self._draw()

    def set_glow(self, glow: bool) -> None:
        if glow == self._glow:
            return
        self._glow = glow
        self._draw()

    def flash(self, color: str = ACCENT, duration: int = 160) -> None:
        self.itemconfig(1, outline=color, width=2)

    # -- événements --------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._draw()

    def _on_release(self, _event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self._enabled and self._command is not None:
            self._command()


def bg_of(widget: tk.Misc) -> str:
    try:
        return widget.cget("bg")
    except tk.TclError:
        return PANEL


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _lighten(value: str, amount: float = 0.12) -> str:
    r, g, b = _hex_to_rgb(value)
    return _rgb_to_hex((r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount))


def _darken(value: str, amount: float = 0.15) -> str:
    r, g, b = _hex_to_rgb(value)
    return _rgb_to_hex((r * (1 - amount), g * (1 - amount), b * (1 - amount)))


def _blend(a: str, b: str, ratio: float) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return _rgb_to_hex((ra * (1 - ratio) + rb * ratio, ga * (1 - ratio) + gb * ratio, ba * (1 - ratio) + bb * ratio))


def format_time(ms: float) -> str:
    """Formate une durée en mm:ss (ou h:mm:ss au-delà d'une heure)."""
    total = int(max(0, ms) // 1000)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
