"""Interface graphique de l'émulateur robot 2-XL."""

from __future__ import annotations

import contextlib
import os
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

from . import __app_author__, __app_name__, __version__
from .engine import AudioEngine, Cassette, CassetteError
from .library import (
    TRACK_KEYS,
    TRACK_LABELS,
    asset,
    cassette_from_paths,
    detect_track_paths,
    load_cassette,
    load_config,
    save_config,
    scan_library,
)
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_TEXT,
    BG,
    BORDER,
    ERROR,
    MUTED,
    OK,
    SURFACE,
    SURFACE_HI,
    TEXT,
    TEXT_SOFT,
    TRACK_COLORS,
    TRACK_COLORS_SOFT,
    WARN,
    Fonts,
    RoundedButton,
    bg_of,
    format_time,
)

ROBOT_IMAGE = "robot.png"
ROBOT_WIDTH = 352
MAX_ROBOT_WIDTH = 480
ROBOT_ASPECT = 810 / 1065  # largeur / hauteur de la photo recadrée
LAYOUT_MAX_WIDTH = 760
# Hauteur réservée aux commandes (en-tête, pastilles, transport, position,
# pied de page, barre d'état) : la photo occupe tout le reste.
COMMAND_HEIGHT = 340

# Fractions mesurées sur la photo réellement embarquée (assets/robot.png,
# 810 x 1065) : centres des quatre étiquettes OUI / NON / VRAI / FAUX.
BUTTON_X_CENTER_FRAC = (0.3210, 0.4253, 0.5302, 0.6340)
BUTTON_Y1_FRAC = 0.8291
BUTTON_Y2_FRAC = 0.9493
BUTTON_HALF_WIDTH_FRAC = 0.0305

SEEK_STEP_MS = 5_000
SEEK_STEP_LARGE_MS = 30_000


# --------------------------------------------------------------------------
# Petits composants
# --------------------------------------------------------------------------


