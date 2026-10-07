"""
The look of the window: a dark, muted retro terminal style, beige text on warm black with a green accent.
"""

import ctypes
import sys
import tkinter
from tkinter import ttk

from tandem_dj.paths import asset_path
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_SEARCHING,
    STATUS_WAITING,
)
from tandem_dj.workflow import LEVEL_ERROR, LEVEL_INFORMATION, LEVEL_SUCCESS, LEVEL_WARNING

BACKGROUND = "#191713"
SURFACE = "#201d18"
SURFACE_RAISED = "#2b2721"
BORDER = "#4a4337"
TEXT = "#d5c8ab"
TEXT_DIM = "#8d8470"
TEXT_BRIGHT = "#f0e6ce"
ACCENT = "#9cb87c"
ACCENT_DIM = "#6f8859"
SELECTION = "#38442f"
AMBER = "#d9a54c"
RED = "#d07060"

FONT_FAMILY = {"win32": "Consolas", "darwin": "Menlo"}.get(sys.platform, "DejaVu Sans Mono")
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
ROW_HEIGHT = 24

STATUS_NOT_FINISHED = "Not finished"
STATUS_FOUND_RELAXED = "Downloaded - check"
STATUS_COLORS = {
    STATUS_WAITING: TEXT_DIM,
    STATUS_SEARCHING: AMBER,
    STATUS_DOWNLOADING: TEXT_BRIGHT,
    STATUS_DOWNLOADED: ACCENT,
    STATUS_FOUND_RELAXED: AMBER,
    STATUS_ALREADY_DOWNLOADED: ACCENT_DIM,
    STATUS_FAILED: RED,
    STATUS_NOT_FINISHED: RED,
}
LEVEL_COLORS = {LEVEL_INFORMATION: TEXT_DIM, LEVEL_SUCCESS: ACCENT, LEVEL_WARNING: AMBER, LEVEL_ERROR: RED}

DARK_TITLE_BAR_ATTRIBUTE = 20
TASKBAR_IDENTIFIER = "TandemDJ.Window"
WINDOWS_ICON_NAME = "icon.ico"
PICTURE_ICON_NAME = "icon.png"


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
        focuscolor=ACCENT_DIM,
        insertcolor=TEXT_BRIGHT,
        selectbackground=SELECTION,
        selectforeground=TEXT_BRIGHT,
        font=FONT,
    )
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Dim.TLabel", foreground=TEXT_DIM)
    style.configure("Heading.TLabel", foreground=ACCENT, font=FONT_BOLD)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TButton", background=SURFACE_RAISED, foreground=TEXT, padding=(10, 5), relief="flat")
    style.map(
        "TButton",
        background=[("disabled", BACKGROUND), ("pressed", SELECTION), ("active", SELECTION)],
        foreground=[("disabled", TEXT_DIM), ("active", TEXT_BRIGHT)],
        bordercolor=[("disabled", SURFACE_RAISED), ("active", ACCENT)],
    )
    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT_BRIGHT, padding=4)
    for toggle_style in ("TCheckbutton", "TRadiobutton"):
        style.configure(
            toggle_style,
            background=BACKGROUND,
            foreground=TEXT,
            indicatorbackground=SURFACE,
            indicatorforeground=ACCENT,
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
    style.configure(
        "Horizontal.TProgressbar",
        background=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        troughcolor=SURFACE,
        thickness=14,
    )
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            f"{orientation}.TScrollbar",
            background=SURFACE_RAISED,
            troughcolor=BACKGROUND,
            arrowcolor=TEXT_DIM,
            relief="flat",
        )
        style.map(f"{orientation}.TScrollbar", background=[("active", SELECTION)])
    window.update_idletasks()
    darken_title_bar(window)
    window.after(100, darken_title_bar, window)


def set_application_icon(window: tkinter.Tk) -> None:
    """
    Give the main window, its dialogs and its taskbar button the icon of the application.

    On Windows the process also gets its own taskbar identity, without which the taskbar shows the icon of the
    Python program when running from source. A missing or unreadable icon leaves the default one.

    :param window: Main window of the application
    """
    try:
        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(TASKBAR_IDENTIFIER)
            window.iconbitmap(default=str(asset_path(WINDOWS_ICON_NAME)))
        else:
            window.icon_picture = tkinter.PhotoImage(master=window, file=str(asset_path(PICTURE_ICON_NAME)))
            window.iconphoto(True, window.icon_picture)
    except (tkinter.TclError, AttributeError, OSError):
        return


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
        highlightcolor=ACCENT_DIM,
        padx=6,
        pady=4,
    )


def style_menu(menu: tkinter.Menu) -> None:
    """
    Give a popup menu the dark terminal look, where the operating system lets menus be coloured.

    :param menu: Menu to style
    """
    menu.configure(
        background=SURFACE_RAISED,
        foreground=TEXT,
        activebackground=SELECTION,
        activeforeground=TEXT_BRIGHT,
        disabledforeground=TEXT_DIM,
        relief="flat",
        borderwidth=1,
        activeborderwidth=0,
        font=FONT,
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
