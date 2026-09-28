#!/usr/bin/env python3
"""
ChatForge
=========

A desktop tool that turns an AI conversation into a real project on disk.

It understands four pieces of special syntax inside a pasted conversation:

  >>filename<</.ext/                new file, saved at the project root
  #folder#sub#>>filename<</.ext/    new file, nested under folder/sub/
  $>>filename<</.ext/               PATCH: replace the contents of an existing file
  $#folder#>>filename<</.ext/       PATCH: same, but nested
  ^start^ ... ^end^                 wraps the literal file contents
  %ProjectName%                     (anywhere, outside code blocks) suggests
                                     the project's root folder name

Workflow: choose a project folder (new or existing -- it loads what's
already there) -> paste a conversation -> Analyze -> review the file tree
-> Save Files. Projects live as minimized tabs on the left sidebar, each
with its own conversation box and pending changes, and can be updated over
many separate conversations.

Pure standard library. Run with:  python ai_chat_to_files.py
"""

import os
import re
import sys
import queue
import difflib
import platform
import io
import base64
import urllib.error
import urllib.request
import subprocess
import threading
import traceback
import itertools
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple

# --------------------------------------------------------------------------
# Optional dependency: tkinterdnd2 (drag & drop of a .txt conversation file).
# Purely a convenience; the app is fully functional without it.
# --------------------------------------------------------------------------
DND_AVAILABLE = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    DND_AVAILABLE = True
except ImportError:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "tkinterdnd2"],
            timeout=12, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
        DND_AVAILABLE = True
    except Exception:
        DND_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

BaseTkClass = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


# ==========================================================================
# THEME
# ==========================================================================
class Theme:
    bg = "#14161c"
    bg_panel = "#1b1e26"
    bg_panel_alt = "#20242e"
    bg_input = "#0f1116"
    bg_raised = "#262b36"
    border = "#2d3340"
    border_light = "#3a4152"
    text = "#e7e9ee"
    text_dim = "#8a91a3"
    text_faint = "#5b6172"
    accent = "#59e6c8"
    accent_soft = "#1f3b37"
    accent_dark = "#2b8f79"
    warn = "#f2b84b"
    warn_soft = "#3a2f18"
    error = "#f2694b"
    error_soft = "#3a1f1a"
    ok = "#59e6c8"
    diff_add = "#3fbf78"
    diff_add_bg = "#132a1f"
    diff_del = "#f2694b"
    diff_del_bg = "#2e1a17"
    diff_meta = "#6f9bd1"
    select = "#28323a"
    tab_active = "#20242e"
    tab_inactive = "#171a21"
    mono = ("Cascadia Code", 11) if platform.system() == "Windows" else ("Menlo", 12) if platform.system() == "Darwin" else ("DejaVu Sans Mono", 11)
    mono_small = ("Cascadia Code", 10) if platform.system() == "Windows" else ("Menlo", 11) if platform.system() == "Darwin" else ("DejaVu Sans Mono", 10)
    ui = ("Segoe UI", 11) if platform.system() == "Windows" else ("Helvetica Neue", 12) if platform.system() == "Darwin" else ("DejaVu Sans", 11)
    ui_bold = ("Segoe UI", 11, "bold") if platform.system() == "Windows" else ("Helvetica Neue", 12, "bold") if platform.system() == "Darwin" else ("DejaVu Sans", 11, "bold")
    ui_small = ("Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica Neue", 11) if platform.system() == "Darwin" else ("DejaVu Sans", 10)
    ui_title = ("Segoe UI", 15, "bold") if platform.system() == "Windows" else ("Helvetica Neue", 16, "bold") if platform.system() == "Darwin" else ("DejaVu Sans", 14, "bold")
    radius = 10          # default corner radius for rounded buttons/panels
    radius_sm = 8         # smaller radius for compact elements (tab badges, chips)
    rail_width = 68       # width of the minimized project sidebar


# ==========================================================================
# ROUNDED-CORNER UI PRIMITIVES  (pure Canvas -- no external image libraries)
# ==========================================================================
def round_rectangle(canvas: tk.Canvas, x1, y1, x2, y2, radius=10, **kwargs):
    """Draw a rounded rectangle on a Canvas via a smoothed polygon."""
    r = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundedButton(tk.Canvas):
    """A small, self-contained rounded-corner button drawn on a Canvas.
    Behaves like a normal button for our purposes: takes text + a command,
    supports a primary/secondary color scheme, hover feedback, and an
    enabled/disabled state -- without needing any image assets."""

    def __init__(self, parent, text, command=None, *, panel_bg, primary=False,
                 small=False, danger=False, width=None, height=None,
                 radius=None, font=None, padx=None, pady=None):
        self._text = text
        self._command = command
        self._radius = radius if radius is not None else (Theme.radius_sm if small else Theme.radius)
        self._font = font or (Theme.ui_small if small else Theme.ui)
        self._panel_bg = panel_bg
        self._disabled = False

        if primary:
            self._bg, self._fg, self._hover = Theme.accent, "#0e1512", "#7ff0d8"
        elif danger:
            self._bg, self._fg, self._hover = Theme.bg_input, Theme.error, Theme.select
        else:
            self._bg, self._fg, self._hover = Theme.bg_input, Theme.text, Theme.select

        padx = padx if padx is not None else (12 if small else 18)
        pady = pady if pady is not None else (6 if small else 9)

        probe = tk.Label(parent, text=text, font=self._font)
        probe.update_idletasks()
        w = width or (probe.winfo_reqwidth() + padx * 2)
        h = height or (probe.winfo_reqheight() + pady * 2)
        probe.destroy()

        super().__init__(parent, width=w, height=h, bg=panel_bg, highlightthickness=0,
                          bd=0, cursor="hand2" if command else "arrow")
        self._btn_w, self._btn_h = w, h
        self._paint(self._bg)

        if command:
            self.bind("<Button-1>", self._on_click)
            self.bind("<Enter>", self._on_enter)
            self.bind("<Leave>", self._on_leave)

    def _paint(self, fill):
        self.delete("all")
        round_rectangle(self, 1, 1, self._btn_w - 1, self._btn_h - 1, radius=self._radius, fill=fill, outline=fill)
        text_color = Theme.text_faint if self._disabled else self._fg
        self.create_text(self._btn_w / 2, self._btn_h / 2, text=self._text, fill=text_color, font=self._font)

    def _on_enter(self, event=None):
        if not self._disabled:
            self._paint(self._hover)

    def _on_leave(self, event=None):
        if not self._disabled:
            self._paint(self._bg)

    def _on_click(self, event=None):
        if not self._disabled and self._command:
            self._command()

    def set_text(self, text: str):
        self._text = text
        self._paint(self._bg)

    def set_enabled(self, enabled: bool):
        self._disabled = not enabled
        self.configure(cursor="hand2" if (enabled and self._command) else "arrow")
        self._paint(self._bg)


class RoundedFrame(tk.Frame):
    """A container with a rounded-corner card background. `.body` is a
    plain tk.Frame to pack/grid normal content into; it sits inset from the
    canvas edges by the corner radius so the rounding is never covered up,
    while still looking like one seamless rounded card since both share
    the same fill color."""

    def __init__(self, parent, *, panel_bg, card_bg=None, radius=None):
        super().__init__(parent, bg=panel_bg, highlightthickness=0, bd=0)
        self._radius = radius if radius is not None else Theme.radius
        self._card_bg = card_bg if card_bg is not None else Theme.bg_panel
        self._canvas = tk.Canvas(self, bg=panel_bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=self._card_bg)
        inset = self._radius
        self.body.place(x=inset, y=inset, relwidth=1, relheight=1,
                         width=-2 * inset, height=-2 * inset)
        self._canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        self._canvas.delete("all")
        round_rectangle(self._canvas, 1, 1, w - 1, h - 1, radius=self._radius,
                         fill=self._card_bg, outline=self._card_bg)


