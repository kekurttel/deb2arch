pkgname=deb2arch-installer
pkgver=1.0.0
pkgrel=1
pkgdesc="Convert and install Debian packages on Arch Linux from GUI or CLI"
arch=('any')
url="https://github.com/yourname/deb2arch-installer"
license=('MIT')
depends=(
  'python'
  'python-gobject'
  'gtk3'
  'binutils'
  'tar'
  'zstd'
  'pacman'
  'sudo'
)
optdepends=(
  'debtap: preferred .deb conversion backend'
  'polkit: pkexec privilege escalation helper'
)
source=()
sha256sums=()

package() {
  local app_root="${pkgdir}/opt/deb2arch-installer"

  install -d "${app_root}/deb2arch"
  install -d "${app_root}/assets"

  install -m755 "${startdir}/deb2arch/main.py" "${app_root}/deb2arch/main.py"
  install -m644 "${startdir}/deb2arch/__init__.py" "${app_root}/deb2arch/__init__.py"
  install -m644 "${startdir}/deb2arch/gui.py" "${app_root}/deb2arch/gui.py"
  install -m644 "${startdir}/deb2arch/converter.py" "${app_root}/deb2arch/converter.py"
  install -m644 "${startdir}/deb2arch/installer.py" "${app_root}/deb2arch/installer.py"
  install -m644 "${startdir}/deb2arch/utils.py" "${app_root}/deb2arch/utils.py"
  install -m644 "${startdir}/assets/icon.png" "${app_root}/assets/icon.png"

  install -Dm644 "${startdir}/deb2arch.desktop" "${pkgdir}/usr/share/applications/${pkgname}.desktop"
  install -Dm644 "${startdir}/assets/icon.png" "${pkgdir}/usr/share/icons/hicolor/128x128/apps/${pkgname}.png"
  install -Dm644 "${startdir}/LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"

  install -Dm755 /dev/stdin "${pkgdir}/usr/bin/${pkgname}" <<'EOF'
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
}