class _Slider(tk.Canvas):
    """Base commune aux curseurs (position, volume)."""

    def __init__(self, master: tk.Misc, *, height: int = 30, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            bg=bg_of(master),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._height = height
        self._ratio = 0.0
        self._dragging = False
        self._hover = False
        self._enabled = True
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.configure(cursor="hand2")

    # -- géométrie ---------------------------------------------------------
    @property
    def _pad(self) -> int:
        return 10

    def _span(self) -> tuple[float, float]:
        return self._pad, max(self._pad + 1, self.winfo_width() - self._pad)

    def _to_ratio(self, event) -> float:
        x1, x2 = self._span()
        return min(1.0, max(0.0, (event.x - x1) / (x2 - x1)))

    # -- état --------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "")
        self._draw()

    def _draw(self) -> None:
        raise NotImplementedError

    # -- événements --------------------------------------------------------
    def _enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hover = False
        if not self._dragging:
            self._draw()

    def _press(self, event) -> None:
        if not self._enabled:
            return
        self._dragging = True
        self._ratio = self._to_ratio(event)
        self._draw()
        self._on_drag(self._ratio)

    def _motion(self, event) -> None:
        if not self._dragging or not self._enabled:
            return
        self._ratio = self._to_ratio(event)
        self._draw()
        self._on_drag(self._ratio)

    def _release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._on_release(self._to_ratio(event))
        self._draw()

    def _on_drag(self, ratio: float) -> None:  # pragma: no cover - surchargé
        pass

    def _on_release(self, ratio: float) -> None:  # pragma: no cover - surchargé
        pass


class SeekBar(_Slider):
    """Barre de progression avec déplacement à la souris."""

    def __init__(self, master: tk.Misc, on_seek: Callable[[float], None], fonts: Fonts) -> None:
        super().__init__(master, height=26)
        self._on_seek = on_seek
        self._fonts = fonts
        self._position = 0.0
        self._duration = 0.0

    def set_state(self, position_ms: float, duration_ms: float) -> None:
        self._duration = max(0.0, duration_ms)
        if not self._dragging:
            self._position = max(0.0, min(position_ms, self._duration))
            self._ratio = self._position / self._duration if self._duration else 0.0
            self._draw()

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def preview_ratio(self) -> float:
        return self._ratio

    def _on_drag(self, ratio: float) -> None:
        # Retour visuel immédiat du temps visé.
        self._draw()

    def _on_release(self, ratio: float) -> None:
        if self._duration:
            self._on_seek(ratio * self._duration)

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            return
        x1, x2 = self._span()
        cy = self._height / 2
        self.create_line(x1, cy, x2, cy, fill=BORDER, width=6, capstyle="round")
        thumb_color = ACCENT if self._enabled else BORDER
        px = x1 + (x2 - x1) * self._ratio
        if self._ratio > 0:
            self.create_line(x1, cy, max(x1 + 3, px), cy, fill=ACCENT, width=6, capstyle="round")
        radius = 8 if (self._hover or self._dragging) else 6
        self.create_oval(px - radius, cy - radius, px + radius, cy + radius, fill=thumb_color, outline=BG, width=2)


class VolumeBar(_Slider):
    """Petit curseur de volume horizontal."""

    def __init__(self, master: tk.Misc, on_change: Callable[[float], None], initial: float = 1.0) -> None:
        super().__init__(master, height=22)
        self._ratio = max(0.0, min(1.0, initial))
        self._on_change = on_change
        self.bind("<Configure>", lambda _e: self._draw(), add="+")

    def value(self) -> float:
        return self._ratio

    def _on_drag(self, ratio: float) -> None:
        self._on_change(ratio)

    def _on_release(self, ratio: float) -> None:
        self._on_change(ratio)

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            return
        x1, x2 = self._span()
        cy = self._height / 2
        self.create_line(x1, cy, x2, cy, fill=BORDER, width=4, capstyle="round")
        px = x1 + (x2 - x1) * self._ratio
        if self._ratio > 0:
            self.create_line(x1, cy, max(x1 + 2, px), cy, fill=ACCENT, width=4, capstyle="round")
        self.create_oval(px - 5, cy - 5, px + 5, cy + 5, fill=TEXT, outline=BG, width=2)


class TrackChip(tk.Canvas):
    """Pastille cliquable : « 1 OUI », « 2 A · NON », « 3 B · VRAI », « 4 C · FAUX »."""

    def __init__(self, master: tk.Misc, index: int, label: str, fonts: Fonts, on_click: Callable[[int], None], on_hover) -> None:
        super().__init__(master, width=118, height=30, bg=bg_of(master), highlightthickness=0, bd=0)
        self.index = index
        self.label = label
        self._fonts = fonts
        self._on_click = on_click
        self._on_hover = on_hover
        self._active = False
        self._hover = False
        self._seen_hover = False
        self._pressed = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", lambda _e: self._press())
        self.bind("<ButtonRelease-1>", lambda _e: self._release())
        self.configure(cursor="hand2")
        self._draw()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self._draw()

    def set_hover_source(self, hovered: bool) -> None:
        """Survol déclenché depuis la photo du robot."""
        if hovered != self._seen_hover:
            self._seen_hover = hovered
            self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()
        self._on_hover(self.index, value)

    def _press(self) -> None:
        self._pressed = True
        self._draw()

    def _release(self) -> None:
        was = self._pressed
        self._pressed = False
        self._draw()
        if was:
            self._on_click(self.index)

    def _draw(self) -> None:
        self.delete("all")
        base = TRACK_COLORS[self.index]
        fill = base if self._active else TRACK_COLORS_SOFT[self.index]
        if (self._hover or self._seen_hover) and not self._active:
            fill = _mix(fill, base, 0.45)
        if self._pressed:
            fill = _mix(fill, "#000000", 0.15)
        outline = base if self._active else BORDER
        _rounded_rect(self, 1, 1, 117, 29, 8, fill=fill, outline=outline, width=2 if self._active else 1)
        text_color = TEXT if self._active else TEXT_SOFT
        letter_color = base if not self._active else TEXT
        prefix = f"{self.index + 1}"
        if TRACK_KEYS[self.index]:
            prefix += f"  {TRACK_KEYS[self.index]}"
        self.create_text(13, 15, text=prefix, anchor="w", fill=letter_color, font=self._fonts.tiny)
        self.create_text(52, 15, text=self.label, anchor="w", fill=text_color, font=self._fonts.small)


def _rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _mix(a: str, b: str, ratio: float) -> str:
    def parts(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    ra, ga, ba = parts(a)
    rb, gb, bb = parts(b)
    r = round(ra + (rb - ra) * ratio)
    g = round(ga + (gb - ga) * ratio)
    b = round(ba + (bb - ba) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# Fenêtre principale
# --------------------------------------------------------------------------


class TwoXLApp:
    def __init__(self, root: tk.Tk, *, autoload: bool = True) -> None:
        self.root = root
        self.fonts = Fonts(root)
        self.config = load_config()
        self.engine = AudioEngine()
        self.engine.set_volume(self.config.volume)
        self.cassette: Cassette | None = None
        self.image_ref: ImageTk.PhotoImage | None = None
        self._robot_target_width = ROBOT_WIDTH
        self._column_width = LAYOUT_MAX_WIDTH
        self._rescale_job: str | None = None
        self.hotspot_rects: list[tuple[float, float, float, float]] = []
        self._hovered_spot: int | None = None
        self.chips: list[TrackChip] = []
        self._tick_job: str | None = None
        self._ui_ready = False
        self._last_state = "stopped"
        self.selection_count = 0
        self._seek_dragging = False

        root.title(__app_name__)
        root.configure(bg=BG)
        root.minsize(430, 680)
        with contextlib.suppress(Exception):
            icon = asset("2xl.ico")
            if icon.is_file():
                root.iconbitmap(default=str(icon))

        self._build()
        self._fit_window()
        self._enforce_min_height()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._ui_ready = True
        self._refresh_transport()
        self._schedule_tick()

        if self.config.warning:
            self.set_status(self.config.warning, WARN)
        if autoload:
            self.root.after(120, self._autoload)

    def _on_window_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        column_width = max(360, min(event.width - 24, LAYOUT_MAX_WIDTH))
        if column_width != self._column_width:
            self._column_width = column_width
            self.column.configure(width=column_width)
        self.root.update_idletasks()
        width = self._robot_width_for(event.width, event.height)
        if abs(width - self._robot_target_width) >= 6:
            self._robot_target_width = width
            self.root.after_idle(self._rescale_robot)

    def _commands_height(self) -> int:
        """Hauteur réellement occupée par les commandes, photo exclue."""
        outer = getattr(self, "outer", None)
        if outer is None:
            return COMMAND_HEIGHT
        total = 0
        for child in outer.winfo_children():
            if child is getattr(self, "robot_area", None):
                continue  # la photo est traitée séparément
            total += child.winfo_reqheight()
            try:
                pad_top, pad_bottom = child.pack_info().get("pady", (0, 0)) or (0, 0)
                total += int(pad_top) + int(pad_bottom)
            except (tk.TclError, ValueError, TypeError):
                pass
        # Marges du conteneur (padx / pady déclarés au montage).
        return max(280, total + 30)

    def _robot_width_for(self, window_width: int, window_height: int) -> int:
        """Largeur de la photo, aussi grande que la place disponible le permet.

        La photo occupe l'espace laissé libre par les commandes : on ne rétrécit
        donc jamais les boutons pour l'agrandir, et elle ne déborde jamais.
        """
        width_limit = max(280, min(window_width - 56, LAYOUT_MAX_WIDTH - 40, MAX_ROBOT_WIDTH))
        # Hauteur restante une fois l'en-tête, les pastilles, le transport,
        # la barre de position, le pied de page et la barre d'état retirés.
        free = window_height - self._commands_height()
        height_limit = int(max(200, free - 12) * ROBOT_ASPECT)
        return max(280, min(width_limit, height_limit))

    def _rescale_robot(self) -> None:
        if not hasattr(self, "canvas") or self.canvas is None:
            return
        self._load_robot_image(self._robot_target_width)
        self._layout_hotspots()

    def _fit_window(self) -> None:
        """Ouvre la fenêtre à la taille qui montre l'interface entière.

        La photo est d'abord posée à sa largeur maximale ; la hauteur réellement
        occupée par les commandes est mesurée, puis la photo est réduite si
        nécessaire pour que rien ne soit rogné.
        """
        self.root.update_idletasks()
        forced = os.environ.get("TWOXL_WINDOW", "").strip()
        if forced and "x" in forced:
            self.root.geometry(forced)
            try:
                forced_w, forced_h = (int(part) for part in forced.lower().split("x"))
            except ValueError:
                return
            photo_w = self._robot_width_for(forced_w, forced_h)
            self._robot_target_width = photo_w
            self._load_robot_image(photo_w)
            self.root.update_idletasks()
            return

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(620, min(LAYOUT_MAX_WIDTH + 40, screen_w - 60))
        available = screen_h - 60

        # Photo à sa taille maximale, puis mesure de la place restante.
        photo_w = min(MAX_ROBOT_WIDTH, width - 56, LAYOUT_MAX_WIDTH - 40)
        self._robot_target_width = photo_w
        self._load_robot_image(photo_w)
        self.root.update_idletasks()
        commands = max(240, self.outer.winfo_reqheight() - self.image_ref.height() + 30)

        photo_h = photo_w / ROBOT_ASPECT
        if commands + photo_h > available:
            photo_h = max(220, available - commands)
            photo_w = max(260, int(photo_h * ROBOT_ASPECT))
            self._robot_target_width = photo_w
            self._load_robot_image(photo_w)
            self.root.update_idletasks()
            commands = max(240, self.outer.winfo_reqheight() - self.image_ref.height() + 30)

        height = int(min(available, commands + self.image_ref.height()))
        self.root.geometry(f"{width}x{height}")

    def _enforce_min_height(self) -> None:
        """Interdit de réduire la fenêtre au point de rogner les commandes."""
        self.root.update_idletasks()
        needed = getattr(self, "outer", self.root).winfo_reqheight() + 30
        limit = self.root.winfo_screenheight() - 60
        target = min(needed, max(640, limit))
        self.root.minsize(560, target)
        if self.root.winfo_height() < target:
            width = self.root.winfo_width() or 620
            self.root.geometry(f"{max(600, width)}x{target}")

    # ------------------------------------------------------------- montage
    def _build(self) -> None:
        # Colonne centrale de largeur bornée : l'interface reste centrée et
        # lisible même sur un écran large.
        self.page = tk.Frame(self.root, bg=BG)
        self.page.pack(fill="both", expand=True)
        # Largeur bornée : sur un grand écran, l'interface reste une colonne
        # centrée au lieu de s'étirer sur toute la largeur de la fenêtre.
        left_pad = tk.Frame(self.page, bg=BG)
        left_pad.pack(side="left", fill="both", expand=True)
        self.column = tk.Frame(self.page, bg=BG, width=LAYOUT_MAX_WIDTH)
        self.column.pack(side="left", fill="y")
        self.column.pack_propagate(False)
        fixed_pad = tk.Frame(self.page, bg=BG)
        fixed_pad.pack(side="left", fill="both", expand=True)
        outer = tk.Frame(self.column, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=(16, 14))
        # Référence gardée pour mesurer la hauteur réelle de l'interface :
        # la colonne a la propagation coupée, donc winfo_reqheight() de la
        # fenêtre ne refléterait pas son contenu.
        self.outer = outer
        # Les éléments du bas sont réservés en premier : ils ne peuvent pas
        # être rognés si la photo ou les contrôles prennent de la place.
        self._build_statusbar(outer)
        self._build_footer(outer)
        self._build_header(outer)
        self._build_robot(outer)
        self._build_tracks(outer)
        self._build_transport(outer)
        self._build_seek(outer)
        self.root.bind("<Configure>", self._on_window_configure, add="+")

    def _build_header(self, parent: tk.Misc) -> None:
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x")
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text=__app_name__, bg=BG, fg=TEXT, font=self.fonts.title).pack(anchor="w")
        tk.Label(
            titles,
            text=f"Cassettes françaises à 4 pistes · par {__app_author__}",
            bg=BG,
            fg=MUTED,
            font=self.fonts.subtitle,
        ).pack(anchor="w")

        right = tk.Frame(header, bg=BG)
        right.pack(side="right", anchor="ne")
        self.led = tk.Canvas(right, width=14, height=14, bg=BG, highlightthickness=0)
        self.led.pack(side="left", padx=(0, 4))
        self._led_dot = self.led.create_oval(2, 2, 12, 12, fill=BORDER, outline="")
        RoundedButton(
            right,
            "Cassette",
            command=self.open_library,
            width=92,
            height=30,
            font=self.fonts.small,
            fill=SURFACE_HI,
            accent=ACCENT,
        ).pack(side="left", padx=(8, 0))

    def _build_robot(self, parent: tk.Misc) -> None:
        self.robot_area = tk.Frame(parent, bg=BG)
        self.robot_area.pack(fill="both", expand=True, pady=(12, 8))
        self.robot_area.pack_propagate(True)
        self.canvas = tk.Canvas(self.robot_area, bg=BG, highlightthickness=0, bd=0, height=420, width=ROBOT_WIDTH)
        self.canvas.pack(expand=True)
        self._load_robot_image()
        self.canvas.bind("<Configure>", lambda _e: self._layout_hotspots())
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

    def _load_robot_image(self, width: int | None = None) -> None:
        path = asset(ROBOT_IMAGE)
        width = int(width or self._robot_target_width or ROBOT_WIDTH)
        if path.is_file():
            try:
                image = Image.open(path).convert("RGB")
                ratio = width / image.width
                image = image.resize((width, int(image.height * ratio)), Image.LANCZOS)
                previous = self.image_ref
                self.image_ref = ImageTk.PhotoImage(image)
                # L'ancienne photo est retirée : sinon les images s'empilent et
                # la boîte de référence garde les dimensions précédentes, ce qui
                # décalait complètement les zones cliquables.
                self.canvas.delete("photo")
                self.canvas.delete("hotspot")
                self.canvas.config(height=image.height, width=image.width)
                self.canvas.create_image(0, 0, anchor="nw", image=self.image_ref, tags="photo")
                del previous
                return
            except Exception as exc:  # image corrompue : on continue sans photo
                print(f"Image du robot illisible : {exc}", file=sys.stderr)
        self.canvas.config(height=380, width=ROBOT_WIDTH)
        self.canvas.create_text(
            width / 2,
            180,
            text="2-XL",
            fill=ACCENT,
            font=(self.fonts.family, 42, "bold"),
        )
        self.canvas.create_text(
            width / 2,
            230,
            text="(photo du robot absente : assets/robot.png)",
            fill=MUTED,
            font=self.fonts.small,
        )

    def _layout_hotspots(self) -> None:
        """Place les repères des quatre boutons de la photo.

        Aucun événement n'est attaché aux repères eux-mêmes : le survol et le
        clic sont calculés une seule fois sur le canevas. Cela évite la boucle
        d'événements qui faisait s'agiter le pointeur et perdait les clics.
        """
        self.canvas.delete("hotspot")
        self.hotspot_rects.clear()
        if self.image_ref is None:
            return
        width = self.image_ref.width()
        height = self.image_ref.height()
        if width <= 1 or height <= 1:
            return
        # La photo est ancrée en (0, 0) : les fractions se convertissent donc
        # directement en coordonnées du canevas, sans décalage.
        y1 = BUTTON_Y1_FRAC * height
        y2 = BUTTON_Y2_FRAC * height
        half = BUTTON_HALF_WIDTH_FRAC * width
        # Marge de confort pour que le clic reste tolérant sur une petite image.
        half = max(half, 12)
        for index, frac in enumerate(BUTTON_X_CENTER_FRAC):
            cx = frac * width
            rect = (cx - half, y1, cx + half, y2)
            self.hotspot_rects.append(rect)
            color = TRACK_COLORS[index]
            self.canvas.create_rectangle(
                *rect, outline=_mix(color, BG, 0.55), width=1, dash=(3, 4), fill="",
                tags=("hotspot", f"spot{index}"),
            )
            self.canvas.create_rectangle(
                rect[0] - 3, rect[1] - 3, rect[2] + 3, rect[3] + 3,
                outline=color, width=3, fill="",
                state="hidden", tags=("hotspot", f"halo{index}"),
            )
        self.canvas.tag_lower("hotspot")
        self._sync_spot_highlight()

    def _spot_at(self, x: float, y: float) -> int | None:
        for index, (x1, y1, x2, y2) in enumerate(self.hotspot_rects):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return index
        return None

    def _on_chip_hover(self, index: int, hovered: bool) -> None:
        if hovered or index == self.engine.active_track:
            self.canvas.itemconfig(f"halo{index}", state="normal")
        else:
            self.canvas.itemconfig(f"halo{index}", state="hidden")

    def _on_canvas_motion(self, event) -> None:
        index = self._spot_at(event.x, event.y)
        cursor = "hand2" if index is not None else ""
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.config(cursor=cursor)
        if index != self._hovered_spot:
            self._set_spot_hover(self._hovered_spot, False)
            self._set_spot_hover(index, True)
            self._hovered_spot = index

    def _on_canvas_leave(self, _event=None) -> None:
        if self._hovered_spot is not None:
            self._set_spot_hover(self._hovered_spot, False)
            self._hovered_spot = None
        self.canvas.config(cursor="")

    def _on_canvas_click(self, event) -> None:
        index = self._spot_at(event.x, event.y)
        if index is not None:
            self.select_track(index)

    def _set_spot_hover(self, index: int | None, hovered: bool) -> None:
        if index is None:
            return
        if hovered or index == self.engine.active_track:
            self.canvas.itemconfig(f"halo{index}", state="normal")
        else:
            self.canvas.itemconfig(f"halo{index}", state="hidden")
        chip = self.chips[index] if index < len(self.chips) else None
        if chip is not None:
            chip.set_hover_source(hovered)

    def _sync_spot_highlight(self) -> None:
        for index in range(len(self.hotspot_rects)):
            visible = index == self.engine.active_track and self.engine.is_loaded
            self.canvas.itemconfig(f"halo{index}", state="normal" if visible else "hidden")
            chip = self.chips[index] if index < len(self.chips) else None
            if chip is not None:
                chip.set_active(index == self.engine.active_track)

    def _build_tracks(self, parent: tk.Misc) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(2, 4))
        tk.Label(row, text="PISTE", bg=BG, fg=MUTED, font=self.fonts.tiny).pack(side="left", padx=(2, 10))
        chips = tk.Frame(row, bg=BG)
        chips.pack(side="left")
        for index, label in enumerate(TRACK_LABELS):
            chip = TrackChip(
                chips,
                index,
                label,
                self.fonts,
                on_click=self.select_track,
                on_hover=self._on_chip_hover,
            )
            chip.pack(side="left", padx=2)
            self.chips.append(chip)
        self.track_hint = tk.Label(row, text="", bg=BG, fg=MUTED, font=self.fonts.tiny)
        self.track_hint.pack(side="right", padx=2)

    def _build_transport(self, parent: tk.Misc) -> None:
        panel = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill="x", pady=(10, 6))
        inner = tk.Frame(panel, bg=SURFACE)
        inner.pack(fill="x", padx=10, pady=10)

        left = tk.Frame(inner, bg=SURFACE)
        left.pack(side="left")
        self.btn_play = RoundedButton(
            left,
            "Lecture",
            command=self.toggle_play,
            width=132,
            height=44,
            fill=ACCENT,
            hover=ACCENT_HOVER,
            foreground=ACCENT_TEXT,
            outline=ACCENT,
            font=self.fonts.button,
        )
        self.btn_play.pack(side="left")
        self.btn_stop = RoundedButton(
            left,
            "Arrêt",
            command=self.stop,
            width=76,
            height=44,
            font=self.fonts.button,
            fill=SURFACE_HI,
        )
        self.btn_stop.pack(side="left", padx=(6, 0))

        right = tk.Frame(inner, bg=SURFACE)
        right.pack(side="right")
        self.time_label = tk.Label(right, text="00:00", bg=SURFACE, fg=TEXT, font=self.fonts.big_time)
        self.time_label.pack(anchor="e")
        self.duration_label = tk.Label(right, text="/ 00:00", bg=SURFACE, fg=MUTED, font=self.fonts.small)
        self.duration_label.pack(anchor="e")

    def _build_seek(self, parent: tk.Misc) -> None:
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x")
        self.seek = SeekBar(box, self.on_seek, self.fonts)
        self.seek.pack(fill="x", pady=(0, 2))
        controls = tk.Frame(box, bg=BG, height=30)
        controls.pack(fill="x")
        controls.pack_propagate(False)
        for text, delta in (("−30 s", -SEEK_STEP_LARGE_MS), ("−5 s", -SEEK_STEP_MS)):
            RoundedButton(
                controls, text, command=lambda d=delta: self.nudge(d),
                width=54, height=26, font=self.fonts.tiny, fill=SURFACE,
            ).pack(side="left", padx=(0, 4))
        for text, delta in (("+5 s", SEEK_STEP_MS), ("+30 s", SEEK_STEP_LARGE_MS)):
            RoundedButton(
                controls, text, command=lambda d=delta: self.nudge(d),
                width=54, height=26, font=self.fonts.tiny, fill=SURFACE,
            ).pack(side="left", padx=(0, 4))

        volume_box = tk.Frame(controls, bg=BG)
        volume_box.pack(side="right")
        tk.Label(volume_box, text="VOL", bg=BG, fg=MUTED, font=self.fonts.tiny).pack(side="left", padx=(0, 4))
        self.volume = VolumeBar(volume_box, self.on_volume, self.config.volume)
        self.volume.configure(width=90)
        self.volume.pack(side="left")
        self.volume_label = tk.Label(volume_box, text=f"{int(self.config.volume * 100)} %", bg=BG, fg=MUTED, font=self.fonts.tiny, width=5, anchor="e")
        self.volume_label.pack(side="left")

    def _build_footer(self, parent: tk.Misc) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(8, 4))
        self.cassette_label = tk.Label(
            footer,
            text="Aucune cassette chargée",
            bg=BG,
            fg=TEXT,
            font=(self.fonts.family, 10, "bold"),
            anchor="w",
        )
        self.cassette_label.pack(side="left", fill="x", expand=True)
        RoundedButton(
            footer,
            "Dossier",
            command=self.open_cassette_folder,
            width=72,
            height=26,
            font=self.fonts.tiny,
            fill=SURFACE,
        ).pack(side="right")
        RoundedButton(
            footer,
            "Charger…",
            command=self.open_library,
            width=82,
            height=26,
            font=self.fonts.tiny,
            fill=SURFACE_HI,
            accent=ACCENT,
        ).pack(side="right", padx=(0, 6))

    def _build_statusbar(self, parent: tk.Misc) -> None:
        bar = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", side="bottom", pady=(6, 0))
        self.status_label = tk.Label(
            bar,
            text="Prêt.",
            bg=SURFACE,
            fg=MUTED,
            font=self.fonts.small,
            anchor="w",
            padx=8,
            pady=5,
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        self.audio_label = tk.Label(
            bar,
            text="sortie audio",
            bg=SURFACE,
            fg=MUTED,
            font=self.fonts.tiny,
            padx=8,
        )
        self.audio_label.pack(side="right")

    # ---------------------------------------------------------- raccourcis
    def _bind_keys(self) -> None:
        bindings = {
            "<space>": lambda _e: self.toggle_play(),
            "<Return>": lambda _e: self.toggle_play(),
            "<s>": lambda _e: self.stop(),
            "<S>": lambda _e: self.stop(),
            "<o>": lambda _e: self.open_library(),
            "<O>": lambda _e: self.open_library(),
            "<Left>": lambda _e: self.nudge(-SEEK_STEP_MS),
            "<Right>": lambda _e: self.nudge(SEEK_STEP_MS),
            "<Shift-Left>": lambda _e: self.nudge(-SEEK_STEP_LARGE_MS),
            "<Shift-Right>": lambda _e: self.nudge(SEEK_STEP_LARGE_MS),
            "<Home>": lambda _e: self.seek_to(0),
            "<Escape>": lambda _e: self.stop(),
        }
        for index in range(4):
            bindings[str(index + 1)] = lambda _e, i=index: self.select_track(i)
            bindings[f"<KP_{index + 1}>"] = lambda _e, i=index: self.select_track(i)
            # Lettres sérigraphiées sur le jouet : A = 2, B = 3, C = 4.
            # La piste 1 (OUI) n'a pas de lettre.
            letter = TRACK_KEYS[index]
            if letter:
                bindings[letter.lower()] = lambda _e, i=index: self.select_track(i)
                bindings[letter.upper()] = lambda _e, i=index: self.select_track(i)
        bindings["<F1>"] = lambda _e: self.show_about()
        for sequence, callback in bindings.items():
            self.root.bind(sequence, callback, add="+")

    # ------------------------------------------------------------- cassette
    def _autoload(self) -> None:
        for record in [self.config.last_cassette, *self.config.recent]:
            if not record:
                continue
            try:
                cassette = _cassette_from_record(record)
            except CassetteError:
                continue
            self.load(cassette, remember=False)
            return
        library = scan_library()
        if library:
            self.load(library[0], remember=False)
            self.set_status(f"{len(library)} cassette(s) trouvée(s) dans le dossier « cassettes ».")
        else:
            self.set_status("Aucune cassette chargée : clique sur « Charger… » pour commencer.")

    def load(self, cassette: Cassette, *, remember: bool = True) -> None:
        try:
            self.engine.load(cassette)
        except CassetteError as exc:
            messagebox.showerror("Lecture impossible", str(exc))
            return
        self.cassette = cassette
        self.engine.set_active_track(min(self.config.last_track, 3), force=True)
        self.config.last_track = self.engine.active_track
        if remember:
            self.config.remember(cassette)
        self.cassette_label.config(
            text=f"{cassette.name}  ·  {format_time(cassette.duration_s * 1000)}",
            fg=TEXT,
        )
        self.seek.set_enabled(True)
        self._sync_spot_highlight()
        self._refresh_transport()
        warnings = cassette.warnings()
        if warnings:
            self.set_status(" ".join(warnings), WARN)
        else:
            self.set_status(f"Cassette « {cassette.name} » prête. Espace pour écouter.")
        self._update_position_ui()

    def open_library(self) -> None:
        LibraryDialog(self.root, self, self.fonts)

    def load_folder(self, folder: str | Path) -> None:
        try:
            cassette = load_cassette(folder)
        except CassetteError as exc:
            messagebox.showerror("Cassette illisible", str(exc))
            return
        self.load(cassette)

    def load_files(self, paths: list[str], name: str | None = None) -> None:
        try:
            cassette = cassette_from_paths(paths, name or "Cassette personnalisée", None)
        except CassetteError as exc:
            messagebox.showerror("Cassette illisible", str(exc))
            return
        self.load(cassette)

    def open_cassette_folder(self) -> None:
        from .library import cassettes_dir

        target = self.cassette.folder if self.cassette and self.cassette.folder else cassettes_dir()
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                import os

                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", str(target)])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:
            messagebox.showinfo("Dossier des cassettes", f"{target}\n\n({exc})")

    # ------------------------------------------------------------- lecture
    def toggle_play(self) -> None:
        if not self.engine.is_loaded and not self._autoload_now():
            return
        if self.engine.is_playing or self.engine.is_paused:
            if self.engine.is_paused:
                self.engine.resume()
                self.set_status("Lecture.")
            else:
                self.engine.pause()
                self.set_status("En pause.")
            self._refresh_transport()
        else:
            try:
                self.engine.play()
            except Exception as exc:
                self._audio_failure(exc)
                return
            self.set_status("Lecture.")
        # Rafraîchit immédiatement le libellé (Lecture / Pause / Reprendre).
        self._refresh_transport()

    def _autoload_now(self) -> bool:
        library = scan_library()
        if library:
            self.load(library[0])
            return True
        self.open_library()
        return False

    def _audio_failure(self, exc: Exception) -> None:
        messagebox.showerror(
            "Sortie audio indisponible",
            "Impossible de lire l'audio sur cette machine.\n\n"
            f"{exc}\n\n"
            "Vérifie qu'un périphérique de lecture est branché et qu'aucune "
            "autre application ne l'occupe exclusivement.",
        )
        self.set_status("Erreur de sortie audio.", ERROR)

    def stop(self) -> None:
        self.engine.stop()
        self.set_status("Arrêté.")
        self._refresh_transport()
        self._update_position_ui()

    def select_track(self, index: int) -> None:
        if not self.engine.is_loaded:
            self.set_status("Charge d'abord une cassette.", WARN)
            return
        self.engine.set_active_track(index)
        self.selection_count += 1
        self.config.last_track = index
        self._sync_spot_highlight()
        label = TRACK_LABELS[index]
        button_name = f"{index + 1}"
        if TRACK_KEYS[index]:
            button_name += f" {TRACK_KEYS[index]}"
        self.set_status(
            f"Bouton {button_name} sélectionné ({label})."
            + ("" if self.engine.is_playing or self.engine.is_paused else " Appuie sur Lecture.")
        )

    def seek_to(self, ms: float) -> None:
        if not self.engine.is_loaded:
            return
        self.engine.seek(ms)
        self._update_position_ui()

    def nudge(self, delta_ms: float) -> None:
        if not self.engine.is_loaded:
            return
        target = self.engine.position_ms + delta_ms
        self.engine.seek(min(max(0.0, target), self.engine.duration_ms))
        self._update_position_ui()

    def on_seek(self, ms: float) -> None:
        self.seek_to(ms)
        self.set_status(f"Position : {format_time(ms)}.")

    def on_volume(self, ratio: float) -> None:
        self.engine.set_volume(ratio)
        self.config.volume = ratio
        self.volume_label.config(text=f"{int(ratio * 100)} %")
        self.volume._draw()

    # -------------------------------------------------------------- état
    def _refresh_transport(self) -> None:
        playing = self.engine.is_playing
        paused = self.engine.is_paused
        loaded = self.engine.is_loaded
        # Un seul bouton principal : il annonce l'action suivante. L'état est
        # lu sur le moteur, mis à jour dès le clic par play/pause/resume.
        if playing:
            self.btn_play.set_text("Pause")
        elif self.engine.state == "paused":
            self.btn_play.set_text("Reprendre")
        elif self.engine.state == "stopped" and self.engine.position_ms <= 1:
            self.btn_play.set_text("Lecture")
        else:
            self.btn_play.set_text("Reprendre")
        self.btn_stop.set_enabled(True)
        self.btn_play.set_glow(playing)
        state = self.engine.state
        self._last_state = state
        color = OK if playing else (WARN if paused else BORDER)
        self.led.itemconfig(self._led_dot, fill=color)
        if loaded:
            self.duration_label.config(text=f"/ {format_time(self.engine.duration_ms)}")
        else:
            self.duration_label.config(text="/ 00:00")

    def _update_position_ui(self) -> None:
        position = self.engine.position_ms
        duration = self.engine.duration_ms
        if not self.seek.dragging:
            self.seek.set_state(position, duration)
        self.time_label.config(text=format_time(position))
        if not self.engine.is_loaded:
            self.time_label.config(text="00:00")

    def _schedule_tick(self) -> None:
        self._tick_job = self.root.after(60, self._tick)

    def _tick(self) -> None:
        try:
            for kind, payload in self.engine.drain_events():
                self._on_engine_event(kind, payload)
            self._update_position_ui()
            self._refresh_transport()
        finally:
            self._schedule_tick()

    def _refresh_transport_light(self) -> None:
        state = self.engine.state
        color = OK if state == "playing" else (WARN if state == "paused" else BORDER)
        self.led.itemconfig(self._led_dot, fill=color)

    def _on_engine_event(self, kind: str, payload: str) -> None:
        if kind == "state":
            self._refresh_transport()
        if kind == "ended":
            self.set_status("Fin de la cassette.", MUTED)
            self._refresh_transport()
        elif kind == "state":
            if payload == "stopped":
                self._refresh_transport()
        elif kind == "status" and payload:
            self.set_status(f"Audio : {payload}", WARN)
        elif kind == "error" and payload:
            self.set_status(f"Erreur audio : {payload}", ERROR)

    def set_status(self, message: str, color: str = MUTED) -> None:
        self.status_label.config(text=message, fg=color)

    def show_about(self) -> None:
        messagebox.showinfo(
            f"À propos de {__app_name__}",
            f"{__app_name__} {__version__} — par {__app_author__}\n\n"
            "Émulateur du robot 2-XL de Tiger (1992), limité aux cassettes "
            "françaises à quatre pistes.\n\n"
            "Boutons : 1 OUI, 2 A (NON), 3 B (VRAI), 4 C (FAUX).\n\n"
            "Les quatre pistes avancent ensemble ; changer de bouton revient à "
            "déplacer la tête de lecture, exactement comme sur le jouet.\n\n"
            "Raccourcis : Espace lecture/pause, S arrêt, flèches ±5 s, "
            "Maj+flèches ±30 s, 1 à 4 ou A B C pour les boutons, "
            "O pour charger une cassette.",
        )

    def on_close(self) -> None:
        if self._tick_job:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._tick_job)
        self.config.volume = self.engine.volume
        self.config.last_track = self.engine.active_track
        save_config(self.config)
        self.engine.close()
        self.root.destroy()


