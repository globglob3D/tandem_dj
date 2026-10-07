"""
The look of the window: a dark, green-on-black terminal style.
"""

import ctypes
import sys
import tkinter
from tkinter import ttk

from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_SEARCHING,
    STATUS_WAITING,
)
from tandem_dj.workflow import LEVEL_ERROR, LEVEL_INFORMATION, LEVEL_SUCCESS, LEVEL_WARNING

BACKGROUND = "#030803"
SURFACE = "#071007"
SURFACE_RAISED = "#0c1c0c"
BORDER = "#145214"
TEXT = "#35e06a"
TEXT_DIM = "#1f7a3a"
TEXT_BRIGHT = "#b6ffc8"
SELECTION = "#11401c"
AMBER = "#ffc94a"
RED = "#ff5c5c"

FONT_FAMILY = "Consolas"
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
ROW_HEIGHT = 24

STATUS_NOT_FINISHED = "Not finished"
STATUS_COLORS = {
    STATUS_WAITING: TEXT_DIM,
    STATUS_SEARCHING: AMBER,
    STATUS_DOWNLOADING: TEXT_BRIGHT,
    STATUS_DOWNLOADED: TEXT,
    STATUS_ALREADY_DOWNLOADED: TEXT_DIM,
    STATUS_FAILED: RED,
    STATUS_NOT_FINISHED: RED,
}
LEVEL_COLORS = {LEVEL_INFORMATION: TEXT_DIM, LEVEL_SUCCESS: TEXT, LEVEL_WARNING: AMBER, LEVEL_ERROR: RED}

DARK_TITLE_BAR_ATTRIBUTE = 20


def apply_theme(window: tkinter.Misc) -> None:
    """
    Give a window and every ttk widget in it the dark terminal look.

    :param window: Main window or dialog to style
    """
    window.configure(background=BACKGROUND)
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(
        ".",
        background=BACKGROUND,
        foreground=TEXT,
        fieldbackground=SURFACE,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        troughcolor=SURFACE,
        focuscolor=TEXT_DIM,
        insertcolor=TEXT_BRIGHT,
        selectbackground=SELECTION,
        selectforeground=TEXT_BRIGHT,
        font=FONT,
    )
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Dim.TLabel", foreground=TEXT_DIM)
    style.configure("Heading.TLabel", foreground=TEXT_BRIGHT, font=FONT_BOLD)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TButton", background=SURFACE_RAISED, foreground=TEXT, padding=(10, 5), relief="flat")
    style.map(
        "TButton",
        background=[("disabled", BACKGROUND), ("pressed", SELECTION), ("active", SELECTION)],
        foreground=[("disabled", TEXT_DIM), ("active", TEXT_BRIGHT)],
        bordercolor=[("disabled", SURFACE_RAISED), ("active", TEXT)],
    )
    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT_BRIGHT, padding=4)
    for toggle_style in ("TCheckbutton", "TRadiobutton"):
        style.configure(
            toggle_style,
            background=BACKGROUND,
            foreground=TEXT,
            indicatorbackground=SURFACE,
            indicatorforeground=TEXT_BRIGHT,
            upperbordercolor=BORDER,
            lowerbordercolor=BORDER,
        )
        style.map(
            toggle_style,
            background=[("active", BACKGROUND)],
            foreground=[("active", TEXT_BRIGHT)],
            indicatorbackground=[("selected", SELECTION), ("pressed", SELECTION)],
        )
    style.configure(
        "Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=ROW_HEIGHT, relief="flat"
    )
    style.map("Treeview", background=[("selected", SELECTION)], foreground=[("selected", TEXT_BRIGHT)])
    style.configure(
        "Treeview.Heading", background=SURFACE_RAISED, foreground=TEXT_BRIGHT, font=FONT_BOLD, relief="flat"
    )
    style.map("Treeview.Heading", background=[("active", SELECTION)])
    style.configure("Horizontal.TProgressbar", background=TEXT, troughcolor=SURFACE, thickness=14)
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            f"{orientation}.TScrollbar",
            background=SURFACE_RAISED,
            troughcolor=BACKGROUND,
            arrowcolor=TEXT,
            relief="flat",
        )
        style.map(f"{orientation}.TScrollbar", background=[("active", SELECTION)])
    window.update_idletasks()
    darken_title_bar(window)
    window.after(100, darken_title_bar, window)


def style_text_box(text_box: tkinter.Text) -> None:
    """
    Give a plain text widget the dark terminal look, which ttk styles do not reach.

    :param text_box: Text widget to style
    """
    text_box.configure(
        background=SURFACE,
        foreground=TEXT_BRIGHT,
        insertbackground=TEXT_BRIGHT,
        selectbackground=SELECTION,
        selectforeground=TEXT_BRIGHT,
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=TEXT,
        padx=6,
        pady=4,
    )


def darken_title_bar(window: tkinter.Misc) -> None:
    """
    Ask Windows to draw the title bar of a window in dark mode. Does nothing on other systems.

    :param window: Window whose title bar to darken
    """
    if sys.platform != "win32":
        return
    try:
        window_handle = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            window_handle, DARK_TITLE_BAR_ATTRIBUTE, ctypes.byref(enabled), ctypes.sizeof(enabled)
        )
    except (AttributeError, OSError):
        return
