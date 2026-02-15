#!/usr/bin/env python3
"""Utility helpers for deb2arch-installer."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from tarfile import TarFile, TarInfo
from typing import Callable, Iterable, Optional

LogCallback = Optional[Callable[[str], None]]
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|[\(\)][0-9A-Za-z])")


class Deb2ArchError(Exception):
    """Base exception for all deb2arch-installer errors."""


class ValidationError(Deb2ArchError):
    """Raised when input validation fails."""


class CommandExecutionError(Deb2ArchError):
    """Raised when a subprocess returns a non-zero exit status."""


class ConversionError(Deb2ArchError):
    """Raised when package conversion fails."""


class InstallError(Deb2ArchError):
    """Raised when package installation fails."""


def setup_logging(name: str = "deb2arch", level: int = logging.INFO) -> logging.Logger:
    """Create and configure a logger with consistent formatting."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(stream_handler)
    return logger


def create_temp_dir(prefix: str = "deb2arch-") -> Path:
    """Create a temporary directory under /tmp for safe workspace operations."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def cleanup_dir(path: Path, logger: Optional[logging.Logger] = None) -> None:
    """Best-effort temporary directory cleanup."""
    try:
        shutil.rmtree(path, ignore_errors=False)
    except FileNotFoundError:
        return
    except Exception as exc:  # pragma: no cover - best effort cleanup
        if logger:
            logger.warning("Failed to cleanup %s: %s", path, exc)


def command_exists(binary: str) -> bool:
    """Return True if a binary is available in PATH."""
    return shutil.which(binary) is not None


def sanitize_package_name(name: str) -> str:
    """Convert a package name into an Arch-compatible package token."""
    cleaned = re.sub(r"[^a-zA-Z0-9@._+-]", "-", name).strip("-._").lower()
    return cleaned or "unknown-package"


def sanitize_pkgver(version: str) -> str:
    """Convert a version string into an Arch pkgver-safe value.

    Arch `pkgver` must not contain hyphens, colons, slashes, or spaces.
    """
    if not version:
        return "0"

    no_epoch = version.split(":", 1)[-1]
    cleaned = no_epoch.replace("~", ".")
    cleaned = cleaned.replace("-", ".")
    cleaned = re.sub(r"[^a-zA-Z0-9.+_]", ".", cleaned)
    cleaned = re.sub(r"\.+", ".", cleaned)
    cleaned = cleaned.strip(".")
    return cleaned or "0"


def parse_debian_depends(depends_field: str) -> list[str]:
    """Parse a Debian Depends field into a flat dependency list.

    Example input:
    "libc6 (>= 2.34), libgtk-3-0 | libgtk-3-1"
    """
    dependencies: list[str] = []
    if not depends_field:
        return dependencies

    for raw_item in depends_field.split(","):
        item = raw_item.strip()
        if not item:
            continue

        preferred_alt = item.split("|", 1)[0].strip()
        no_version = re.sub(r"\s*\(.*?\)", "", preferred_alt).strip()
        no_arch = no_version.split(":", 1)[0].strip()
        if no_arch:
            dependencies.append(no_arch)

    return dependencies


def _is_safe_tar_target(destination: Path, member_name: str) -> bool:
    """Check if a tar member path stays inside destination directory."""
    normalized_name = member_name.lstrip("/")
    target_path = (destination / normalized_name).resolve()
    destination_root = destination.resolve()
    return str(target_path).startswith(str(destination_root) + os.sep) or target_path == destination_root


def _extract_regular_file(
    tar: TarFile,
    member: TarInfo,
    destination: Path,
) -> None:
    """Extract a single regular file from a tar archive safely."""
    target = (destination / member.name.lstrip("/")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    extracted = tar.extractfile(member)
    if extracted is None:
        raise ConversionError(f"Unable to read tar member: {member.name}")

    with extracted, target.open("wb") as output:
        shutil.copyfileobj(extracted, output)

    mode = member.mode & 0o777
    if mode:
        target.chmod(mode)


def safe_extract_tar(
    tar: TarFile,
    destination: Path,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Safely extract a tar archive.

    Security constraints:
    - Prevent path traversal
    - Skip symlinks/hardlinks/devices/FIFOs
    - Extract only directories and regular files
    """
    destination.mkdir(parents=True, exist_ok=True)

    for member in tar.getmembers():
        if not _is_safe_tar_target(destination, member.name):
            raise ConversionError(f"Unsafe archive path detected: {member.name}")

        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            if logger:
                logger.warning("Skipping unsafe archive member: %s", member.name)
            continue

        target = (destination / member.name.lstrip("/")).resolve()

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            mode = member.mode & 0o777
            if mode:
                target.chmod(mode)
            continue

        if member.isfile():
            _extract_regular_file(tar, member, destination)
            continue

        if logger:
            logger.warning("Skipping unsupported archive member type: %s", member.name)


def strip_ansi_escapes(text: str) -> str:
    """Remove ANSI terminal escape codes from a log line."""
    return ANSI_ESCAPE_RE.sub("", text)


def run_command(
    cmd: list[str],
    logger: logging.Logger,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    log_callback: LogCallback = None,
    check: bool = True,
) -> tuple[int, list[str]]:
    """Run a command and stream combined stdout/stderr line-by-line."""
    logger.debug("Running command: %s", " ".join(cmd))

    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    process_env.setdefault("TERM", "xterm-256color")

    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=process_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines: list[str] = []
    assert process.stdout is not None

    for line in iter(process.stdout.readline, ""):
        raw = line.rstrip("\n")
        stripped = strip_ansi_escapes(raw).strip()
        output_lines.append(stripped)
        if stripped:
            logger.info(stripped)
            if log_callback:
                log_callback(stripped)

    process.wait()

    if check and process.returncode != 0:
        joined = "\n".join(output_lines)
        raise CommandExecutionError(
            f"Command failed with exit code {process.returncode}: {' '.join(cmd)}\n{joined}"
        )

    return process.returncode, output_lines


def format_dependency_list(items: Iterable[str]) -> str:
    """Format dependency names for readable display."""
    return ", ".join(sorted(set(items))) if items else "none"
