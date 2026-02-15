#!/usr/bin/env bash
set -euo pipefail

APP_NAME="deb2arch-installer"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="/opt/${APP_NAME}"
BIN_TARGET="/usr/bin/${APP_NAME}"
LEGACY_BIN_TARGET="/usr/local/bin/${APP_NAME}"
DESKTOP_TARGET="/usr/share/applications/${APP_NAME}.desktop"
ICON_TARGET="/usr/share/icons/hicolor/128x128/apps/${APP_NAME}.png"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

require_command pacman
require_command sudo

echo "Installing system dependencies with pacman..."
sudo pacman -S --needed --noconfirm \
    python \
    python-gobject \
    gtk3 \
    python-pip \
    debtap \
    binutils \
    tar \
    zstd \
    base-devel \
    sudo \
    polkit \
    desktop-file-utils \
    shared-mime-info \
    xdg-utils

if command -v debtap >/dev/null 2>&1; then
    echo "Initializing debtap database (one-time update, can take a while)..."
    if ! sudo debtap -u; then
        echo "Warning: debtap database update failed."
        echo "The application can still run using the manual conversion fallback."
    fi
fi

echo "Installing Python requirements..."
if grep -Eq '^[[:space:]]*[^#[:space:]]' "${PROJECT_ROOT}/requirements.txt"; then
    if ! python3 -m pip install --user --requirement "${PROJECT_ROOT}/requirements.txt"; then
        echo "Warning: pip install failed (likely PEP 668 externally-managed Python on Arch)."
        echo "Install Python dependencies via pacman when possible, then re-run install.sh."
    fi
else
    echo "No extra pip requirements detected. Skipping pip install step."
fi

echo "Deploying application files to ${INSTALL_ROOT}..."
sudo install -d "${INSTALL_ROOT}/deb2arch"
sudo install -d "${INSTALL_ROOT}/assets"

sudo install -m 755 "${PROJECT_ROOT}/deb2arch/main.py" "${INSTALL_ROOT}/deb2arch/main.py"
sudo install -m 644 "${PROJECT_ROOT}/deb2arch/__init__.py" "${INSTALL_ROOT}/deb2arch/__init__.py"
sudo install -m 644 "${PROJECT_ROOT}/deb2arch/gui.py" "${INSTALL_ROOT}/deb2arch/gui.py"
sudo install -m 644 "${PROJECT_ROOT}/deb2arch/converter.py" "${INSTALL_ROOT}/deb2arch/converter.py"
sudo install -m 644 "${PROJECT_ROOT}/deb2arch/installer.py" "${INSTALL_ROOT}/deb2arch/installer.py"
sudo install -m 644 "${PROJECT_ROOT}/deb2arch/utils.py" "${INSTALL_ROOT}/deb2arch/utils.py"
sudo install -m 644 "${PROJECT_ROOT}/assets/icon.png" "${INSTALL_ROOT}/assets/icon.png"

echo "Installing desktop entry and icon..."
sudo install -Dm644 "${PROJECT_ROOT}/deb2arch.desktop" "${DESKTOP_TARGET}"
sudo install -Dm644 "${PROJECT_ROOT}/assets/icon.png" "${ICON_TARGET}"

echo "Installing launcher script to ${BIN_TARGET}..."
sudo tee "${BIN_TARGET}" >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="/opt/deb2arch-installer:${PYTHONPATH:-}"

if [ -t 1 ] || [ -t 2 ]; then
    exec /usr/bin/python3 -m deb2arch.main "$@"
fi

LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/deb2arch-installer"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/launcher.log"

exec /usr/bin/python3 -m deb2arch.main "$@" >>"${LOG_FILE}" 2>&1
EOF
sudo chmod 755 "${BIN_TARGET}"

if [ -f "${LEGACY_BIN_TARGET}" ]; then
    echo "Removing legacy launcher ${LEGACY_BIN_TARGET}..."
    sudo rm -f "${LEGACY_BIN_TARGET}"
fi

echo "Updating desktop and MIME databases..."
sudo update-desktop-database /usr/share/applications || true
sudo update-mime-database /usr/share/mime || true

if command -v xdg-mime >/dev/null 2>&1; then
    echo "Registering .deb MIME handler for current user..."
    xdg-mime default "${APP_NAME}.desktop" application/vnd.debian.binary-package || true
fi

if command -v update-mime-database >/dev/null 2>&1; then
    sudo update-mime-database /usr/share/mime || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f /usr/share/icons/hicolor || true
fi

echo "Installation complete."
echo "Use: ${APP_NAME} /path/to/package.deb"
