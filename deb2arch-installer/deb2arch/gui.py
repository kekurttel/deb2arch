#!/usr/bin/env python3
"""GTK user interface for deb2arch-installer."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import gi

from .converter import ConversionResult, DebPackageConverter, PackageMetadata
from .installer import PackageInstaller
from .utils import Deb2ArchError, format_dependency_list

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class Deb2ArchWindow(Gtk.Window):
    """Main application window for handling .deb conversion + installation."""

    def __init__(self, deb_path: Optional[Path], logger: Optional[logging.Logger] = None) -> None:
        super().__init__(title="deb2arch Installer")
        self.set_default_size(840, 560)
        self.set_border_width(14)

        self.logger = logger or logging.getLogger("deb2arch.gui")
        self.converter = DebPackageConverter(self.logger)
        self.installer = PackageInstaller(self.logger)

        self.deb_path: Optional[Path] = deb_path
        self.metadata: Optional[PackageMetadata] = None
        self._busy = False

        self._apply_dark_theme()
        self._build_ui()

        if self.deb_path:
            self._load_metadata_async(self.deb_path)
        else:
            self._prompt_for_file()

    def _apply_dark_theme(self) -> None:
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        css = b"""
        window {
            background: #11161c;
            color: #e8edf2;
        }
        label {
            color: #d7dee7;
        }
        .title {
            font-size: 20px;
            font-weight: 700;
            color: #f2f4f8;
        }
        button {
            background-image: none;
            background: #273241;
            color: #e8edf2;
            border: 1px solid #405066;
            border-radius: 6px;
            padding: 8px 12px;
        }
        button:hover {
            background: #2f3c4d;
        }
        textview, textview text {
            background: #0d1117;
            color: #d2d9e3;
        }
        entry {
            background: #0d1117;
            color: #d2d9e3;
            border: 1px solid #2d3948;
            border-radius: 4px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)

        title = Gtk.Label(label="deb2arch Installer")
        title.get_style_context().add_class("title")
        title.set_halign(Gtk.Align.START)
        root.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(
            label="Convert .deb or .tar.gz packages and install them with pacman on Arch Linux"
        )
        subtitle.set_halign(Gtk.Align.START)
        root.pack_start(subtitle, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        root.pack_start(grid, False, False, 0)

        self.path_entry = Gtk.Entry()
        self.path_entry.set_editable(False)
        self.path_entry.set_placeholder_text("No package file selected")

        self.pkg_value = Gtk.Label(label="-")
        self.pkg_value.set_xalign(0)
        self.version_value = Gtk.Label(label="-")
        self.version_value.set_xalign(0)
        self.arch_value = Gtk.Label(label="-")
        self.arch_value.set_xalign(0)
        self.dep_value = Gtk.Label(label="-")
        self.dep_value.set_xalign(0)
        self.dep_value.set_line_wrap(True)
        self.unmapped_dep_value = Gtk.Label(label="-")
        self.unmapped_dep_value.set_xalign(0)
        self.unmapped_dep_value.set_line_wrap(True)

        labels = [
            ("Selected package", self.path_entry),
            ("Package", self.pkg_value),
            ("Version", self.version_value),
            ("Architecture", self.arch_value),
            ("Mapped dependencies", self.dep_value),
            ("Unmapped dependencies", self.unmapped_dep_value),
        ]

        for row, (name, widget) in enumerate(labels):
            label = Gtk.Label(label=name)
            label.set_xalign(0)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(status_box, False, False, 0)

        self.spinner = Gtk.Spinner()
        status_box.pack_start(self.spinner, False, False, 0)

        self.status_label = Gtk.Label(label="Idle")
        self.status_label.set_xalign(0)
        status_box.pack_start(self.status_label, True, True, 0)

        log_label = Gtk.Label(label="Operation log")
        log_label.set_halign(Gtk.Align.START)
        root.pack_start(log_label, False, False, 0)

        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.log_view)
        root.pack_start(scroll, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(actions, False, False, 0)

        open_button = Gtk.Button(label="Open package")
        open_button.connect("clicked", self._on_open_clicked)
        actions.pack_start(open_button, False, False, 0)

        self.install_button = Gtk.Button(label="Convert and Install")
        self.install_button.connect("clicked", self._on_install_clicked)
        self.install_button.set_sensitive(False)
        actions.pack_start(self.install_button, False, False, 0)

        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", self._on_cancel_clicked)
        actions.pack_end(cancel_button, False, False, 0)

    def _on_open_clicked(self, _button: Gtk.Button) -> None:
        self._prompt_for_file()

    def _prompt_for_file(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Select package file",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN,
            Gtk.ResponseType.OK,
        )

        package_filter = Gtk.FileFilter()
        package_filter.set_name("Supported packages (*.deb, *.tar.gz, *.tgz)")
        package_filter.add_pattern("*.deb")
        package_filter.add_pattern("*.tar.gz")
        package_filter.add_pattern("*.tgz")
        dialog.add_filter(package_filter)

        response = dialog.run()
        selected = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()

        if selected:
            self._load_metadata_async(Path(selected))

    def _load_metadata_async(self, deb_path: Path) -> None:
        if self._busy:
            return

        self.deb_path = deb_path.expanduser().resolve()
        self.path_entry.set_text(str(self.deb_path))
        self._append_log(f"Loaded file: {self.deb_path}")
        self._set_busy(True, "Reading package metadata...")

        def worker() -> None:
            try:
                metadata = self.converter.inspect_metadata(self.deb_path, log_callback=self._log_from_worker)
                GLib.idle_add(self._set_metadata, metadata)
                GLib.idle_add(self._append_log, "Metadata parsed successfully")
                GLib.idle_add(self.install_button.set_sensitive, True)
            except Exception as exc:
                GLib.idle_add(self.install_button.set_sensitive, False)
                GLib.idle_add(self._show_error_dialog, "Invalid or unsupported package file", str(exc))
                GLib.idle_add(self._append_log, f"Metadata read failed: {exc}")
            finally:
                GLib.idle_add(self._set_busy, False, "Idle")

        threading.Thread(target=worker, daemon=True).start()

    def _set_metadata(self, metadata: PackageMetadata) -> None:
        self.metadata = metadata
        self.pkg_value.set_text(metadata.package)
        self.version_value.set_text(metadata.version)
        self.arch_value.set_text(metadata.architecture)
        self.dep_value.set_text(format_dependency_list(metadata.mapped_dependencies))
        self.unmapped_dep_value.set_text(format_dependency_list(metadata.unmapped_dependencies))

    def _on_install_clicked(self, _button: Gtk.Button) -> None:
        if self._busy or not self.deb_path or not self.metadata:
            return

        if not self._confirm_install(self.metadata):
            self._append_log("Installation cancelled by user")
            return

        self.install_button.set_sensitive(False)
        self._set_busy(True, "Converting package...")

        def worker() -> None:
            result: Optional[ConversionResult] = None
            try:
                result = self.converter.convert(
                    self.deb_path,
                    prefer_debtap=True,
                    log_callback=self._log_from_worker,
                )
                GLib.idle_add(
                    self._append_log,
                    f"Conversion backend: {'debtap' if result.used_debtap else 'manual'}",
                )

                GLib.idle_add(self._set_busy, True, "Installing package with pacman...")
                install_result = self.installer.install(result.package_path, log_callback=self._log_from_worker)

                if install_result.success:
                    GLib.idle_add(
                        self._show_info_dialog,
                        "Installation Complete",
                        f"{result.metadata.package} {result.metadata.version} installed successfully.",
                    )
                else:
                    GLib.idle_add(
                        self._show_error_dialog,
                        "Installation Failed",
                        install_result.message,
                    )
            except Deb2ArchError as exc:
                GLib.idle_add(self._show_error_dialog, "Operation Failed", str(exc))
                GLib.idle_add(self._append_log, f"Error: {exc}")
            except Exception as exc:  # pragma: no cover - defensive catch for GUI path
                GLib.idle_add(self._show_error_dialog, "Unexpected Error", str(exc))
                GLib.idle_add(self._append_log, f"Unexpected error: {exc}")
            finally:
                if result is not None:
                    self.converter.cleanup_workspace(result.temp_dir)
                    GLib.idle_add(self._append_log, f"Cleaned temp directory: {result.temp_dir}")

                GLib.idle_add(self._set_busy, False, "Idle")
                GLib.idle_add(self.install_button.set_sensitive, self.metadata is not None)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_install(self, metadata: PackageMetadata) -> bool:
        mapped = format_dependency_list(metadata.mapped_dependencies)
        unmapped = format_dependency_list(metadata.unmapped_dependencies)

        detail = (
            f"Package: {metadata.package}\n"
            f"Version: {metadata.version}\n"
            f"Mapped dependencies: {mapped}\n"
            f"Unmapped dependencies: {unmapped}\n\n"
            "Continue with conversion and installation?"
        )

        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Confirm installation",
        )
        dialog.format_secondary_text(detail)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _on_cancel_clicked(self, _button: Gtk.Button) -> None:
        if self._busy:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text="An operation is currently running",
            )
            dialog.format_secondary_text(
                "Closing now will not interrupt already started pacman processes. Exit anyway?"
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return

        Gtk.main_quit()

    def _show_error_dialog(self, title: str, details: str) -> None:
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(details)
        dialog.run()
        dialog.destroy()

    def _show_info_dialog(self, title: str, details: str) -> None:
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(details)
        dialog.run()
        dialog.destroy()

    def _append_log(self, line: str) -> None:
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, f"{line}\n")

        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

    def _log_from_worker(self, line: str) -> None:
        GLib.idle_add(self._append_log, line)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self.status_label.set_text(status)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()


def launch_gui(deb_path: Optional[Path], logger: Optional[logging.Logger] = None) -> int:
    """Launch GTK interface."""
    win = Deb2ArchWindow(deb_path=deb_path, logger=logger)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0
