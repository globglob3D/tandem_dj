"""
The settings dialog: Soulseek account, folders, quality preferences, VPN and conversion.
"""

import dataclasses
import tkinter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tandem_dj.config import Settings, save_settings
from tandem_dj.ui import theme

DIALOG_TITLE = "tandem_dj settings"


class SettingsDialog(tkinter.Toplevel):
    """
    Modal dialog editing the settings file.

    After the dialog closes, :attr:`saved_settings` holds the new settings, or ``None`` when it was cancelled.

    :param parent: Window the dialog belongs to
    :param settings: Settings shown when the dialog opens
    :param config_path: Settings file to write, ``None`` for ``config.toml`` at the repository root
    """

    def __init__(self, parent: tkinter.Misc, settings: Settings, config_path: Path | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.config_path = config_path
        self.saved_settings: Settings | None = None

        self.title(DIALOG_TITLE)
        self.resizable(False, False)
        self.transient(parent)
        theme.apply_theme(self)

        self.username = tkinter.StringVar(self, settings.soulseek_username)
        self.password = tkinter.StringVar(self, settings.soulseek_password)
        self.output_directory = tkinter.StringVar(self, str(settings.output_directory))
        self.preferred_formats = tkinter.StringVar(self, ", ".join(settings.preferred_formats))
        self.preferred_minimum_bitrate = tkinter.StringVar(self, str(settings.preferred_minimum_bitrate))
        self.name_format = tkinter.StringVar(self, settings.name_format)
        self.extra_arguments = tkinter.StringVar(self, " ".join(settings.extra_arguments))
        self.vpn_required = tkinter.BooleanVar(self, settings.vpn_required)
        self.piactl_executable = tkinter.StringVar(self, str(settings.piactl_executable))
        self.convert_lossless_to_mp3 = tkinter.BooleanVar(self, settings.convert_lossless_to_mp3)
        self.mp3_bitrate = tkinter.StringVar(self, str(settings.mp3_bitrate))
        self.ffmpeg_executable = tkinter.StringVar(self, settings.ffmpeg_executable)

        form = ttk.Frame(self, padding=14)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        self.next_row = 0
        self._add_heading(form, "Soulseek account")
        self._add_entry(form, "Username", self.username)
        self._add_entry(form, "Password", self.password, hidden=True)
        self._add_heading(form, "Downloads")
        self._add_entry(form, "Download folder", self.output_directory, browse=self._browse_output_directory)
        self._add_entry(form, "Preferred formats", self.preferred_formats, hint="comma separated, best first")
        self._add_entry(form, "Preferred bitrate (kbps)", self.preferred_minimum_bitrate, hint="lower is a fallback")
        self._add_entry(form, "File naming", self.name_format, hint="sockseek --name-format")
        self._add_entry(form, "Extra sockseek flags", self.extra_arguments, hint="space separated, optional")
        self._add_heading(form, "VPN (Private Internet Access)")
        self._add_checkbox(
            form, "Only download while the VPN is connected (turned on and off automatically)", self.vpn_required
        )
        self._add_entry(form, "piactl program", self.piactl_executable, browse=self._browse_piactl)
        self._add_heading(form, "Conversion")
        self._add_checkbox(
            form, "Convert FLAC, WAV and AIFF downloads to MP3 and delete the original", self.convert_lossless_to_mp3
        )
        self._add_entry(form, "MP3 bitrate (kbps)", self.mp3_bitrate)
        self._add_entry(form, "ffmpeg program", self.ffmpeg_executable, hint="name on the PATH, or full path")

        buttons = ttk.Frame(self, padding=(14, 0, 14, 14))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Save", command=self._on_save).pack(side="right")
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()

    def _add_heading(self, form: ttk.Frame, text: str) -> None:
        """
        Add a section title to the form.

        :param form: Frame holding the form
        :param text: Title of the section
        """
        top_padding = 12 if self.next_row else 0
        ttk.Label(form, text=text, style="Heading.TLabel").grid(
            row=self.next_row, column=0, columnspan=3, sticky="w", pady=(top_padding, 4)
        )
        self.next_row += 1

    def _add_entry(
        self,
        form: ttk.Frame,
        label: str,
        variable: tkinter.StringVar,
        hidden: bool = False,
        hint: str = "",
        browse: object = None,
    ) -> None:
        """
        Add a labelled text field to the form.

        :param form: Frame holding the form
        :param label: Label of the field
        :param variable: Variable holding the text of the field
        :param hidden: Whether the text is masked, as for a password
        :param hint: Short explanation shown after the field
        :param browse: Command of a browse button shown after the field, when the field is a path
        """
        ttk.Label(form, text=label).grid(row=self.next_row, column=0, sticky="w", padx=(0, 10), pady=2)
        ttk.Entry(form, textvariable=variable, width=62, show="*" if hidden else "").grid(
            row=self.next_row, column=1, sticky="ew", pady=2
        )
        if browse is not None:
            ttk.Button(form, text="Browse...", command=browse).grid(row=self.next_row, column=2, padx=(6, 0))
        elif hint:
            ttk.Label(form, text=hint, style="Dim.TLabel").grid(row=self.next_row, column=2, sticky="w", padx=(6, 0))
        self.next_row += 1

    def _add_checkbox(self, form: ttk.Frame, label: str, variable: tkinter.BooleanVar) -> None:
        """
        Add a checkbox spanning the form.

        :param form: Frame holding the form
        :param label: Text of the checkbox
        :param variable: Variable holding the state of the checkbox
        """
        ttk.Checkbutton(form, text=label, variable=variable).grid(
            row=self.next_row, column=0, columnspan=3, sticky="w", pady=2
        )
        self.next_row += 1

    def _browse_output_directory(self) -> None:
        """
        Let the user pick the download folder.
        """
        chosen_directory = filedialog.askdirectory(parent=self, initialdir=self.output_directory.get() or None)
        if chosen_directory:
            self.output_directory.set(chosen_directory)

    def _browse_piactl(self) -> None:
        """
        Let the user pick the Private Internet Access command line program.
        """
        chosen_file = filedialog.askopenfilename(
            parent=self, title="piactl.exe", filetypes=[("Programs", "*.exe"), ("All files", "*.*")]
        )
        if chosen_file:
            self.piactl_executable.set(chosen_file)

    def _on_save(self) -> None:
        """
        Check the form, write the settings file and close the dialog.
        """
        try:
            new_settings = self._collect()
        except ValueError as error:
            messagebox.showerror(DIALOG_TITLE, str(error), parent=self)
            return
        save_settings(new_settings, self.config_path)
        self.saved_settings = new_settings
        self.destroy()

    def _collect(self) -> Settings:
        """
        Build settings from the content of the form.

        :returns: The settings as edited
        :raises ValueError: If a required field is empty or a number is not a number
        """
        if not self.username.get().strip() or not self.password.get():
            raise ValueError("The Soulseek username and password are required.")
        if not self.output_directory.get().strip():
            raise ValueError("The download folder is required.")
        try:
            preferred_minimum_bitrate = int(self.preferred_minimum_bitrate.get())
            mp3_bitrate = int(self.mp3_bitrate.get())
        except ValueError as error:
            raise ValueError("Bitrates must be whole numbers, such as 320.") from error
        return dataclasses.replace(
            self.settings,
            soulseek_username=self.username.get().strip(),
            soulseek_password=self.password.get(),
            output_directory=Path(self.output_directory.get().strip()),
            preferred_formats=tuple(name.strip() for name in self.preferred_formats.get().split(",") if name.strip()),
            preferred_minimum_bitrate=preferred_minimum_bitrate,
            name_format=self.name_format.get().strip() or self.settings.name_format,
            extra_arguments=tuple(self.extra_arguments.get().split()),
            vpn_required=self.vpn_required.get(),
            piactl_executable=Path(self.piactl_executable.get().strip()),
            convert_lossless_to_mp3=self.convert_lossless_to_mp3.get(),
            mp3_bitrate=mp3_bitrate,
            ffmpeg_executable=self.ffmpeg_executable.get().strip() or "ffmpeg",
        )
