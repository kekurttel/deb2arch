#!/usr/bin/env python3
"""Installation backend for converted Arch packages."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import InstallError, command_exists, run_command


@dataclass
class InstallResult:
    """Structured result of a pacman installation attempt."""

    success: bool
    returncode: int
    message: str


class PackageInstaller:
    """Install built Arch packages with pacman, using elevated privileges."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("deb2arch.installer")

    def install(self, package_path: Path, log_callback=None) -> InstallResult:
        """Install an Arch package via pacman and stream logs.

        Uses current root privileges when available.
        Falls back to pkexec, then sudo.
        """
        package_path = package_path.expanduser().resolve()
        if not package_path.exists():
            raise InstallError(f"Package does not exist: {package_path}")

        base_cmd = ["pacman", "-U", "--needed", "--noconfirm", str(package_path)]

        command: list[str]
        if self._is_root():
            command = base_cmd
            helper = "root"
        elif command_exists("pkexec"):
            command = ["pkexec"] + base_cmd
            helper = "pkexec"
        elif command_exists("sudo"):
            command = ["sudo"] + base_cmd
            helper = "sudo"
        else:
            raise InstallError("Neither pkexec nor sudo is available for privilege escalation")

        if log_callback:
            log_callback(f"Installing with {helper}: {' '.join(command)}")

        returncode, output_lines = run_command(
            command,
            self.logger,
            log_callback=log_callback,
            check=False,
        )

        output = "\n".join(output_lines).lower()

        if returncode == 0:
            return InstallResult(True, returncode, "Installation completed successfully")

        if "unable to lock database" in output or "failed to init transaction" in output:
            return InstallResult(
                False,
                returncode,
                "Pacman database is locked. Close other package managers and retry.",
            )

        if (
            "target not found" in output
            or "could not satisfy dependencies" in output
            or "dependencies could not be resolved" in output
            or "bağımlılıklar sağlanamadı" in output
            or "bağımlılıkları" in output
        ):
            return InstallResult(
                False,
                returncode,
                "Dependency resolution failed during pacman installation.",
            )

        if "conflicting dependencies" in output or "breaks dependency" in output:
            return InstallResult(
                False,
                returncode,
                "Version or dependency conflict detected.",
            )

        if "conflicting files" in output or "exists in filesystem" in output:
            return InstallResult(
                False,
                returncode,
                "File conflict detected. Remove conflicting files or package and retry.",
            )

        if "already installed" in output:
            return InstallResult(
                False,
                returncode,
                "A matching package is already installed.",
            )

        if "invalid or corrupted package" in output:
            return InstallResult(
                False,
                returncode,
                "Generated package is invalid or corrupted.",
            )

        return InstallResult(False, returncode, "Pacman returned an error; review logs for details")

    def _is_root(self) -> bool:
        """Return True when running with root privileges."""
        try:
            import os

            return os.geteuid() == 0
        except AttributeError:
            return False
