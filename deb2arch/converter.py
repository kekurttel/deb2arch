#!/usr/bin/env python3
"""Conversion logic for turning .deb/.tar.gz inputs into Arch packages."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import (
    CommandExecutionError,
    ConversionError,
    cleanup_dir,
    command_exists,
    create_temp_dir,
    format_dependency_list,
    parse_debian_depends,
    run_command,
    safe_extract_tar,
    sanitize_package_name,
    sanitize_pkgver,
)

SUPPORTED_TARBALL_SUFFIXES = (".tar.gz", ".tgz")

DEBIAN_TO_ARCH_DEP_MAP = {
    "adduser": "shadow",
    "ca-certificates": "ca-certificates",
    "gcc-12-base": "gcc-libs",
    "libasound2": "alsa-lib",
    "libatk1.0-0": "atk",
    "libatk-bridge2.0-0": "at-spi2-core",
    "libbz2-1.0": "bzip2",
    "libc6": "glibc",
    "libcurl4": "curl",
    "libdbus-1-3": "dbus",
    "libexpat1": "expat",
    "libfontconfig1": "fontconfig",
    "libfreetype6": "freetype2",
    "libgcc-s1": "gcc-libs",
    "libgdk-pixbuf-2.0-0": "gdk-pixbuf2",
    "libglib2.0-0": "glib2",
    "libgtk-3-0": "gtk3",
    "libnss3": "nss",
    "libnspr4": "nspr",
    "libnotify4": "libnotify",
    "libpango-1.0-0": "pango",
    "libpulse0": "libpulse",
    "libssl3": "openssl",
    "libstdc++6": "gcc-libs",
    "libuuid1": "util-linux-libs",
    "libx11-6": "libx11",
    "libxcomposite1": "libxcomposite",
    "libxcursor1": "libxcursor",
    "libxdamage1": "libxdamage",
    "libxext6": "libxext",
    "libxfixes3": "libxfixes",
    "libxi6": "libxi",
    "libxrandr2": "libxrandr",
    "libxrender1": "libxrender",
    "libxtst6": "libxtst",
    "python3": "python",
    "python3-gi": "python-gobject",
    "xdg-utils": "xdg-utils",
    "zlib1g": "zlib",
}

PASSTHROUGH_DEPENDENCIES = {
    "bash",
    "coreutils",
    "curl",
    "dbus",
    "file",
    "findutils",
    "glib2",
    "grep",
    "gzip",
    "libx11",
    "openssl",
    "sed",
    "tar",
    "util-linux",
    "which",
    "xz",
    "zstd",
}


@dataclass
class PackageMetadata:
    """Parsed metadata extracted from source package input."""

    package: str
    version: str
    architecture: str
    description: str
    maintainer: str
    depends_raw: str
    dependencies: list[str]
    mapped_dependencies: list[str]
    unmapped_dependencies: list[str]
    source_path: Path
    source_format: str


@dataclass
class ConversionResult:
    """Result of conversion, including generated package and temp workspace."""

    metadata: PackageMetadata
    package_path: Path
    temp_dir: Path
    used_debtap: bool


class DebPackageConverter:
    """Convert source archives into installable Arch package artifacts."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("deb2arch.converter")

    def _normalize_archive_member_name(self, member_name: str) -> str:
        """Normalize archive member paths without stripping significant dots."""
        cleaned = member_name.lstrip("/")
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        return cleaned

    def _is_deb_file(self, input_path: Path) -> bool:
        return input_path.suffix.lower() == ".deb"

    def _is_tarball_file(self, input_path: Path) -> bool:
        lowered = input_path.name.lower()
        return lowered.endswith(SUPPORTED_TARBALL_SUFFIXES)

    def _detect_input_format(self, input_path: Path) -> str:
        if self._is_deb_file(input_path):
            return "deb"
        if self._is_tarball_file(input_path):
            return "tarball"
        raise ConversionError("Supported files are: .deb, .tar.gz, .tgz")

    def validate_input_file(self, input_path: Path) -> str:
        """Validate a candidate input archive and return its detected format."""
        if not input_path.exists() or not input_path.is_file():
            raise ConversionError(f"File does not exist: {input_path}")

        input_format = self._detect_input_format(input_path)

        if input_format == "deb":
            with input_path.open("rb") as deb_file:
                magic = deb_file.read(8)
            if magic != b"!<arch>\n":
                raise ConversionError("Invalid .deb archive (missing ar header)")
            return input_format

        if not tarfile.is_tarfile(str(input_path)):
            raise ConversionError("Invalid tarball archive")

        return input_format

    def inspect_metadata(self, input_path: Path, log_callback=None) -> PackageMetadata:
        """Extract metadata only, without building an Arch package."""
        input_path = input_path.expanduser().resolve()
        input_format = self.validate_input_file(input_path)

        if input_format == "tarball":
            return self._parse_tarball_metadata(input_path)

        temp_dir = create_temp_dir()
        try:
            control_archive, _ = self._extract_deb_members(input_path, temp_dir, log_callback)
            control_text = self._read_control_file(control_archive, temp_dir)
            return self._parse_control_metadata(control_text, input_path)
        finally:
            cleanup_dir(temp_dir, self.logger)

    def convert(
        self,
        input_path: Path,
        prefer_debtap: bool = True,
        log_callback=None,
    ) -> ConversionResult:
        """Convert a source archive to a .pkg.tar.zst package."""
        input_path = input_path.expanduser().resolve()
        input_format = self.validate_input_file(input_path)

        temp_dir = create_temp_dir()
        try:
            if input_format == "tarball":
                metadata = self._parse_tarball_metadata(input_path)
                package_path = self._convert_tarball(metadata, input_path, temp_dir, log_callback)
                return ConversionResult(
                    metadata=metadata,
                    package_path=package_path,
                    temp_dir=temp_dir,
                    used_debtap=False,
                )

            control_archive, data_archive = self._extract_deb_members(input_path, temp_dir, log_callback)
            control_text = self._read_control_file(control_archive, temp_dir)
            metadata = self._parse_control_metadata(control_text, input_path)

            package_path: Optional[Path] = None
            used_debtap = False

            if prefer_debtap and command_exists("debtap"):
                try:
                    package_path = self._convert_with_debtap(input_path, temp_dir, log_callback)
                    if self._debtap_output_is_usable(package_path, metadata, temp_dir, log_callback):
                        used_debtap = True
                        self.logger.info("Converted with debtap: %s", package_path)
                        if log_callback:
                            log_callback(f"Converted with debtap: {package_path}")
                    else:
                        package_path = None
                        used_debtap = False
                        if log_callback:
                            log_callback(
                                "debtap produced suspicious dependency metadata. "
                                "Falling back to manual conversion."
                            )
                except ConversionError as exc:
                    self.logger.warning("debtap conversion failed, falling back: %s", exc)
                    if log_callback:
                        log_callback("debtap conversion failed. Falling back to manual conversion.")

            if package_path is None:
                package_path = self._convert_manually_deb(metadata, data_archive, temp_dir, log_callback)
                if log_callback:
                    log_callback(f"Manual conversion completed: {package_path}")

            return ConversionResult(
                metadata=metadata,
                package_path=package_path,
                temp_dir=temp_dir,
                used_debtap=used_debtap,
            )
        except Exception:
            cleanup_dir(temp_dir, self.logger)
            raise

    def cleanup_workspace(self, workspace: Path) -> None:
        """Cleanup conversion workspace."""
        cleanup_dir(workspace, self.logger)

    def _extract_deb_members(self, deb_path: Path, temp_dir: Path, log_callback=None) -> tuple[Path, Path]:
        """Extract control and data members using ar."""
        _, listing = run_command(["ar", "t", str(deb_path)], self.logger, log_callback=log_callback)
        control_member = next((line.strip() for line in listing if line.startswith("control.tar")), None)
        data_member = next((line.strip() for line in listing if line.startswith("data.tar")), None)

        if not control_member or not data_member:
            raise ConversionError(".deb is missing control.tar or data.tar archive")

        run_command(["ar", "x", str(deb_path)], self.logger, cwd=temp_dir, log_callback=log_callback)

        control_archive = temp_dir / control_member
        data_archive = temp_dir / data_member

        if not control_archive.exists() or not data_archive.exists():
            raise ConversionError("Failed to extract .deb members")

        return control_archive, data_archive

    @contextmanager
    def _open_tar_archive(self, archive_path: Path, temp_dir: Path):
        """Open tar archives including .tar.zst by decompressing to temp when needed."""
        try:
            tar = tarfile.open(archive_path, mode="r:*")
            try:
                yield tar
            finally:
                tar.close()
            return
        except tarfile.ReadError:
            pass

        if archive_path.name.endswith(".tar.zst") or archive_path.suffix == ".zst":
            if not command_exists("zstd"):
                raise ConversionError("zstd is required to process .tar.zst archives")

            decompressed = temp_dir / f"{archive_path.name}.decompressed.tar"
            run_command(
                ["zstd", "-d", "-f", "-q", str(archive_path), "-o", str(decompressed)],
                self.logger,
            )

            tar = tarfile.open(decompressed, mode="r:")
            try:
                yield tar
            finally:
                tar.close()
                decompressed.unlink(missing_ok=True)
            return

        raise ConversionError(f"Unsupported tar format: {archive_path.name}")

    def _read_control_file(self, control_archive: Path, temp_dir: Path) -> str:
        """Read the Debian control file from control.tar.* archive."""
        with self._open_tar_archive(control_archive, temp_dir) as tar:
            control_member = None
            for member in tar.getmembers():
                cleaned = self._normalize_archive_member_name(member.name)
                if cleaned == "control" and member.isfile():
                    control_member = member
                    break

            if control_member is None:
                raise ConversionError("Could not locate control metadata in control.tar")

            extracted = tar.extractfile(control_member)
            if extracted is None:
                raise ConversionError("Failed to read control metadata file")

            return extracted.read().decode("utf-8", errors="replace")

    def _parse_control_metadata(self, control_text: str, source_path: Path) -> PackageMetadata:
        """Parse key fields from Debian control metadata."""
        fields: dict[str, str] = {}
        current_key: Optional[str] = None

        for line in control_text.splitlines():
            if not line.strip():
                continue

            if line[0].isspace() and current_key:
                fields[current_key] = f"{fields[current_key]} {line.strip()}".strip()
                continue

            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            normalized_key = key.strip().lower()
            fields[normalized_key] = value.strip()
            current_key = normalized_key

        package = sanitize_package_name(fields.get("package", source_path.stem))
        version = sanitize_pkgver(fields.get("version", "0"))
        architecture = self._map_architecture(fields.get("architecture", "any"))
        description = fields.get("description", "Converted Debian package")
        maintainer = fields.get("maintainer", "Unknown")
        depends_raw = fields.get("depends", "")
        dependencies = parse_debian_depends(depends_raw)
        mapped_dependencies, unmapped_dependencies = self._map_dependencies(dependencies)

        return PackageMetadata(
            package=package,
            version=version,
            architecture=architecture,
            description=description,
            maintainer=maintainer,
            depends_raw=depends_raw,
            dependencies=dependencies,
            mapped_dependencies=mapped_dependencies,
            unmapped_dependencies=unmapped_dependencies,
            source_path=source_path,
            source_format="deb",
        )

    def _strip_archive_suffix(self, filename: str) -> str:
        lowered = filename.lower()
        for suffix in SUPPORTED_TARBALL_SUFFIXES:
            if lowered.endswith(suffix):
                return filename[: -len(suffix)]
        return Path(filename).stem

    def _parse_tarball_metadata(self, source_path: Path) -> PackageMetadata:
        """Infer package metadata from a tar.gz/tgz filename."""
        base_name = self._strip_archive_suffix(source_path.name)
        tokens = [token for token in re.split(r"[-_]+", base_name) if token]

        version_index = next((idx for idx, token in enumerate(tokens) if any(char.isdigit() for char in token)), None)

        if version_index is None:
            package_name = sanitize_package_name(base_name)
            version_text = "1.0.0"
        else:
            package_name = sanitize_package_name("-".join(tokens[:version_index]) or base_name)
            version_text = sanitize_pkgver("-".join(tokens[version_index:]) or "1.0.0")

        lower_name = base_name.lower()
        if any(marker in lower_name for marker in ("x86_64", "amd64", "x64")):
            architecture = "x86_64"
        elif any(marker in lower_name for marker in ("aarch64", "arm64")):
            architecture = "aarch64"
        elif any(marker in lower_name for marker in ("i386", "i686")):
            architecture = "i686"
        else:
            architecture = "any"

        return PackageMetadata(
            package=package_name,
            version=version_text,
            architecture=architecture,
            description=f"Repackaged tarball application from {source_path.name}",
            maintainer="Unknown",
            depends_raw="",
            dependencies=[],
            mapped_dependencies=[],
            unmapped_dependencies=[],
            source_path=source_path,
            source_format="tarball",
        )

    def _map_architecture(self, deb_arch: str) -> str:
        mapping = {
            "all": "any",
            "amd64": "x86_64",
            "arm64": "aarch64",
            "armhf": "armv7h",
            "i386": "i686",
        }
        arch = deb_arch.strip().lower()
        return mapping.get(arch, arch if arch else "any")

    def _map_dependencies(self, dependencies: list[str]) -> tuple[list[str], list[str]]:
        mapped: list[str] = []
        unmapped: list[str] = []

        for dep in dependencies:
            key = dep.lower()
            if key in DEBIAN_TO_ARCH_DEP_MAP:
                mapped.append(DEBIAN_TO_ARCH_DEP_MAP[key])
                continue

            if key in PASSTHROUGH_DEPENDENCIES:
                mapped.append(key)
                continue

            soname_guess = re.sub(r"\d+$", "", key).rstrip("-")
            if soname_guess in PASSTHROUGH_DEPENDENCIES:
                mapped.append(soname_guess)
                continue

            unmapped.append(dep)

        mapped_unique = sorted(set(mapped))
        unmapped_unique = sorted(set(unmapped))
        return mapped_unique, unmapped_unique

    def _detect_debtap_command(self) -> list[str]:
        """Pick debtap flags dynamically to avoid interactive prompts when possible."""
        try:
            _, help_output = run_command(["debtap", "--help"], self.logger, check=False)
            help_text = "\n".join(help_output)
        except Exception:
            help_text = ""

        if "-Q" in help_text:
            return ["debtap", "-Q"]
        if "-q" in help_text:
            return ["debtap", "-q"]
        return ["debtap"]

    def _collect_pkg_artifacts(self, paths: list[Path]) -> list[Path]:
        artifacts: set[Path] = set()
        for base in paths:
            if not base.exists() or not base.is_dir():
                continue
            for candidate in base.glob("*.pkg.tar*"):
                if candidate.is_file():
                    artifacts.add(candidate.resolve())
        return sorted(artifacts, key=lambda item: item.stat().st_mtime, reverse=True)

    def _convert_with_debtap(self, deb_path: Path, temp_dir: Path, log_callback=None) -> Path:
        """Convert using debtap and return generated package path."""
        output_dir = temp_dir / "debtap-output"
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = self._detect_debtap_command() + ["-o", str(output_dir), str(deb_path)]
        env = {
            "EDITOR": "/usr/bin/true",
            "VISUAL": "/usr/bin/true",
            "NO_COLOR": "1",
        }

        candidate_dirs = [output_dir, temp_dir, deb_path.parent]
        before = set(self._collect_pkg_artifacts(candidate_dirs))
        started_at = time.time()

        try:
            run_command(cmd, self.logger, cwd=output_dir, env=env, log_callback=log_callback)
        except CommandExecutionError as exc:
            raise ConversionError(f"debtap failed: {exc}") from exc

        after = self._collect_pkg_artifacts(candidate_dirs)
        new_artifacts = [path for path in after if path not in before and path.stat().st_mtime >= started_at - 2]

        if new_artifacts:
            return new_artifacts[0]

        recent_after = [path for path in after if path.stat().st_mtime >= started_at - 2]
        if recent_after:
            return recent_after[0]

        raise ConversionError("debtap completed but no package artifact was produced")

    def _parse_pkginfo_dependencies(self, package_path: Path, temp_dir: Path) -> list[str]:
        """Read `depend = ...` entries from a generated Arch package `.PKGINFO`."""
        with self._open_tar_archive(package_path, temp_dir) as tar:
            pkginfo_member = None
            for member in tar.getmembers():
                cleaned = self._normalize_archive_member_name(member.name)
                if cleaned in {".PKGINFO", "PKGINFO"} and member.isfile():
                    pkginfo_member = member
                    break

            if pkginfo_member is None:
                return []

            extracted = tar.extractfile(pkginfo_member)
            if extracted is None:
                return []

            text = extracted.read().decode("utf-8", errors="replace")

        dependencies: list[str] = []
        for line in text.splitlines():
            if line.startswith("depend = "):
                dep = line.split("=", 1)[1].strip()
                if dep:
                    dependencies.append(dep)
        return dependencies

    def _dependency_base(self, dep: str) -> str:
        return re.split(r"[<>=]+", dep, maxsplit=1)[0].strip().lower()

    def _debtap_output_is_usable(
        self,
        package_path: Path,
        metadata: PackageMetadata,
        temp_dir: Path,
        log_callback=None,
    ) -> bool:
        """Heuristically validate debtap dependency metadata before install."""
        deps = self._parse_pkginfo_dependencies(package_path, temp_dir)
        if not deps:
            return True

        dep_bases = [self._dependency_base(dep) for dep in deps]
        expected = set(metadata.mapped_dependencies)
        overlap = expected.intersection(dep_bases)

        suspicious_dependencies: list[str] = []
        for dep, base in zip(deps, dep_bases):
            has_relation = any(op in dep for op in (">", "<", "="))
            if not base:
                suspicious_dependencies.append(dep)
                continue

            if len(base) <= 1 and has_relation:
                suspicious_dependencies.append(dep)
                continue

            if "." in base and re.search(r"\d+\.\d", base):
                suspicious_dependencies.append(dep)
                continue

            if base.startswith("lib") and re.search(r"\d+\.", base):
                suspicious_dependencies.append(dep)
                continue

        suspicious = len(suspicious_dependencies)

        if suspicious >= 4:
            if log_callback:
                preview = ", ".join(suspicious_dependencies[:6])
                log_callback(
                    "debtap dependency check failed: detected mangled dependency names "
                    f"({suspicious} suspicious): {preview}"
                )
            return False

        if expected and len(expected) >= 4 and len(overlap) < 2 and suspicious >= 2:
            if log_callback:
                log_callback(
                    "debtap dependency check failed: low overlap with expected mappings "
                    f"(overlap={len(overlap)}, suspicious={suspicious})."
                )
            return False

        if expected and (len(overlap) / max(len(expected), 1)) < 0.25 and suspicious >= 3:
            if log_callback:
                log_callback(
                    "debtap dependency check failed: dependency names look mangled "
                    f"(overlap={len(overlap)}, suspicious={suspicious})."
                )
            return False

        return True

    def _build_from_pkgroot(self, metadata: PackageMetadata, build_dir: Path, log_callback=None) -> Path:
        """Build an Arch package from a prepared pkgroot directory using makepkg."""
        pkgbuild_path = build_dir / "PKGBUILD"
        pkgbuild_path.write_text(self._render_pkgbuild(metadata), encoding="utf-8")

        try:
            run_command(
                ["makepkg", "--nodeps", "--force", "--clean"],
                self.logger,
                cwd=build_dir,
                log_callback=log_callback,
            )
        except CommandExecutionError as exc:
            raise ConversionError(f"makepkg failed during manual conversion: {exc}") from exc

        generated = sorted(build_dir.glob("*.pkg.tar*"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not generated:
            raise ConversionError("Manual conversion completed but no package was generated")

        return generated[0]

    def _convert_manually_deb(
        self,
        metadata: PackageMetadata,
        data_archive: Path,
        temp_dir: Path,
        log_callback=None,
    ) -> Path:
        """Manual fallback conversion for Debian packages."""
        manual_dir = temp_dir / "manual-build"
        pkgroot_dir = manual_dir / "pkgroot"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pkgroot_dir.mkdir(parents=True, exist_ok=True)

        if log_callback:
            log_callback("Running manual conversion fallback")
            log_callback(f"Mapped dependencies: {format_dependency_list(metadata.mapped_dependencies)}")
            if metadata.unmapped_dependencies:
                log_callback(
                    "Unmapped dependencies: "
                    f"{format_dependency_list(metadata.unmapped_dependencies)}"
                )

        with self._open_tar_archive(data_archive, temp_dir) as tar:
            safe_extract_tar(tar, pkgroot_dir, self.logger)

        return self._build_from_pkgroot(metadata, manual_dir, log_callback)

    def _copy_tree_contents(self, src: Path, dst: Path) -> None:
        """Copy direct children from src to dst, preserving metadata where possible."""
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    def _pick_primary_executable(self, install_root: Path, package_name: str) -> Optional[Path]:
        """Find a likely executable entrypoint inside extracted tarball payload."""
        candidates: list[Path] = []
        for path in install_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(install_root)
            if len(rel.parts) > 4:
                continue
            if os.access(path, os.X_OK):
                candidates.append(path)

        if not candidates:
            return None

        preferred_names = {
            package_name,
            package_name.replace("-", ""),
            package_name.replace("-", "_"),
        }

        for candidate in candidates:
            if candidate.name in preferred_names:
                return candidate

        for candidate in candidates:
            rel = candidate.relative_to(install_root)
            if len(rel.parts) == 1 and "." not in candidate.name:
                return candidate

        return candidates[0]

    def _convert_tarball(
        self,
        metadata: PackageMetadata,
        tarball_path: Path,
        temp_dir: Path,
        log_callback=None,
    ) -> Path:
        """Convert a .tar.gz/.tgz application archive into an Arch package."""
        if log_callback:
            log_callback("Running tarball conversion")

        extract_dir = temp_dir / "tarball-extract"
        build_dir = temp_dir / "tarball-build"
        pkgroot_dir = build_dir / "pkgroot"

        extract_dir.mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        pkgroot_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(tarball_path, mode="r:*") as tar:
                safe_extract_tar(tar, extract_dir, self.logger)
        except tarfile.TarError as exc:
            raise ConversionError(f"Failed to extract tarball archive: {exc}") from exc

        top_entries = sorted(extract_dir.iterdir(), key=lambda item: item.name)
        source_root = top_entries[0] if len(top_entries) == 1 and top_entries[0].is_dir() else extract_dir

        install_root = pkgroot_dir / "opt" / metadata.package
        self._copy_tree_contents(source_root, install_root)

        primary_exec = self._pick_primary_executable(install_root, metadata.package)
        if primary_exec is not None:
            rel_exec = primary_exec.relative_to(install_root)
            bin_dir = pkgroot_dir / "usr" / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            launcher = bin_dir / metadata.package
            launcher.unlink(missing_ok=True)
            launcher.symlink_to(Path("/opt") / metadata.package / rel_exec)
            if log_callback:
                log_callback(f"Created launcher: /usr/bin/{metadata.package}")

        return self._build_from_pkgroot(metadata, build_dir, log_callback)

    def _render_pkgbuild(self, metadata: PackageMetadata) -> str:
        """Create a PKGBUILD for wrapping prepared payload files."""

        def quoted(value: str) -> str:
            escaped = value.replace("'", "'\\''")
            return f"'{escaped}'"

        depends_entries = " ".join(quoted(dep) for dep in metadata.mapped_dependencies)
        depends_line = f"depends=({depends_entries})" if metadata.mapped_dependencies else "depends=()"

        arch_value = metadata.architecture if metadata.architecture else "any"
        if arch_value not in {"any", "x86_64", "aarch64", "armv7h", "i686"}:
            arch_value = "any"

        pkgdesc = metadata.description or "Converted package"
        if len(pkgdesc) > 120:
            pkgdesc = pkgdesc[:117] + "..."

        pkgbuild = f"""# Maintainer: deb2arch-installer
pkgname={quoted(metadata.package)}
pkgver={quoted(metadata.version)}
pkgrel=1
pkgdesc={quoted(pkgdesc)}
arch=({quoted(arch_value)})
url={quoted('https://example.invalid/deb2arch-installer')}
license=('custom')
{depends_line}
options=('!strip' '!debug')
source=()
sha256sums=()

package() {{
    cp -a "$startdir/pkgroot/." "$pkgdir/"
}}
"""
        return pkgbuild