class Tooltip:
    """A minimal hover tooltip -- used for the minimized project sidebar,
    where a project's full name doesn't fit in the narrow badge."""

    def __init__(self, widget, text_getter):
        self.widget = widget
        self.text_getter = text_getter
        self._win = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None):
        text = self.text_getter()
        if not text or self._win is not None:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() // 2 - 10
        self._win = tk.Toplevel(self.widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(self._win, text=text, bg=Theme.bg_raised, fg=Theme.text, font=Theme.ui_small,
                 padx=8, pady=4, relief="flat").pack()

    def _hide(self, event=None):
        if self._win is not None:
            self._win.destroy()
            self._win = None


# Per-extension language identity: no emoji, no bundled image assets --
# each extension gets a short text abbreviation rendered in that
# language's real, recognizable brand color (the same convention GitHub's
# language bar and most editors use).
LANGUAGE_COLORS = {
    "py": "#4B8BBE", "ipynb": "#4B8BBE",
    "js": "#F0DB4F", "mjs": "#F0DB4F", "cjs": "#F0DB4F",
    "ts": "#3178C6", "tsx": "#3178C6", "jsx": "#61DAFB",
    "html": "#E34F26", "htm": "#E34F26",
    "css": "#2965F1", "scss": "#CC6699", "sass": "#CC6699",
    "json": "#8BC34A", "jsonc": "#8BC34A",
    "yaml": "#CB171E", "yml": "#CB171E", "toml": "#9C4221", "ini": "#9C4221", "env": "#F2B84B",
    "md": "#A0A6B4", "markdown": "#A0A6B4", "txt": "#8A91A3", "rst": "#A0A6B4",
    "java": "#EA8220", "kt": "#A97BFF",
    "c": "#5C6BC0", "h": "#5C6BC0", "cpp": "#00599C", "hpp": "#00599C", "cs": "#9B4F96",
    "go": "#00ADD8", "rs": "#DE7B34", "rb": "#CC342D", "php": "#8892BF",
    "swift": "#FA7343", "dart": "#12B6F5",
    "sql": "#F2A33C", "db": "#F2A33C",
    "sh": "#89E051", "bash": "#89E051", "zsh": "#89E051", "ps1": "#5391FE", "bat": "#89E051",
    "xml": "#6FA8DC", "csv": "#66BB6A",
    "svg": "#FFB13B", "png": "#FFB13B", "jpg": "#FFB13B", "jpeg": "#FFB13B", "gif": "#FFB13B", "ico": "#FFB13B",
    "gitignore": "#8A91A3", "dockerfile": "#2496ED", "lock": "#8A91A3",
}
LANGUAGE_ABBR = {
    "py": "PY", "ipynb": "PY", "js": "JS", "mjs": "JS", "cjs": "JS",
    "ts": "TS", "tsx": "TSX", "jsx": "JSX", "html": "HTML", "htm": "HTML",
    "css": "CSS", "scss": "SCSS", "sass": "SASS", "json": "JSON", "jsonc": "JSON",
    "yaml": "YAML", "yml": "YAML", "toml": "TOML", "ini": "INI", "env": "ENV",
    "md": "MD", "markdown": "MD", "txt": "TXT", "rst": "RST", "java": "JAVA", "kt": "KT",
    "c": "C", "h": "H", "cpp": "C++", "hpp": "H++", "cs": "C#",
    "go": "GO", "rs": "RS", "rb": "RB", "php": "PHP", "swift": "SWIFT", "dart": "DART",
    "sql": "SQL", "db": "DB", "sh": "SH", "bash": "SH", "zsh": "ZSH", "ps1": "PS1", "bat": "BAT",
    "xml": "XML", "csv": "CSV", "svg": "SVG", "png": "PNG", "jpg": "JPG", "jpeg": "JPG",
    "gif": "GIF", "ico": "ICO", "gitignore": "GIT", "dockerfile": "DOCK", "lock": "LOCK",
}


def language_color(ext: str) -> str:
    return LANGUAGE_COLORS.get(ext.lower(), Theme.text_faint)


def language_abbr(ext: str) -> str:
    if not ext:
        return "FILE"
    return LANGUAGE_ABBR.get(ext.lower(), ext.upper()[:4])


def make_dot_icon(color: str, size: int = 10) -> tk.PhotoImage:
    """A small, real (procedurally drawn, transparent) filled-circle status
    icon -- used instead of emoji for new/patch/existing/duplicate markers."""
    img = tk.PhotoImage(width=size, height=size)
    cx = cy = (size - 1) / 2
    r = size / 2 - 0.5
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            if (dx * dx + dy * dy) ** 0.5 <= r:
                img.put(color, (x, y))
            else:
                img.transparency_set(x, y, True)
    return img


def make_folder_icon(size: int = 16, color: str = "#9bb6d6") -> tk.PhotoImage:
    """A small, real (procedurally drawn, transparent) folder icon -- used
    instead of an emoji folder glyph in the project tree."""
    img = tk.PhotoImage(width=size, height=size)
    for y in range(size):
        for x in range(size):
            img.transparency_set(x, y, True)
    x0, x1 = int(size * 0.08), int(size * 0.92)
    tab_h = max(2, int(size * 0.20))
    tab_w = int(size * 0.5)
    body_bottom = int(size * 0.82)
    for y in range(1, tab_h + 1):
        for x in range(x0, x0 + tab_w):
            if 0 <= x < size:
                img.put(color, (x, y))
    for y in range(tab_h + 1, body_bottom):
        for x in range(x0, x1):
            if 0 <= x < size:
                img.put(color, (x, y))
    return img


def make_app_icon(size: int = 48) -> tk.PhotoImage:
    """Procedurally draw ChatForge's mark -- a rounded teal spark/anvil --
    as a tk.PhotoImage, with no external image files or libraries needed."""
    img = tk.PhotoImage(width=size, height=size)
    bg = "#14161c"
    accent = "#59e6c8"
    accent_dark = "#2b8f79"
    spark = "#0e1512"
    cx = cy = size / 2
    r = size * 0.46
    for y in range(size):
        row_colors = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > r:
                row_colors.append(bg)
                continue
            # simple vertical gradient between accent and accent_dark for a bit of depth
            t = (y / size)
            col = accent if t < 0.55 else accent_dark
            row_colors.append(col)
        img.put("{" + " ".join(row_colors) + "}", to=(0, y))
    # a small stylized spark/flame mark in the center, evoking a forge
    for y in range(int(size * 0.22), int(size * 0.80)):
        t = (y - size * 0.22) / (size * 0.58)
        half_w = max(1, int(size * 0.16 * (1 - abs(t - 0.55) * 1.6)))
        for x in range(int(cx - half_w), int(cx + half_w) + 1):
            if 0 <= x < size:
                img.put(spark, (x, y))
    return img


# ==========================================================================
# REAL LANGUAGE LOGOS
#
# Logos are ordinary image files in a "logos" folder next to this script,
# named by Devicon name (python.png, javascript.png, react.png, ...).
#   * PNG/GIF are read natively by Tkinter (Pillow, if installed, is only
#     used to resize more smoothly).
#   * On first launch, missing logos are downloaded in the background from
#     the open-source Devicon set (SVG), then converted to PNG -- using
#     Tk's own SVG support if your Tk has it, otherwise cairosvg if you have
#     it installed. If neither can convert, drop your own PNGs into the
#     logos folder using the names below and they will be used.
#   * Any file type without a logo falls back to a colored abbreviation.
# ==========================================================================
LOGO_DIR = Path(__file__).resolve().parent / "logos"
DEVICON_URL = "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/{n}/{n}-original.svg"

LOGO_NAME = {
    "py": "python", "ipynb": "python",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "react", "jsx": "react",
    "html": "html5", "htm": "html5",
    "css": "css3", "scss": "sass", "sass": "sass",
    "json": "json", "jsonc": "json",
    "yaml": "yaml", "yml": "yaml",
    "md": "markdown", "markdown": "markdown",
    "java": "java", "kt": "kotlin",
    "c": "c", "h": "c", "cpp": "cplusplus", "hpp": "cplusplus", "cs": "csharp",
    "go": "go", "rs": "rust", "rb": "ruby", "php": "php", "swift": "swift", "dart": "dart",
    "sql": "mysql", "db": "mysql",
    "sh": "bash", "bash": "bash", "zsh": "bash", "ps1": "powershell",
    "xml": "xml", "dockerfile": "docker", "gitignore": "git",
    "vue": "vuejs", "lua": "lua", "r": "r",
}


class LogoManager:
    """Loads real logo images for file types, shows a small status dot on
    the corner of each one, and fetches missing logos in the background."""

    def __init__(self, root, size: int = 16):
        self.root = root
        self.size = size
        self._base: Dict[str, Optional[tk.PhotoImage]] = {}
        self._composed: Dict[tuple, tk.PhotoImage] = {}
        try:
            LOGO_DIR.mkdir(exist_ok=True)
        except Exception:
            pass

    @staticmethod
    def png_path(name: str) -> Path:
        return LOGO_DIR / f"{name}.png"

    @staticmethod
    def svg_path(name: str) -> Path:
        return LOGO_DIR / f"{name}.svg"

    # ---- background download (file I/O only -- no Tk calls off-thread) ----
    def start_download(self, result_queue: "queue.Queue"):
        names = sorted(set(LOGO_NAME.values()))
        missing = [n for n in names if not self.png_path(n).exists() and not self.svg_path(n).exists()]
        if not missing:
            return

        def worker():
            got = 0
            for n in missing:
                try:
                    req = urllib.request.Request(DEVICON_URL.format(n=n), headers={"User-Agent": "ChatForge"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = resp.read()
                    if b"<svg" in data[:2000]:
                        self.svg_path(n).write_bytes(data)
                        got += 1
                except urllib.error.HTTPError:
                    continue            # this one logo doesn't exist upstream; skip it
                except Exception:
                    break               # offline / blocked: stop trying this launch
            result_queue.put(("logos_ready", got))

        threading.Thread(target=worker, daemon=True).start()

    # ---- SVG -> PNG (main thread) ----
    def convert_pending(self) -> bool:
        made = False
        for svg in list(LOGO_DIR.glob("*.svg")):
            png = svg.with_suffix(".png")
            if png.exists():
                continue
            if self._convert_svg(svg, png):
                made = True
        if made:
            self._base.clear()
            self._composed.clear()
        return made

    def _convert_svg(self, svg: Path, png: Path) -> bool:
        try:  # 1) Tk's built-in SVG support (Tk 8.7 / 9.0+)
            img = tk.PhotoImage(master=self.root, file=str(svg), format="svg -scaletoheight 64")
            img.write(str(png), format="png")
            return png.exists()
        except Exception:
            pass
        try:  # 2) cairosvg, if it happens to be installed
            import cairosvg  # type: ignore
            cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=64, output_height=64)
            return png.exists()
        except Exception:
            return False

    # ---- loading + status-dot overlay ----
    def _load(self, name: str) -> Optional[tk.PhotoImage]:
        if name in self._base:
            return self._base[name]
        img = None
        path = self.png_path(name)
        if path.exists():
            img = self._load_png(path)
        self._base[name] = img
        return img

    def _load_png(self, path: Path) -> Optional[tk.PhotoImage]:
        s = self.size
        try:
            from PIL import Image  # optional: smoother resizing
            resample = getattr(Image, "Resampling", Image).LANCZOS
            im = Image.open(path).convert("RGBA").resize((s, s), resample)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return tk.PhotoImage(master=self.root, data=base64.b64encode(buf.getvalue()))
        except ImportError:
            pass
        except Exception:
            pass
        try:
            img = tk.PhotoImage(master=self.root, file=str(path))
            w = img.width()
            if w > s:
                img = img.subsample(max(1, round(w / s)))
            elif 0 < w < s:
                img = img.zoom(max(1, round(s / w)))
            return img
        except Exception:
            return None

    def icon_for(self, ext: str, kind: str) -> Optional[tk.PhotoImage]:
        """The file type's real logo with a small colored status dot in its
        corner, or None if there's no logo for this type."""
        name = LOGO_NAME.get(ext.lower())
        if not name:
            return None
        key = (name, kind)
        if key in self._composed:
            return self._composed[key]
        base = self._load(name)
        if base is None:
            return None
        colors = {"new": Theme.ok, "patch": Theme.warn, "existing": Theme.text_faint, "duplicate": Theme.error}
        img = base.copy()
        w, h = img.width(), img.height()
        cx, cy = w - 4.5, h - 4.5
        for y in range(max(0, h - 9), h):
            for x in range(max(0, w - 9), w):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                if d <= 2.6:
                    img.put(colors.get(kind, Theme.text_faint), (x, y))
                elif d <= 3.8:
                    img.put(Theme.bg, (x, y))
        self._composed[key] = img
        return img


# ==========================================================================
# PARSER CORE  (pure functions, no UI -- kept separate and unit-testable)
# ==========================================================================
# Marker anatomy, left to right:
#   ($)?                       optional patch flag
#   (#seg#seg#...#)?           optional folder prefix
#   >>raw filename<<           filename (metadata only, never used verbatim)
#   /.extension/               extension (authoritative, from here ONLY)
MARKER_RE = re.compile(
    r'(\$)?'
    r'(#(?:[^#>\n$]+#)+)?'
    r'>>([^<\n]*?)<<'
    r'\s*/\.([A-Za-z0-9_+\-]+)/'
)
CODE_RE = re.compile(r'\^start\^(?:\r?\n)?(.*?)(?:\r?\n)?\^end\^', re.DOTALL)
ROOT_MARKER_RE = re.compile(r'%([^%\n]{1,200})%')


@dataclass
class RawChange:
    """One raw >>...<< or $>>...<< occurrence found in the conversation."""
    index: int
    is_patch: bool
    raw_name: str
    folders: List[str]
    filename: str
    ext: str
    relpath: str
    code: str


@dataclass
class ChangeGroup:
    """All occurrences of one relative path, collapsed into the single
    effective change that will be reviewed and (optionally) written."""
    relpath: str
    folders: List[str]
    filename: str
    ext: str
    code: str                       # last occurrence's raw code
    effective_is_patch: bool
    is_duplicate_new: bool
    occurrence_count: int
    disk_exists_before: bool
    old_code: Optional[str] = None
    target_missing: bool = False
    diff_error: Optional[str] = None
    preview_after: str = ""         # what will actually be written
    action: str = "write"           # resolved just before saving: write | skip


def strip_fence_lines(code: str) -> str:
    """Strip a leading/trailing markdown fence line. The language identifier
    (```python, ```json, ...) is discarded here and NEVER used as, or
    appended to, a filename or extension."""
    lines = code.split('\n')
    if lines and re.match(r'^\s*```', lines[0]):
        lines = lines[1:]
    if lines and re.match(r'^\s*```\s*$', lines[-1]):
        lines = lines[:-1]
    return '\n'.join(lines)


def sanitize_path_parts(raw: str) -> List[str]:
    """Split into safe path components. Rejects traversal and absolute paths."""
    raw = raw.strip()
    if not raw:
        return []
    raw = raw.replace('\\', '/')
    parts = raw.split('/')
    clean: List[str] = []
    for p in parts:
        p = p.strip()
        if p in ('', '.'):
            continue
        if p == '..':
            raise ValueError("path traversal ('..') is not allowed")
        if re.match(r'^[A-Za-z]:$', p):
            raise ValueError("absolute drive paths are not allowed")
        p = re.sub(r'[<>:"|?*#$]', '_', p)
        clean.append(p)
    return clean


def parse_folder_prefix(prefix: Optional[str]) -> List[str]:
    if not prefix:
        return []
    inner = prefix.strip('#')
    if not inner:
        return []
    return [seg for seg in inner.split('#') if seg.strip()]


def build_relative_path(folder_segments: List[str], raw_filename: str, raw_extension: str):
    """THE single, centralized function that combines folder + filename +
    extension into one final path. This is what prevents 'config.json.json'
    style bugs: the extension is appended exactly once, only if not already
    present at the end of the filename."""
    clean_folders: List[str] = []
    for seg in folder_segments:
        clean_folders.extend(sanitize_path_parts(seg))

    name_parts = sanitize_path_parts(raw_filename)
    if not name_parts:
        raise ValueError("filename resolves to an empty path")
    filename_base = name_parts[-1]
    clean_folders.extend(name_parts[:-1])

    ext_clean = raw_extension.strip()
    if ext_clean.startswith('.'):
        ext_clean = ext_clean[1:]
    ext_clean = re.sub(r'[^A-Za-z0-9_+\-]', '', ext_clean)
    if not ext_clean:
        raise ValueError("missing or invalid extension")

    suffix = '.' + ext_clean
    if filename_base.lower().endswith(suffix.lower()):
        final_name = filename_base
    else:
        final_name = filename_base + suffix

    folders_joined = '/'.join(clean_folders)
    relative_path = '/'.join(clean_folders + [final_name]) if clean_folders else final_name
    return folders_joined, filename_base, ext_clean, relative_path


def apply_unified_diff(original: str, diff_text: str) -> str:
    """Best-effort unified-diff applier for the optional 'Apply Text/Diff
    Patch' mode. Raises ValueError if no recognizable hunks are found."""
    orig_lines = original.split('\n')
    diff_lines = diff_text.split('\n')
    result = list(orig_lines)
    hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
    i = 0
    offset = 0
    applied_any = False
    while i < len(diff_lines):
        m = hunk_re.match(diff_lines[i])
        if not m:
            i += 1
            continue
        start_a = int(m.group(1)) - 1
        i += 1
        hunk_body = []
        while i < len(diff_lines) and not hunk_re.match(diff_lines[i]):
            if diff_lines[i].startswith(('---', '+++')):
                i += 1
                continue
            hunk_body.append(diff_lines[i])
            i += 1
        pos = start_a + offset
        new_segment, consumed = [], 0
        for hl in hunk_body:
            if hl.startswith(' '):
                new_segment.append(hl[1:]); consumed += 1
            elif hl.startswith('-'):
                consumed += 1
            elif hl.startswith('+'):
                new_segment.append(hl[1:])
            elif hl == '':
                new_segment.append('')
        result[pos:pos + consumed] = new_segment
        offset += len(new_segment) - consumed
        applied_any = True
    if not applied_any:
        raise ValueError("No recognizable unified-diff hunks (@@ ... @@) found")
    return '\n'.join(result)


def parse_conversation(text: str) -> Tuple[List[RawChange], Optional[str], List[str]]:
    """Scan conversation text for file/patch markers. Returns
    (occurrences, root_name_or_None, errors). Never raises."""
    if not text or not text.strip():
        return [], None, ["The conversation is empty."]

    norm = text.replace('\r\n', '\n').replace('\r', '\n')
    markers = list(MARKER_RE.finditer(norm))
    occurrences: List[RawChange] = []
    errors: List[str] = []
    code_spans: List[Tuple[int, int]] = []

    if not markers:
        errors.append("No file markers found. Expected >>filename<</.ext/, optionally "
                       "prefixed with #folder# segments and/or $ for a patch.")

    for i, m in enumerate(markers):
        is_patch = bool(m.group(1))
        folder_prefix_raw = m.group(2)
        raw_name = m.group(3)
        raw_ext = m.group(4)
        region_start = m.end()
        region_end = markers[i + 1].start() if i + 1 < len(markers) else len(norm)
        region = norm[region_start:region_end]
        prefix_display = ("$" if is_patch else "") + (folder_prefix_raw or "")
        display_name = f"{prefix_display}>>{raw_name.strip()}<</.{raw_ext}/"

        code_match = CODE_RE.search(region)
        if not code_match:
            has_start = '^start^' in region
            has_end = '^end^' in region
            if not has_start:
                errors.append(f"Missing ^start^ marker for {display_name}")
            elif not has_end:
                errors.append(f"Missing ^end^ marker for {display_name}")
            else:
                errors.append(f"Could not read the code block for {display_name}")
            continue

        code_spans.append((region_start + code_match.start(), region_start + code_match.end()))
        code = strip_fence_lines(code_match.group(1))
        folder_segments = parse_folder_prefix(folder_prefix_raw)

        try:
            folders_joined, filename_base, ext_clean, relative_path = build_relative_path(
                folder_segments, raw_name, raw_ext
            )
        except ValueError as e:
            errors.append(f"Invalid file path for {display_name}: {e}")
            continue

        occurrences.append(RawChange(
            index=len(occurrences), is_patch=is_patch, raw_name=raw_name.strip(),
            folders=folders_joined.split('/') if folders_joined else [],
            filename=filename_base, ext=ext_clean, relpath=relative_path, code=code,
        ))

    # Root-folder marker: search only OUTSIDE any ^start^..^end^ code span,
    # so things like CSS "width: 100%;" can never be mistaken for it.
    root_name = None
    leftover_parts, last = [], 0
    for s, e in sorted(code_spans):
        leftover_parts.append(norm[last:s]); last = e
    leftover_parts.append(norm[last:])
    leftover = ''.join(leftover_parts)
    root_matches = list(ROOT_MARKER_RE.finditer(leftover))
    if root_matches:
        candidate = root_matches[-1].group(1).strip()
        try:
            parts = sanitize_path_parts(candidate)
            if parts:
                root_name = '/'.join(parts)
        except ValueError:
            errors.append(f"Ignored invalid project root name '{candidate}'.")

    return occurrences, root_name, errors


def group_changes(occurrences: List[RawChange], out_root: Optional[Path],
                   patch_mode: str = "replace") -> List[ChangeGroup]:
    """Collapse raw occurrences (in document order) into one ChangeGroup per
    relative path. The LAST occurrence for a path wins. A path is treated as
    a patch if it already exists on disk, OR if any occurrence for it used
    the $ patch marker, OR if it was created earlier in this same batch and
    then referenced again (create-then-patch-in-one-conversation)."""
    order: List[str] = []
    by_path: Dict[str, List[RawChange]] = {}
    for occ in occurrences:
        if occ.relpath not in by_path:
            by_path[occ.relpath] = []
            order.append(occ.relpath)
        by_path[occ.relpath].append(occ)

    groups: List[ChangeGroup] = []
    for relpath in order:
        occs = by_path[relpath]
        last_occ = occs[-1]
        has_patch_marker = any(o.is_patch for o in occs)

        disk_exists_before = False
        old_code = None
        if out_root is not None:
            candidate_path = out_root / relpath
            if candidate_path.is_file():
                disk_exists_before = True
                try:
                    old_code = candidate_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    old_code = ""

        effective_is_patch = disk_exists_before or has_patch_marker
        is_duplicate_new = (not effective_is_patch) and len(occs) > 1

        if old_code is None and len(occs) > 1:
            old_code = occs[-2].code  # state right before the final occurrence

        target_missing = False
        diff_error = None
        preview_after = last_occ.code

        if effective_is_patch:
            if old_code is None:
                target_missing = True
                preview_after = last_occ.code
            elif patch_mode == "diff":
                try:
                    preview_after = apply_unified_diff(old_code, last_occ.code)
                except ValueError as e:
                    diff_error = str(e)
                    preview_after = last_occ.code
            else:
                preview_after = last_occ.code  # full-file replacement (default)

        groups.append(ChangeGroup(
            relpath=relpath, folders=last_occ.folders, filename=last_occ.filename, ext=last_occ.ext,
            code=last_occ.code, effective_is_patch=effective_is_patch, is_duplicate_new=is_duplicate_new,
            occurrence_count=len(occs), disk_exists_before=disk_exists_before, old_code=old_code,
            target_missing=target_missing, diff_error=diff_error, preview_after=preview_after,
        ))
    return groups


def build_write_plan(groups: List[ChangeGroup], create_missing_patch_targets: bool) -> List[ChangeGroup]:
    for g in groups:
        if g.effective_is_patch and g.target_missing and not create_missing_patch_targets:
            g.action = "skip"
        elif g.effective_is_patch and g.diff_error:
            g.action = "skip"   # never write an unapplied/garbled diff
        else:
            g.action = "write"
        g.final_relpath = g.relpath
    return groups


def is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def build_file_tree(groups: List[ChangeGroup]) -> dict:
    root = {"__folders__": {}, "__files__": []}
    for g in groups:
        node = root
        for part in g.folders:
            node = node["__folders__"].setdefault(part, {"__folders__": {}, "__files__": []})
        node["__files__"].append(g)
    return root


_SCAN_IGNORED_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env',
                       'dist', 'build', '.idea', '.vscode', '.mypy_cache', '.pytest_cache'}


def scan_existing_project_files(out_root: Path, max_files: int = 3000) -> Dict[str, str]:
    """Recursively read every text file already inside out_root, so an
    existing project can be loaded and shown the moment its folder is
    chosen. Binary/unreadable files and common noisy directories (.git,
    node_modules, __pycache__, venvs, build output, ...) are skipped."""
    result: Dict[str, str] = {}
    if not out_root.exists() or not out_root.is_dir():
        return result
    count = 0
    for root, dirs, files in os.walk(out_root):
        dirs[:] = [d for d in dirs if d not in _SCAN_IGNORED_DIRS and not d.startswith('.')]
        for fname in files:
            if fname.startswith('.'):
                continue
            fpath = Path(root) / fname
            try:
                relpath = str(fpath.relative_to(out_root)).replace('\\', '/')
            except ValueError:
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
            except Exception:
                continue  # binary or unreadable -- skip rather than guess
            result[relpath] = content
            count += 1
            if count >= max_files:
                return result
    return result


# ==========================================================================
# SYSTEM PROMPT GENERATOR
# ==========================================================================
def get_system_prompt(example_project: str = "MyProject") -> str:
    return f"""You are generating files for a desktop tool called "ChatForge".
Whenever you produce code or files for the user, format EVERY file using the
exact syntax below so the tool can extract it automatically.

============================================================
OUTPUT DISCIPLINE -- READ THIS FIRST
============================================================
When the user's request is about writing or fixing code/files for this tool,
your ENTIRE reply must contain ONLY the file blocks below (marker line +
^start^ ... ^end^), one after another, and nothing else:
- No introductory sentence ("Sure, here's the fix:").
- No summary or explanation before, between, or after the blocks.
- No commentary about what you changed, why, or what to do next.
- If you genuinely need to say something to the user, say it in a separate
  message -- never mixed into the same reply as file blocks.
This isn't about tidiness: the tool only looks for the exact patterns below,
but keeping your reply to just those patterns removes any chance that a
stray "$" or "#" in your own prose gets confused for a real marker.

Inside the actual file contents (between ^start^ and ^end^), write totally
normal code. Dollar signs, backslashes, quotes, regex, shell variables,
Windows paths, f-strings, template literals -- all of it is fine exactly as
it would normally be written. NEVER escape, remove, or alter a character in
the code just because it resembles this tool's syntax ($, #, >>, <<). Those
characters only mean anything as this tool's metadata when they appear in
the exact marker patterns shown below, immediately outside of a code block --
never inside one.

============================================================
NEW FILE (goes in the project root)
============================================================
>>filename<</.extension/
^start^
...file contents...
^end^

Example:
>>main<</.py/
^start^
print("Hello")
^end^

This creates: main.py

============================================================
NEW FILE INSIDE FOLDERS
============================================================
Prefix with one "#" per folder level, each folder wrapped in # marks:
#folder#subfolder#>>filename<</.extension/

Example:
#src#utils#>>helper<</.py/
^start^
def helper():
    return 1
^end^

This creates: src/utils/helper.py

============================================================
PATCHING / FIXING AN EXISTING FILE
============================================================
Put a single "$" immediately before the file marker. This means "replace the
contents of this EXISTING file" -- it does not create a second file.

$>>filename<</.extension/
^start^
...the COMPLETE new contents of the file...
^end^

$#src#utils#>>helper<</.py/
^start^
def helper():
    return 2
^end^

The code between ^start^ and ^end^ for a patch is the full replacement
content of the file (not a diff), unless told otherwise.

A fix is not a special case -- it uses the exact same full structure as a
new file: the "$" prefix, the complete folder path if the file is nested,
and a full ^start^ / ^end^ block containing the file's complete new
contents. Never shorten or drop any part of this structure for a "small"
fix, and never reply with just the changed lines on their own -- always
the whole file, wrapped in the full marker.

============================================================
PROJECT ROOT NAME
============================================================
At the very end of your file output, once, include:

%{example_project}%

This tells the tool what to name the project's root folder. Only include
this once you actually want to (re)name/confirm the project folder.

============================================================
EXTENSION RULES -- VERY IMPORTANT
============================================================
- The filename (between >> and <<) and the extension (between /. and /)
  are TWO SEPARATE pieces of information.
- Never include the extension twice. >>config<</.json/ must produce
  config.json -- NEVER config.json.json.
- The extension comes ONLY from the /.extension/ part of the marker.
  Never infer it from a markdown code-fence language tag, from the file's
  contents, or from your own explanation text.
- A markdown language identifier like ```json or ```python is only for
  syntax highlighting. It is NEVER part of the filename or extension.

Example -- this must create config.json, NOT config.json.json:

    ```json
    >>config<</.json/
    ^start^
    {{}}
    ^end^
    ```

============================================================
OTHER RULES
============================================================
- You may include as many files (and patches) as you want in one reply.
- Markdown fences (```) directly around a block are fine and are ignored
  by the parser -- but do not add any text alongside them.
- Never include the literal characters $, #, >>, <<, ^start^, ^end^, or a
  %...% root marker INSIDE the actual generated source code -- they are
  metadata for the tool, not file contents.
"""


# ==========================================================================
# SMALL DIALOGS
# ==========================================================================
class NewProjectDialog(tk.Toplevel):
    """Modal dialog to create a new project: name + optional output folder."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("New Project")
        self.configure(bg=Theme.bg_panel)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: Optional[Tuple[str, Optional[str]]] = None
        self.chosen_dir: Optional[str] = None

        pad = {"padx": 20, "pady": (14, 4)}
        tk.Label(self, text="Project name", font=Theme.ui_bold, bg=Theme.bg_panel,
                 fg=Theme.text).pack(anchor="w", **pad)
        self.name_var = tk.StringVar(value="New Project")
        name_entry = tk.Entry(self, textvariable=self.name_var, font=Theme.ui, bg=Theme.bg_input,
                               fg=Theme.text, insertbackground=Theme.accent, relief="flat",
                               highlightthickness=1, highlightbackground=Theme.border,
                               highlightcolor=Theme.accent, width=36)
        name_entry.pack(fill="x", padx=20)
        name_entry.select_range(0, "end")
        name_entry.focus_set()

        tk.Label(self, text="Output folder (optional -- can be set later)", font=Theme.ui_small,
                 bg=Theme.bg_panel, fg=Theme.text_dim).pack(anchor="w", padx=20, pady=(14, 4))
        row = tk.Frame(self, bg=Theme.bg_panel)
        row.pack(fill="x", padx=20)
        self.dir_label = tk.Label(row, text="Not set", font=Theme.ui_small, bg=Theme.bg_panel,
                                   fg=Theme.text_faint, anchor="w")
        self.dir_label.pack(side="left", fill="x", expand=True)
        RoundedButton(row, "Browse…", self._browse, panel_bg=Theme.bg_panel, small=True).pack(side="right")

        btn_row = tk.Frame(self, bg=Theme.bg_panel)
        btn_row.pack(fill="x", padx=20, pady=(20, 18))
        RoundedButton(btn_row, "Cancel", self._cancel, panel_bg=Theme.bg_panel).pack(side="right")
        RoundedButton(btn_row, "Create Project", self._create, panel_bg=Theme.bg_panel,
                      primary=True).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda e: self._create())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_window(self)

    def _browse(self):
        d = filedialog.askdirectory(title="Choose output folder", parent=self)
        if d:
            self.chosen_dir = d
            display = d if len(d) < 46 else "..." + d[-43:]
            self.dir_label.configure(text=display, fg=Theme.text)

    def _create(self):
        name = self.name_var.get().strip() or "New Project"
        self.result = (name, self.chosen_dir)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class SystemPromptDialog(tk.Toplevel):
    """Shows the generated AI system prompt with a Copy button."""

    def __init__(self, parent, example_project: str):
        super().__init__(parent)
        self.title("AI System Prompt")
        self.configure(bg=Theme.bg_panel)
        self.geometry("720x620")
        self.transient(parent)

        header = tk.Frame(self, bg=Theme.bg_panel)
        header.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(header, text="AI System Prompt", font=Theme.ui_title, bg=Theme.bg_panel,
                 fg=Theme.text).pack(anchor="w")
        tk.Label(header, text="Paste this into your AI's system prompt so it formats files the way "
                              "this app expects.", font=Theme.ui_small, bg=Theme.bg_panel,
                 fg=Theme.text_dim, wraplength=670, justify="left").pack(anchor="w", pady=(2, 0))

        body_wrap = RoundedFrame(self, panel_bg=Theme.bg_panel, card_bg=Theme.bg_input)
        body_wrap.pack(fill="both", expand=True, padx=20, pady=12)
        text = tk.Text(body_wrap.body, wrap="word", bg=Theme.bg_input, fg=Theme.text, font=Theme.mono_small,
                        relief="flat", padx=14, pady=14, highlightthickness=0)
        scroll = tk.Scrollbar(body_wrap.body, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.prompt_text = get_system_prompt(example_project)
        text.insert("1.0", self.prompt_text)
        text.configure(state="disabled")

        footer = tk.Frame(self, bg=Theme.bg_panel)
        footer.pack(fill="x", padx=20, pady=(0, 18))
        self.copied_label = tk.Label(footer, text="", font=Theme.ui_small, bg=Theme.bg_panel, fg=Theme.ok)
        self.copied_label.pack(side="left")
        RoundedButton(footer, "Close", self.destroy, panel_bg=Theme.bg_panel).pack(side="right")
        RoundedButton(footer, "Copy System Prompt", self._copy, panel_bg=Theme.bg_panel,
                      primary=True).pack(side="right", padx=(0, 8))

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.prompt_text)
        self.update()
        self.copied_label.configure(text="Copied to clipboard")
        self.after(2200, lambda: self.copied_label.configure(text=""))


# ==========================================================================
# ==========================================================================
# PROJECT STATE
# ==========================================================================
_id_counter = itertools.count(1)


@dataclass
class ProjectState:
    id: str
    name: str
    output_dir: Optional[str] = None
    root_folder: str = ""
    last_occurrences: List[RawChange] = field(default_factory=list)
    changes: List[ChangeGroup] = field(default_factory=list)
    raw_errors: List[str] = field(default_factory=list)
    existing_files: Dict[str, str] = field(default_factory=dict)   # relpath -> content, loaded from disk


# ==========================================================================
# PROJECT PANEL  (all UI for a single project/tab)
#
# Deliberately minimal: Paste, Analyze, Save, and a file tree/preview.
# Analyzing a conversation adds its files/patches to this project's pending
# change set and clears the textbox, ready for the next conversation to be
# pasted in (for more new files, or updates to files already in this
# project). Nothing touches disk until Save Files is clicked.
#
# Layout note: every direct child of `self` uses grid() with explicit row
# weights (toolbar / content / action bar). This guarantees the action bar
# -- which holds the Save button -- always keeps its full natural height,
# with no ambiguity about packing order.
# ==========================================================================
class ProjectPanel(tk.Frame):
    def __init__(self, master, app: "App", state: ProjectState):
        super().__init__(master, bg=Theme.bg)
        self.app = app
        self.state = state
        self._gen_queue: "queue.Queue" = queue.Queue()
        self._tree_item_to_entry: Dict[str, tuple] = {}   # iid -> (kind, relpath, payload)
        self._selected_entry: Optional[tuple] = None
        self._placeholder_active = True

        self._build()
        self.after(120, self._poll_queue)

    # ------------------------------------------------------------------
    def _section_label(self, parent, text, bg=None):
        return tk.Label(parent, text=text, font=Theme.ui_bold, bg=bg or Theme.bg_panel, fg=Theme.text)

    def _pill(self, parent, text, command, primary=False, small=False, panel_bg=Theme.bg_panel):
        return RoundedButton(parent, text, command, panel_bg=panel_bg, primary=primary, small=small)

    # ------------------------------------------------------------------
    def _build(self):
        self.grid_rowconfigure(0, weight=0)   # toolbar
        self.grid_rowconfigure(1, weight=1)   # conversation + file tree
        self.grid_rowconfigure(2, weight=0)   # action bar (Paste/Analyze/Save live here)
        self.grid_columnconfigure(0, weight=1)

        # ---- toolbar (rounded card, fixed height since it just holds one row) ----
        toolbar = RoundedFrame(self, panel_bg=Theme.bg, card_bg=Theme.bg_panel_alt)
        toolbar.configure(height=64)
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        tinner = tk.Frame(toolbar.body, bg=Theme.bg_panel_alt)
        tinner.pack(fill="both", expand=True, padx=10, pady=6)
        self._pill(tinner, "Choose Output Folder", self.on_choose_folder, small=True,
                   panel_bg=Theme.bg_panel_alt).pack(side="left")
        self.output_label = tk.Label(tinner, text="No output folder selected -- choose an empty folder for a "
                                                    "new project, or an existing project folder to load it",
                                      font=Theme.ui_small, bg=Theme.bg_panel_alt, fg=Theme.text_faint)
        self.output_label.pack(side="left", padx=(10, 0))

        # ---- main content: conversation (left) + file tree/preview (right) ----
        content = tk.Frame(self, bg=Theme.bg)
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1, uniform="cols")
        content.grid_columnconfigure(1, weight=1, uniform="cols")

        # LEFT: conversation
        left = RoundedFrame(content, panel_bg=Theme.bg, card_bg=Theme.bg_panel)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.body.grid_rowconfigure(1, weight=1)
        left.body.grid_columnconfigure(0, weight=1)

        left_header = tk.Frame(left.body, bg=Theme.bg_panel)
        left_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        self._section_label(left_header, "Conversation").pack(side="left")
        self._pill(left_header, "Paste", self.on_paste, small=True, panel_bg=Theme.bg_panel).pack(side="right")

        text_wrap = RoundedFrame(left.body, panel_bg=Theme.bg_panel, card_bg=Theme.bg_input, radius=8)
        text_wrap.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        text_wrap.body.grid_rowconfigure(0, weight=1)
        text_wrap.body.grid_columnconfigure(0, weight=1)
        self.input_text = tk.Text(text_wrap.body, wrap="word", bg=Theme.bg_input, fg=Theme.text_dim,
                                   insertbackground=Theme.accent, font=Theme.mono, relief="flat",
                                   padx=10, pady=10, undo=True, highlightthickness=0)
        self.input_text.grid(row=0, column=0, sticky="nsew")
        yscroll = tk.Scrollbar(text_wrap.body, command=self.input_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.input_text.configure(yscrollcommand=yscroll.set)

        self._placeholder = (
            "Paste an AI conversation here...\n\n"
            ">>main<</.py/\n^start^\nprint(\"hello\")\n^end^\n\n"
            "#src#utils#>>helper<</.py/   (nested folder)\n"
            "$>>main<</.py/   (patch = update an existing file)\n\n"
            "Click Analyze to add these to the project below. The box will "
            "clear automatically so you can paste the next conversation."
        )
        self._show_placeholder()
        self.input_text.bind("<FocusIn>", self._hide_placeholder_on_focus)

        if DND_AVAILABLE:
            self.input_text.drop_target_register(DND_FILES)
            self.input_text.dnd_bind('<<Drop>>', self._on_drop_files)

        # RIGHT: file tree + preview
        right = RoundedFrame(content, panel_bg=Theme.bg, card_bg=Theme.bg_panel)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.body.grid_rowconfigure(2, weight=1)
        right.body.grid_rowconfigure(4, weight=1)
        right.body.grid_columnconfigure(0, weight=1)

        right_header = tk.Frame(right.body, bg=Theme.bg_panel)
        right_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        self._section_label(right_header, "Project Files").pack(side="left")
        self.count_label = tk.Label(right_header, text="0 files", font=Theme.ui_small,
                                     bg=Theme.bg_panel, fg=Theme.text_dim)
        self.count_label.pack(side="right")

        legend = tk.Frame(right.body, bg=Theme.bg_panel)
        legend.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        for kind, label_text in [("new", "new"), ("patch", "patch"),
                                  ("existing", "existing"), ("duplicate", "duplicate")]:
            item = tk.Frame(legend, bg=Theme.bg_panel)
            item.pack(side="left", padx=(0, 14))
            tk.Label(item, image=self.app._icon_status[kind], bg=Theme.bg_panel).pack(side="left")
            tk.Label(item, text=f" {label_text}", font=Theme.ui_small, bg=Theme.bg_panel,
                     fg=Theme.text_faint).pack(side="left")

        tree_wrap = RoundedFrame(right.body, panel_bg=Theme.bg_panel, card_bg=Theme.bg_input, radius=8)
        tree_wrap.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 6))
        tree_wrap.body.grid_rowconfigure(0, weight=1)
        tree_wrap.body.grid_columnconfigure(0, weight=1)
        self.files_tree = ttk.Treeview(tree_wrap.body, show="tree", selectmode="browse", style="Files.Treeview")
        self.files_tree.grid(row=0, column=0, sticky="nsew")
        tscroll = tk.Scrollbar(tree_wrap.body, command=self.files_tree.yview)
        tscroll.grid(row=0, column=1, sticky="ns")
        self.files_tree.configure(yscrollcommand=tscroll.set)
        self.files_tree.tag_configure("striped", background=Theme.bg_panel_alt)
        self.files_tree.bind("<<TreeviewSelect>>", self._on_select_node)

        preview_header = tk.Frame(right.body, bg=Theme.bg_panel)
        preview_header.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.preview_title = tk.Label(preview_header, text="Preview", font=Theme.ui_bold,
                                       bg=Theme.bg_panel, fg=Theme.text)
        self.preview_title.pack(side="left")

        preview_wrap = RoundedFrame(right.body, panel_bg=Theme.bg_panel, card_bg=Theme.bg_input, radius=8)
        preview_wrap.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        preview_wrap.body.grid_rowconfigure(0, weight=1)
        preview_wrap.body.grid_columnconfigure(0, weight=1)
        self.preview_text = tk.Text(preview_wrap.body, wrap="none", bg=Theme.bg_input, fg=Theme.text,
                                     font=Theme.mono_small, relief="flat", padx=10, pady=10,
                                     state="disabled", highlightthickness=0)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        pscroll = tk.Scrollbar(preview_wrap.body, command=self.preview_text.yview)
        pscroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=pscroll.set)

        # ---- action bar: Analyze + Save, always visible ----
        action_bar = RoundedFrame(self, panel_bg=Theme.bg, card_bg=Theme.bg_panel_alt)
        action_bar.configure(height=150)
        action_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        ainner = tk.Frame(action_bar.body, bg=Theme.bg_panel_alt)
        ainner.pack(fill="both", expand=True, padx=10, pady=8)

        buttons_row = tk.Frame(ainner, bg=Theme.bg_panel_alt)
        buttons_row.pack(fill="x")
        self._pill(buttons_row, "Analyze Conversation", self.on_analyze,
                   panel_bg=Theme.bg_panel_alt).pack(side="left")
        self.save_btn = RoundedButton(buttons_row, "Save Files", self.on_save,
                                       panel_bg=Theme.bg_panel_alt, primary=True)
        self.save_btn.pack(side="right")

        self.progress = ttk.Progressbar(ainner, style="Horizontal.TProgressbar", mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

        self.status_label = tk.Label(ainner, text="Choose an output folder, paste a conversation, then Analyze.",
                                      font=Theme.ui_small, bg=Theme.bg_panel_alt, fg=Theme.text_dim,
                                      anchor="w", justify="left", wraplength=1000)
        self.status_label.pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------------
    # PLACEHOLDER
    # ------------------------------------------------------------------
    def _show_placeholder(self):
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", self._placeholder)
        self.input_text.configure(fg=Theme.text_dim)
        self._placeholder_active = True

    def _hide_placeholder_on_focus(self, event=None):
        if self._placeholder_active:
            self.input_text.delete("1.0", "end")
            self.input_text.configure(fg=Theme.text)
            self._placeholder_active = False

    def _get_conversation_text(self) -> str:
        if self._placeholder_active:
            return ""
        return self.input_text.get("1.0", "end-1c")

    def _flash_status(self, text: str, color: str):
        """Inline, non-interrupting status feedback with a brief highlight
        flash, used instead of a modal popup for routine/expected messages
        (errors that need explicit attention still use a real dialog)."""
        self.status_label.configure(text=text, fg=color, bg=Theme.select)
        self.after(260, lambda: self.status_label.configure(bg=Theme.bg_panel_alt))

    def _on_drop_files(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        for p in paths:
            p = p.strip('{}')
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self._hide_placeholder_on_focus()
                    self.input_text.delete("1.0", "end")
                    self.input_text.insert("1.0", content)
                    self.input_text.configure(fg=Theme.text)
                except Exception as e:
                    messagebox.showerror("Could not read file", str(e))
                break

    # ------------------------------------------------------------------
    # TOOLBAR ACTIONS
    # ------------------------------------------------------------------
    def on_paste(self):
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            self._flash_status("Clipboard is empty -- copy some text first, then click Paste.", Theme.warn)
            return
        self._hide_placeholder_on_focus()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", clip)
        self.input_text.configure(fg=Theme.text)

    def on_choose_folder(self):
        try:
            folder = filedialog.askdirectory(
                title="Choose a folder -- pick an existing project folder to load it, or an empty one to start new")
            if not folder:
                return
            self.state.output_dir = folder
            display = folder if len(folder) < 55 else "..." + folder[-52:]
            self.output_label.configure(text=display, fg=Theme.text)
            self.status_label.configure(text=f"Scanning {folder} ...", fg=Theme.text_dim)
            threading.Thread(target=self._scan_worker, args=(Path(folder),), daemon=True).start()
        except Exception as e:
            messagebox.showerror("Error choosing folder", str(e))

    def _scan_worker(self, out_root: Path):
        try:
            existing = scan_existing_project_files(out_root)
            self._gen_queue.put(("scan_done", existing, str(out_root), None))
        except Exception as e:
            self._gen_queue.put(("scan_done", {}, str(out_root), str(e)))

    # ------------------------------------------------------------------
    # ANALYZE  (adds to the project's pending changes, then clears the box)
    # ------------------------------------------------------------------
    def on_analyze(self):
        try:
            text = self._get_conversation_text()
            if not text.strip():
                self._flash_status("Paste a conversation first, then click Analyze.", Theme.warn)
                return

            occurrences, root_name, errors = parse_conversation(text)
            offset = len(self.state.last_occurrences)
            for occ in occurrences:
                occ.index += offset
            self.state.last_occurrences.extend(occurrences)
            self.state.raw_errors = errors
            if root_name:
                self.state.root_folder = root_name  # informational only; files still save directly into the chosen folder

            out_root = self._effective_out_root()
            self.state.changes = group_changes(self.state.last_occurrences, out_root, "replace")
            self._refresh_tree()

            # Ready for the next conversation to be pasted in.
            self._show_placeholder()

            n_new = sum(1 for g in self.state.changes if not g.effective_is_patch)
            n_patch = sum(1 for g in self.state.changes if g.effective_is_patch)
            if not occurrences:
                self.status_label.configure(text="No files detected in that conversation. Paste another one.",
                                             fg=Theme.warn)
            else:
                self.status_label.configure(
                    text=f"Added to project: {n_new} new file(s), {n_patch} patch(es) pending "
                         f"({len(self.state.changes)} total). Paste more, or click Save Files.",
                    fg=Theme.ok)
        except Exception as e:
            messagebox.showerror("Error analyzing conversation", f"{type(e).__name__}: {e}")

    def _effective_out_root(self) -> Optional[Path]:
        # The chosen folder IS the project -- files load from and save
        # directly into it, no extra nested subfolder.
        return Path(self.state.output_dir) if self.state.output_dir else None

    # ------------------------------------------------------------------
    # TREE / PREVIEW
    # ------------------------------------------------------------------
    def _refresh_tree(self):
        self.files_tree.delete(*self.files_tree.get_children())
        self._tree_item_to_entry = {}

        # Merge the on-disk baseline (already-existing files, loaded when
        # the folder was chosen) with any pending, not-yet-saved changes.
        # A pending change for a path overrides the baseline entry for it.
        entries: Dict[str, tuple] = {}
        for relpath, content in self.state.existing_files.items():
            entries[relpath] = ("existing", content)
        for g in self.state.changes:
            kind = "patch" if g.effective_is_patch else ("duplicate" if g.is_duplicate_new else "new")
            entries[g.relpath] = (kind, g)

        root_tree = {"__folders__": {}, "__files__": []}
        for relpath, (kind, payload) in entries.items():
            parts = relpath.split('/')
            folders, filename_full = parts[:-1], parts[-1]
            node = root_tree
            for part in folders:
                node = node["__folders__"].setdefault(part, {"__folders__": {}, "__files__": []})
            node["__files__"].append((filename_full, relpath, kind, payload))

        root_name = Path(self.state.output_dir).name if self.state.output_dir else "(no folder selected)"
        root_id = self.files_tree.insert("", "end", text=root_name, image=self.app._icon_folder, open=True)
        self._tree_row_index = 0
        self._populate_tree(root_id, root_tree)

        n = len(entries)
        self.count_label.configure(text=f"{n} file{'s' if n != 1 else ''}")
        self._selected_entry = None
        self._update_preview()

    def _populate_tree(self, parent_id, node):
        status_icons = self.app._icon_status
        for folder_name in sorted(node["__folders__"].keys(), key=str.lower):
            stripe = ("striped",) if self._tree_row_index % 2 else ()
            self._tree_row_index += 1
            fid = self.files_tree.insert(parent_id, "end", text=folder_name, image=self.app._icon_folder,
                                          open=True, tags=stripe)
            self._populate_tree(fid, node["__folders__"][folder_name])
        for filename_full, relpath, kind, payload in sorted(node["__files__"], key=lambda x: x[0].lower()):
            stripe = ("striped",) if self._tree_row_index % 2 else ()
            self._tree_row_index += 1
            ext = filename_full.rsplit('.', 1)[-1] if '.' in filename_full else ''
            logo = self.app.logos.icon_for(ext, kind)
            if logo is not None:
                iid = self.files_tree.insert(parent_id, "end", text=filename_full, image=logo, tags=stripe)
            else:
                color = language_color(ext)
                abbr = language_abbr(ext)
                lang_tag = f"lang_{ext.lower() or 'none'}"
                self.files_tree.tag_configure(lang_tag, foreground=color)
                display_text = f"{abbr:<5} {filename_full}"
                iid = self.files_tree.insert(parent_id, "end", text=display_text, image=status_icons[kind],
                                              tags=(lang_tag,) + stripe)
            self._tree_item_to_entry[iid] = (kind, relpath, payload)

    def _on_select_node(self, event=None):
        sel = self.files_tree.selection()
        self._selected_entry = self._tree_item_to_entry.get(sel[0]) if sel else None
        self._update_preview()

    def _update_preview(self):
        entry = self._selected_entry
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        if entry is None:
            self.preview_title.configure(text="Preview")
            self.preview_text.insert("1.0", "Select a file in the tree to preview it.")
        else:
            kind, relpath, payload = entry
            self.preview_title.configure(text=f"Preview — {relpath}")
            content = payload if kind == "existing" else payload.preview_after
            self.preview_text.insert("1.0", content if content else "(empty file)")
        self.preview_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # SAVE
    # ------------------------------------------------------------------
    def on_save(self):
        try:
            if not self.state.output_dir:
                self._flash_status("Choose an output folder first, then Save Files.", Theme.warn)
                return
            if not self.state.last_occurrences:
                self._flash_status("Paste a conversation and click Analyze first.", Theme.warn)
                return

            out_root = Path(self.state.output_dir)
            try:
                out_root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Cannot use output folder", f"Could not create/access the output folder:\n{e}")
                return

            # Re-group fresh against the real disk state right before writing.
            fresh_groups = group_changes(self.state.last_occurrences, out_root, "replace")
            plan = build_write_plan(fresh_groups, create_missing_patch_targets=True)
            to_write = [g for g in plan if g.action == "write"]

            if not to_write:
                self._flash_status("Nothing to save -- there are no files to write.", Theme.warn)
                return

            self.save_btn.set_enabled(False)
            self.progress.configure(maximum=len(to_write), value=0)
            self.status_label.configure(text=f"Saving {len(to_write)} file(s) to {out_root} ...", fg=Theme.text_dim)

            thread = threading.Thread(target=self._save_worker, args=(out_root, to_write, plan), daemon=True)
            thread.start()
        except Exception as e:
            messagebox.showerror("Error saving files", f"{type(e).__name__}: {e}")

    def _save_worker(self, out_root: Path, to_write: List[ChangeGroup], full_plan: List[ChangeGroup]):
        written_new, written_patch, failed = [], [], []
        for i, g in enumerate(to_write):
            try:
                if not g.final_relpath or g.final_relpath.startswith('/') or ':' in g.final_relpath:
                    raise ValueError("path is not a valid relative path")
                if '..' in Path(g.final_relpath).parts:
                    raise ValueError("path contains '..'")
                double_ext = f".{g.ext}.{g.ext}"
                if g.final_relpath.lower().endswith(double_ext.lower()):
                    raise ValueError(f"extension '.{g.ext}' would be duplicated")

                target = out_root / g.final_relpath
                if not is_path_inside(target, out_root):
                    raise ValueError("resolved path escapes the selected output directory")

                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    f.write(g.preview_after)

                if g.effective_is_patch:
                    written_patch.append(g.final_relpath)
                else:
                    written_new.append(g.final_relpath)
            except Exception as ex:
                failed.append((g.final_relpath or g.relpath, str(ex)))
            self._gen_queue.put(("progress", i + 1, len(to_write)))
        self._gen_queue.put(("done", written_new, written_patch, failed, full_plan, str(out_root)))

    _SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def _poll_queue(self):
        try:
            while True:
                item = self._gen_queue.get_nowait()
                if item[0] == "progress":
                    _, done, total = item
                    self.progress.configure(value=done)
                    spinner = self._SPINNER_FRAMES[done % len(self._SPINNER_FRAMES)]
                    self.status_label.configure(text=f"{spinner}  Saving files... {done}/{total}", fg=Theme.text_dim)
                elif item[0] == "done":
                    _, written_new, written_patch, failed, full_plan, out_root = item
                    self._on_save_done(written_new, written_patch, failed, full_plan, out_root)
                elif item[0] == "scan_done":
                    _, existing, out_root, err = item
                    self._on_scan_done(existing, out_root, err)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _on_scan_done(self, existing: Dict[str, str], out_root: str, err: Optional[str]):
        self.state.existing_files = existing
        # if there are already pending changes (from an earlier Analyze),
        # re-check them against this (possibly new) folder's real disk state
        if self.state.last_occurrences:
            self.state.changes = group_changes(self.state.last_occurrences, Path(out_root), "replace")
        self._refresh_tree()

        if err:
            self.status_label.configure(text=f"Folder selected, but couldn't fully scan it: {err}", fg=Theme.warn)
        elif existing:
            self.status_label.configure(
                text=f"Loaded existing project: {len(existing)} file(s) found in {out_root}.", fg=Theme.ok)
        else:
            self.status_label.configure(
                text="Folder is empty -- ready to save a new project here.", fg=Theme.text_dim)

    def _on_save_done(self, written_new, written_patch, failed, full_plan, out_root):
        self.progress.configure(value=0)
        self.save_btn.set_enabled(True)

        written_paths = set(written_new) | set(written_patch)
        if written_paths:
            by_path = {g.final_relpath: g for g in full_plan if g.final_relpath in written_paths}
            for relpath in written_paths:
                g = by_path.get(relpath)
                if g is not None:
                    self.state.existing_files[relpath] = g.preview_after
            # These changes are now committed to disk (and folded into the
            # baseline above) -- clear the pending queue so the next Analyze
            # starts fresh instead of re-applying the same changes again.
            self.state.last_occurrences = []
            self.state.changes = []

        self._refresh_tree()

        if failed:
            self.status_label.configure(
                text=f"Saved with errors: {len(written_new)} created, {len(written_patch)} patched, "
                     f"{len(failed)} failed. " + "; ".join(f"{n} ({e})" for n, e in failed),
                fg=Theme.error)
            messagebox.showerror("Some files failed", "The following files could not be saved:\n\n" +
                                  "\n".join(f"- {n}: {e}" for n, e in failed))
        else:
            self._flash_status(
                f"Saved. Created {len(written_new)} file(s), patched {len(written_patch)} file(s) in {out_root}.",
                Theme.ok)

        if written_new or written_patch:
            self._open_folder(out_root)

    def _open_folder(self, path: str):
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception:
            pass



# ==========================================================================
# MAIN APPLICATION (tab strip + project management)
# ==========================================================================
class App(BaseTkClass):
    def __init__(self):
        super().__init__()
        self.title("ChatForge")
        # Fixed, non-resizable window sized to comfortably fit inside a
        # typical 1366x768 laptop screen (with room for the OS taskbar),
        # so every panel -- including the Save button -- is always fully
        # on-screen and never gets clipped or pushed off the edge.
        WIN_W, WIN_H = 1260, 700
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg=Theme.bg)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max((sw - WIN_W) // 2, 0)
        y = max((sh - WIN_H) // 2 - 20, 0)
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        self._icon_img = make_app_icon(48)
        self._icon_img_small = make_app_icon(22)
        try:
            self.iconphoto(True, self._icon_img)
        except Exception:
            pass

        # Real, procedurally-drawn icons (kept alive for the app's lifetime)
        # used throughout the project tree instead of emoji.
        self._icon_folder = make_folder_icon(16)
        self._icon_status = {
            "new": make_dot_icon(Theme.ok, 10),
            "patch": make_dot_icon(Theme.warn, 10),
            "existing": make_dot_icon(Theme.text_faint, 10),
            "duplicate": make_dot_icon(Theme.error, 10),
        }

        # Real language logos (loaded from ./logos, fetched in the background if missing)
        self.logos = LogoManager(self, size=16)
        self._logo_queue: "queue.Queue" = queue.Queue()
        self.logos.start_download(self._logo_queue)
        self.after(500, self._poll_logos)

        self.projects: Dict[str, ProjectState] = {}
        self.panels: Dict[str, ProjectPanel] = {}
        self.tab_badges: Dict[str, RoundedButton] = {}
        self.active_id: Optional[str] = None

        self._build_style()
        self._build_shell()

        self._create_project("Project 1")

    # ------------------------------------------------------------------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=Theme.bg)
        style.configure("TLabel", background=Theme.bg, foreground=Theme.text, font=Theme.ui)
        style.configure("TPanedwindow", background=Theme.bg)
        style.configure("Horizontal.TProgressbar", troughcolor=Theme.bg_input, background=Theme.accent,
                         bordercolor=Theme.bg, lightcolor=Theme.accent, darkcolor=Theme.accent)
        style.configure("TCombobox", fieldbackground=Theme.bg_input, background=Theme.bg_input,
                         foreground=Theme.text, arrowcolor=Theme.text)
        style.map("TCombobox", fieldbackground=[("readonly", Theme.bg_input)])
        style.configure("TCheckbutton", background=Theme.bg_panel_alt, foreground=Theme.text_dim,
                         font=Theme.ui_small)
        style.map("TCheckbutton", background=[("active", Theme.bg_panel_alt)])
        style.configure("Files.Treeview", background=Theme.bg_input, fieldbackground=Theme.bg_input,
                         foreground=Theme.text, font=Theme.mono_small, borderwidth=0, rowheight=24)
        style.map("Files.Treeview", background=[("selected", Theme.select)],
                  foreground=[("selected", Theme.accent)])
        style.layout("Files.Treeview", style.layout("Treeview"))

    def _build_shell(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)   # minimized project sidebar
        self.grid_columnconfigure(1, weight=1)   # main content

        # ---- minimized project sidebar (left rail) ----
        self.sidebar = tk.Frame(self, bg=Theme.bg_panel, width=Theme.rail_width,
                                 highlightthickness=1, highlightbackground=Theme.border)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        tk.Label(self.sidebar, image=self._icon_img_small, bg=Theme.bg_panel).pack(pady=(16, 14))
        self.badge_stack = tk.Frame(self.sidebar, bg=Theme.bg_panel)
        self.badge_stack.pack(fill="x")

        # ---- main content (header + active project) ----
        content = tk.Frame(self, bg=Theme.bg)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        header = tk.Frame(content, bg=Theme.bg)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        title_row = tk.Frame(header, bg=Theme.bg)
        title_row.pack(fill="x")
        name_wrap = tk.Frame(title_row, bg=Theme.bg)
        name_wrap.pack(side="left")
        tk.Label(name_wrap, image=self._icon_img_small, bg=Theme.bg).pack(side="left", padx=(0, 8))
        tk.Label(name_wrap, text="ChatForge", font=Theme.ui_title, bg=Theme.bg, fg=Theme.text).pack(side="left")
        RoundedButton(title_row, "AI System Prompt", self.on_show_system_prompt,
                      panel_bg=Theme.bg, small=True).pack(side="right")
        sub = "Paste a conversation, Analyze it, then Save Files."
        if not DND_AVAILABLE:
            sub += "   (drag & drop of .txt files is unavailable in this environment)"
        tk.Label(header, text=sub, font=Theme.ui_small, bg=Theme.bg, fg=Theme.text_dim).pack(anchor="w", pady=(2, 0))

        # ---- project container (stacked frames) ----
        self.project_container = tk.Frame(content, bg=Theme.bg)
        self.project_container.grid(row=1, column=0, sticky="nsew")
        self.project_container.grid_rowconfigure(0, weight=1)
        self.project_container.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # PROJECT MANAGEMENT
    # ------------------------------------------------------------------
    def _create_project(self, name: str, output_dir: Optional[str] = None):
        pid = f"p{next(_id_counter)}"
        try:
            parts = sanitize_path_parts(name)
        except ValueError:
            parts = []
        root_folder = '/'.join(parts) if parts else "project"

        state = ProjectState(id=pid, name=name, output_dir=output_dir, root_folder=root_folder)
        self.projects[pid] = state

        panel = ProjectPanel(self.project_container, self, state)
        panel.grid(row=0, column=0, sticky="nsew")
        self.panels[pid] = panel

        self._add_project_badge(pid, name)
        self._switch_to(pid)

    def _poll_logos(self):
        try:
            while True:
                msg = self._logo_queue.get_nowait()
                if msg[0] == "logos_ready":
                    self.logos.convert_pending()
                    self.logos._base.clear()
                    self.logos._composed.clear()
                    for panel in self.panels.values():
                        panel._refresh_tree()
        except queue.Empty:
            pass
        self.after(1000, self._poll_logos)

    def on_new_project(self):
        dlg = NewProjectDialog(self)
        if dlg.result:
            name, out_dir = dlg.result
            self._create_project(name, out_dir)

    @staticmethod
    def _initials(name: str) -> str:
        words = [w for w in re.split(r'\s+', name.strip()) if w]
        if len(words) >= 2:
            return (words[0][0] + words[1][0]).upper()
        if len(name.strip()) >= 2:
            return name.strip()[:2].upper()
        return (name.strip()[:1] or "P").upper()

    def _add_project_badge(self, pid: str, name: str):
        # remove the "+ new project" badge, if present, so it's always last
        plus = getattr(self, "_plus_badge", None)
        if plus:
            plus.destroy()

        badge = RoundedButton(self.badge_stack, self._initials(name), lambda pid=pid: self._switch_to(pid),
                               panel_bg=Theme.bg_panel, width=44, height=44, radius=Theme.radius,
                               font=Theme.ui_bold)
        badge.pack(pady=4)
        Tooltip(badge, lambda pid=pid: self.projects[pid].name if pid in self.projects else "")
        self.tab_badges[pid] = badge

        self._plus_badge = RoundedButton(self.badge_stack, "+", self.on_new_project,
                                          panel_bg=Theme.bg_panel, width=44, height=44,
                                          radius=Theme.radius, font=Theme.ui_bold)
        self._plus_badge.pack(pady=(10, 4))
        Tooltip(self._plus_badge, lambda: "New Project")

    def _switch_to(self, pid: str):
        if pid not in self.panels:
            return
        self.active_id = pid
        self.panels[pid].tkraise()
        for tpid, badge in self.tab_badges.items():
            if tpid == pid:
                badge._bg, badge._hover = Theme.accent, "#7ff0d8"
                badge._fg = "#0e1512"
            else:
                badge._bg, badge._hover = Theme.bg_input, Theme.select
                badge._fg = Theme.text
            badge._paint(badge._bg)

    def on_show_system_prompt(self):
        example = "MyProject"
        if self.active_id and self.active_id in self.projects:
            state = self.projects[self.active_id]
            example = (state.root_folder or state.name or "MyProject")
        SystemPromptDialog(self, example)


def main():
    try:
        app = App()
        app.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("ChatForge - Fatal Error", traceback.format_exc())
        except Exception:
            pass


if __name__ == "__main__":
    main()