# --------------------------------------------------------------------------
# Sélecteur de cassette
# --------------------------------------------------------------------------


class LibraryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, app: TwoXLApp, fonts: Fonts) -> None:
        super().__init__(parent)
        self.app = app
        self.fonts = fonts
        self.title("Choisir une cassette")
        self.configure(bg=BG)
        self.geometry("520x420")
        self.minsize(460, 360)
        self.transient(parent)
        self.grab_set()
        self._entries: list[Cassette] = []

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(header, text="Cassettes disponibles", bg=BG, fg=TEXT, font=(fonts.family, 13, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="Dossier « cassettes » de l'application, ou n'importe quel dossier de ton disque.",
            bg=BG,
            fg=MUTED,
            font=fonts.small,
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        self.listbox = tk.Listbox(
            body,
            bg=SURFACE,
            fg=TEXT,
            selectbackground=ACCENT,
            selectforeground=BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            activestyle="none",
            font=(fonts.family, 10),
            bd=0,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._choose())
        self.listbox.bind("<Return>", lambda _e: self._choose())

        self.hint = tk.Label(self, text="", bg=BG, fg=MUTED, font=fonts.small, anchor="w")
        self.hint.pack(fill="x", padx=16, pady=(8, 6))

        actions = tk.Frame(self, bg=BG)
        actions.pack(fill="x", padx=16, pady=(0, 14))
        RoundedButton(actions, "Annuler", command=self.destroy, width=86, height=32, font=fonts.small, fill=SURFACE).pack(side="right")
        RoundedButton(actions, "Ouvrir", command=self._choose, width=86, height=32, font=fonts.small, fill=ACCENT, foreground=ACCENT_TEXT, outline=ACCENT).pack(side="right", padx=(0, 6))
        RoundedButton(actions, "Choisir un dossier…", command=self._choose_folder, width=150, height=32, font=fonts.small, fill=SURFACE_HI).pack(side="left")
        RoundedButton(actions, "Quatre fichiers…", command=self._choose_files, width=132, height=32, font=fonts.small, fill=SURFACE_HI).pack(side="left", padx=(6, 0))

        self._populate()
        self.listbox.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _populate(self) -> None:
        self.listbox.delete(0, "end")
        self._entries = scan_library()
        if not self._entries:
            self.listbox.insert("end", "  (aucune cassette dans le dossier « cassettes »)")
            self.listbox.itemconfig(0, foreground=MUTED)
            self.hint.config(text="Utilise « Choisir un dossier… » pour pointer vers tes quatre MP3.")
            return
        for cassette in self._entries:
            self.listbox.insert("end", f"  {cassette.name}   ·   {format_time(cassette.duration_s * 1000)}")
        self.listbox.selection_set(0)
        self.hint.config(text=f"{len(self._entries)} cassette(s) détectée(s).")

    def _choose(self) -> None:
        selection = self.listbox.curselection()
        if not selection or not self._entries:
            return
        cassette = self._entries[selection[0]]
        self.app.load(cassette)
        self.destroy()

    def _choose_folder(self) -> None:
        initial = self.app.config.last_folder
        folder = filedialog.askdirectory(
            parent=self,
            title="Dossier contenant les quatre pistes",
            initialdir=str(initial) if initial else None,
            mustexist=True,
        )
        if not folder:
            return
        path = Path(folder)
        try:
            paths = detect_track_paths(path)
        except CassetteError as exc:
            messagebox.showerror("Dossier incomplet", str(exc), parent=self)
            return
        self._show_preview(paths, path.name, folder=path)

    def _choose_files(self) -> None:
        initial = self.app.config.last_folder
        files = filedialog.askopenfilenames(
            parent=self,
            title="Sélectionne les quatre pistes (OUI, NON, VRAI, FAUX)",
            initialdir=str(initial) if initial else None,
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a"), ("Tous les fichiers", "*.*")],
        )
        if not files:
            return
        if len(files) != 4:
            messagebox.showwarning(
                "Quatre fichiers",
                "Sélectionne exactement quatre fichiers audio : ils seront "
                "attribués dans l'ordre OUI, NON, VRAI, FAUX.",
                parent=self,
            )
            return
        self._show_preview(list(Path(f) for f in files), "Cassette personnalisée")

    def _show_preview(self, paths: list[Path], name: str, folder: Path | None = None) -> None:
        try:
            cassette = cassette_from_paths(list(paths), name, folder)
        except CassetteError as exc:
            messagebox.showerror("Cassette illisible", str(exc), parent=self)
            return
        details = "\n".join(
            f"  Piste {i + 1} ({TRACK_LABELS[i]}) : {track.filename} — {format_time(track.duration_s * 1000)}"
            for i, track in enumerate(cassette.tracks)
        )
        warnings = cassette.warnings()
        message = f"{cassette.name}\n\n{details}"
        if warnings:
            message += "\n\n" + "\n".join(warnings)
        if not messagebox.askokcancel("Charger cette cassette ?", message, parent=self):
            return
        self.app.load(cassette)
        self.destroy()


def _cassette_from_record(record: dict) -> Cassette:
    from .library import cassette_from_record

    return cassette_from_record(record)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------


def run(*, autoload: bool = True, folder: str | None = None) -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Impossible d'ouvrir la fenêtre : {exc}", file=sys.stderr)
        return 2
    app = TwoXLApp(root, autoload=autoload)
    if folder:
        try:
            app.load_folder(folder)
        except CassetteError as exc:
            app.set_status(f"Cassette non chargée : {exc}", ERROR)
    try:
        root.mainloop()
    finally:
        with contextlib.suppress(Exception):
            app.engine.close()
    return 0
