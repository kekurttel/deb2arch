İNSTALL THE APP FROM THE RELEASES TAB 
I ADDED ELECTRON APP DOWNLOAND SUPPORT AND MADE THE GUİ MORE MODERN THANK U FOR USİNG 



# deb2arch-installer

`deb2arch-installer` is a minimal desktop utility for Arch Linux and Arch-based distributions.
It registers as a handler for `.deb` files and can also process `.tar.gz`/`.tgz` application archives through a guided GTK interface with clear logs.

The tool also supports command-line usage:

```bash
deb2arch-installer /path/to/package.deb
deb2arch-installer /path/to/package.tar.gz
```

## Features

- GTK (PyGObject) desktop interface with a dark theme
- `.desktop` integration for `.deb` MIME and optional open-with support for tar archives
- CLI and GUI workflows
- Conversion strategy:
  - Primary: `debtap`
  - Fallback: safe manual extraction + generated `PKGBUILD` + `makepkg`
- Dependency parsing with Debian -> Arch mapping attempts
- Warning for unmapped dependencies before install
- Privilege escalation via `pkexec` or `sudo`
- Pacman output streamed to a live log panel
- Secure extraction to `/tmp/deb2arch-*` with path sanitization
- Automatic temporary workspace cleanup after install

## Screenshots

Placeholder (add real screenshots after running locally):

- `docs/screenshot-main-window.png`
- `docs/screenshot-install-log.png`

## Installation

### Option 1: install script (recommended)

```bash
git clone https://github.com/yourname/deb2arch-installer.git
cd deb2arch-installer
chmod +x install.sh
./install.sh
```

This script:

- installs required system dependencies with `pacman`
- installs Python requirements from `requirements.txt`
- deploys app files to `/opt/deb2arch-installer`
- installs launcher: `/usr/local/bin/deb2arch-installer`
- installs desktop entry and icon
- updates desktop and MIME databases
- registers `.deb` MIME handler for the current user

### Option 2: build as native Arch package

```bash
makepkg -si
```

### Uninstall

```bash
chmod +x uninstall.sh
./uninstall.sh
```

## Usage

### GUI (double-click flow)

1. Double-click a `.deb` file in your file manager, or open the app and select `.deb` / `.tar.gz` / `.tgz`.
2. `deb2arch-installer` opens and parses package metadata.
3. Review mapped/unmapped dependencies.
4. Click **Convert and Install**.
5. Confirm installation.
6. Watch conversion and `pacman` output in the log panel.

### CLI

```bash
deb2arch-installer /path/to/package.deb
deb2arch-installer /path/to/package.tar.gz
```

CLI mode shows metadata, prompts for conversion, then prompts for installation.

### Forcing CLI mode

```bash
deb2arch-installer --cli /path/to/package.deb
deb2arch-installer --cli /path/to/package.tgz
```

## How conversion works

1. Validate input archive (`.deb`, `.tar.gz`, `.tgz`).
2. For `.deb`: extract `control.tar.*` and `data.tar.*` into `/tmp/deb2arch-*`.
3. For `.deb`: parse control metadata and map dependencies to Arch names.
4. Conversion backend:
   - `.deb`: try `debtap` first, then fallback to generated `PKGBUILD`
   - `.tar.gz`/`.tgz`: safe extraction and repackaging under `/opt/<pkgname>`
5. Build `.pkg.tar.zst` via `makepkg`.
6. Install with `pacman -U` using `pkexec` or `sudo`.
7. Cleanup temporary workspace.

## Troubleshooting

### `pacman` database lock error

If logs show lock-related failures:

- close other package managers
- remove stale lock only if safe to do so:

```bash
sudo rm -f /var/lib/pacman/db.lck
```

### `debtap` fails

The app automatically falls back to manual conversion. You can still improve `debtap` reliability by ensuring its database is up to date:

```bash
sudo debtap -u
```

### Missing GTK / PyGObject

Install required packages:

```bash
sudo pacman -S --needed python python-gobject gtk3
```

### Dependency mismatch after conversion

Some Debian package names do not map directly to Arch package names. Install missing runtime dependencies manually and retry.

## Security notes

- The app does **not** execute Debian maintainer scripts (`preinst`, `postinst`, etc.).
- Archive extraction is restricted to a dedicated `/tmp/deb2arch-*` workspace.
- Tar path traversal is blocked; unsafe links/devices are skipped.
- Every privileged install action is explicit (`pkexec` or `sudo`) and logged.
- Temporary conversion artifacts are removed after completion.

## Limitations

- Automated Debian -> Arch dependency mapping is heuristic and incomplete.
- Complex Debian packages with distro-specific post-install logic may not behave correctly after conversion.
- Generic tarball apps may need manual launchers/desktop files depending on upstream layout.
- Fallback PKGBUILD wraps extracted payload and may require manual adjustment for advanced packages.
- The tool targets Arch Linux / Arch-based systems only.

## Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Keep code modular and typed where practical.
4. Test both GUI and CLI paths.
5. Open a pull request with clear reproduction/testing notes.

## License

MIT License. See `LICENSE`.
