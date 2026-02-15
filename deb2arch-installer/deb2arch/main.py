#!/usr/bin/env python3
"""Entry point for deb2arch-installer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .converter import DebPackageConverter
from .installer import PackageInstaller
from .utils import Deb2ArchError, format_dependency_list, setup_logging


def _print_metadata(converter: DebPackageConverter, input_path: Path) -> None:
    metadata = converter.inspect_metadata(input_path)
    print(f"Package: {metadata.package}")
    print(f"Version: {metadata.version}")
    print(f"Architecture: {metadata.architecture}")
    print(f"Maintainer: {metadata.maintainer}")
    print(f"Mapped dependencies: {format_dependency_list(metadata.mapped_dependencies)}")
    print(f"Unmapped dependencies: {format_dependency_list(metadata.unmapped_dependencies)}")


def run_cli(input_path: Path) -> int:
    """Run conversion and installation in terminal mode."""
    logger = setup_logging("deb2arch.cli")
    converter = DebPackageConverter(logger)
    installer = PackageInstaller(logger)

    result = None
    try:
        input_path = input_path.expanduser().resolve()

        print(f"Inspecting: {input_path}")
        _print_metadata(converter, input_path)

        proceed = input("Proceed with conversion? [y/N]: ").strip().lower()
        if proceed not in {"y", "yes"}:
            print("Cancelled.")
            return 1

        print("Converting package...")
        result = converter.convert(input_path, prefer_debtap=True, log_callback=lambda line: print(line))

        print(f"Generated package: {result.package_path}")
        print(f"Conversion backend: {'debtap' if result.used_debtap else 'manual fallback'}")

        proceed_install = input("Install generated package with pacman now? [y/N]: ").strip().lower()
        if proceed_install not in {"y", "yes"}:
            print("Conversion complete. Installation skipped.")
            return 0

        install_result = installer.install(result.package_path, log_callback=lambda line: print(line))
        if install_result.success:
            print("Installation completed successfully.")
            return 0

        print(f"Installation failed: {install_result.message}")
        return 2

    except Deb2ArchError as exc:
        logger.error("Operation failed: %s", exc)
        print(f"Error: {exc}")
        return 2
    finally:
        if result is not None:
            converter.cleanup_workspace(result.temp_dir)


def run_gui(input_path: Optional[Path]) -> int:
    """Run GTK mode."""
    logger = setup_logging("deb2arch.gui")
    try:
        from .gui import launch_gui
    except Exception as exc:  # pragma: no cover - runtime dependency branch
        print(f"GUI dependencies unavailable: {exc}", file=sys.stderr)
        if input_path is None:
            return 2
        print("Falling back to CLI mode.")
        return run_cli(input_path)

    return launch_gui(input_path, logger=logger)


def build_parser() -> argparse.ArgumentParser:
    """Build command-line parser."""
    parser = argparse.ArgumentParser(
        prog="deb2arch-installer",
        description="Convert .deb/.tar.gz packages into Arch packages and install them.",
    )
    parser.add_argument("input_file", nargs="?", help="Path to .deb, .tar.gz, or .tgz package")
    parser.add_argument("--cli", action="store_true", help="Force command-line mode")
    parser.add_argument("--no-gui", action="store_true", help="Alias for --cli")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Program entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input_file).expanduser() if args.input_file else None

    force_cli = bool(args.cli or args.no_gui)
    if force_cli:
        if input_path is None:
            parser.error("input_file is required in CLI mode")
        return run_cli(input_path)

    return run_gui(input_path)


if __name__ == "__main__":
    raise SystemExit(main())
