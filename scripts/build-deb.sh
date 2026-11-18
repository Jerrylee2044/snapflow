#!/usr/bin/env bash
# 从源码构建 deb 包，输出到 dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import pathlib, re; print(re.search(r"__version__ = \"(.*)\"", pathlib.Path("snapflow/__init__.py").read_text()).group(1))')"
PKG="snapflow_${VERSION}-1_all.deb"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "==> 构建 snapflow $VERSION"

mkdir -p "$BUILD/DEBIAN" "$BUILD/usr/bin" "$BUILD/usr/share/snapflow" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/128x128/apps" \
         "$BUILD/usr/share/doc/snapflow" \
         "$BUILD/etc/xdg/autostart"

cp -r snapflow "$BUILD/usr/share/snapflow/"
cp main.py "$BUILD/usr/share/snapflow/"
cp README.md "$BUILD/usr/share/doc/snapflow/"
cat > "$BUILD/usr/share/doc/snapflow/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: snapflow

Files: *
Copyright: 2026 jerrylee
License: MIT

Files: usr/share/snapflow/vendor/mss/*
Copyright: 2013-2025, Mickaël Schoentgen & contributors
License: MIT
EOF
find "$BUILD" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# mss 不在 apt 源中，随包 vendor（纯 Python，MIT）
python3 -m pip download mss --no-deps -q -d "$BUILD/wheel"
mkdir -p "$BUILD/usr/share/snapflow/vendor"
python3 -m zipfile -e "$(ls "$BUILD"/wheel/*.whl)" "$BUILD/usr/share/snapflow/vendor/"

cat > "$BUILD/usr/bin/snapflow" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/share/snapflow/vendor:/usr/share/snapflow${PYTHONPATH:+:$PYTHONPATH}"
exec python3 /usr/share/snapflow/main.py "$@"
EOF
chmod 755 "$BUILD/usr/bin/snapflow"

cat > "$BUILD/usr/share/applications/snapflow.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Snapflow
Name[zh_CN]=Snapflow 截图
Comment=Screenshot with annotation (X11 & Wayland)
Comment[zh_CN]=截图与标注工具（支持 X11 与 Wayland）
Exec=snapflow trigger
Icon=snapflow
Categories=Utility;Graphics;
Keywords=screenshot;capture;annotation;截图;
StartupNotify=false
EOF

# 常驻触发器：登录后自动启动，快捷键只发一条本地消息，避免冷启动
cat > "$BUILD/etc/xdg/autostart/snapflow.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Snapflow Daemon
Name[zh_CN]=Snapflow 后台服务
Comment=Warm trigger service for the Snapflow screenshot tool
Comment[zh_CN]=截图工具 Snapflow 的常驻触发器
Exec=snapflow daemon
Icon=snapflow
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

cat > "$BUILD/DEBIAN/control" <<EOF
Package: snapflow
Version: ${VERSION}-1
Section: graphics
Priority: optional
Architecture: all
Maintainer: jerrylee <jerrylee@localhost>
Depends: python3, python3-pyqt6, python3-dbus-next, wl-clipboard | xclip
Recommends: grim
Description: 截图与标注工具（X11 优先，Wayland 经 XDG Portal / grim 兜底）
 微信式单层交互：拖拽框选后在选区旁直接标注（矩形/箭头/文字/马赛克），
 Enter 复制到剪贴板，Ctrl+Enter 保存到 ~/Pictures/截图/。
 .
 Wayland 下经 XDG Portal 截图（GNOME/KDE），wlroots 系合成器使用 grim，
 X11 使用内置的 mss。内置全局热键仅限 X11；Wayland 请绑定桌面快捷键到
 \`snapflow capture\`。
EOF

# 图标在打包树中生成，避免仓库携带二进制
python3 - "$BUILD/usr/share/icons/hicolor/128x128/apps/snapflow.png" <<'PY'
import sys
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
img = QImage(128, 128, QImage.Format.Format_ARGB32)
img.fill(0)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
g = QLinearGradient(0, 0, 0, 128)
g.setColorAt(0, QColor('#0a84ff'))
g.setColorAt(1, QColor('#0060df'))
p.setBrush(g)
p.setPen(Qt.PenStyle.NoPen)
p.drawRoundedRect(QRectF(4, 4, 120, 120), 26, 26)
p.setPen(QPen(QColor('white'), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
p.setBrush(Qt.BrushStyle.NoBrush)
p.drawLine(28, 28, 52, 28); p.drawLine(28, 28, 28, 52)
p.drawLine(100, 28, 76, 28); p.drawLine(100, 28, 100, 52)
p.drawLine(28, 100, 28, 76); p.drawLine(28, 100, 52, 100)
p.drawLine(100, 100, 76, 100); p.drawLine(100, 100, 100, 76)
p.setBrush(QColor('white'))
p.setPen(Qt.PenStyle.NoPen)
p.drawEllipse(QRectF(56, 56, 16, 16))
p.end()
img.save(sys.argv[1])
PY

mkdir -p dist
dpkg-deb --root-owner-group --build "$BUILD" "dist/$PKG"
echo "==> 完成：dist/$PKG"
