#!/usr/bin/env bash
set -euo pipefail

APP_NAME="deb2arch-installer"
INSTALL_ROOT="/opt/${APP_NAME}"
BIN_TARGET="/usr/bin/${APP_NAME}"
LEGACY_BIN_TARGET="/usr/local/bin/${APP_NAME}"
DESKTOP_TARGET="/usr/share/applications/${APP_NAME}.desktop"
ICON_TARGET="/usr/share/icons/hicolor/128x128/apps/${APP_NAME}.png"
LICENSE_DIR="/usr/share/licenses/${APP_NAME}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

require_command sudo

echo "Uninstalling ${APP_NAME}..."

sudo rm -rf "${INSTALL_ROOT}"
sudo rm -f "${BIN_TARGET}"
sudo rm -f "${LEGACY_BIN_TARGET}"
sudo rm -f "${DESKTOP_TARGET}"
sudo rm -f "${ICON_TARGET}"
sudo rm -rf "${LICENSE_DIR}"

echo "Updating desktop and MIME databases..."
sudo update-desktop-database /usr/share/applications || true
sudo update-mime-database /usr/share/mime || true

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f /usr/share/icons/hicolor || true
fi

echo "${APP_NAME} has been removed."
echo "If .deb files still point to this app, set another handler with:"
echo "  xdg-mime default <other.desktop> application/vnd.debian.binary-package"
