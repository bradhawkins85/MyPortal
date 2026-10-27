#!/bin/sh
# Install, reinstall or upgrade the MyPortal tray on Linux.
#
#   sudo ./install.sh --url https://portal.example.com --token INSTALL_TOKEN
#   sudo ./install.sh --url ... --token ... --headless    # servers, e.g. Proxmox VE
#   sudo ./install.sh --url ... --token ... --desktop     # always add the tray icon
#   sudo ./install.sh --upgrade                           # used by tray self-updates
#
# The service (myportal-tray-service) runs as root under systemd on every
# computer. On a computer with a desktop the tray UI is added too, and XDG
# autostart opens it in each user's session. Without --headless or --desktop
# the installer adds the UI when an X11 or Wayland desktop session is installed.
#
# Options: --no-auto-update turns off automatic tray updates (AUTO_UPDATE=false).
set -eu

PREFIX=/opt/myportal-tray
CONF_DIR=/etc/myportal-tray
ENV_FILE="$CONF_DIR/tray.env"
AUTOSTART=/etc/xdg/autostart/myportal-tray.desktop
SERVICE=MyPortalTrayService

die() { echo "install.sh: $*" >&2; exit 1; }

here="$(cd "$(dirname "$0")" && pwd)"
url=""
token=""
mode=auto
upgrade=0
auto_update=""

while [ $# -gt 0 ]; do
	case "$1" in
	--url) [ $# -ge 2 ] || die "--url needs a value"; url="$2"; shift 2 ;;
	--token) [ $# -ge 2 ] || die "--token needs a value"; token="$2"; shift 2 ;;
	--headless) mode=headless; shift ;;
	--desktop) mode=desktop; shift ;;
	--upgrade) upgrade=1; shift ;;
	--no-auto-update) auto_update=false; shift ;;
	-h | --help) sed -n '2,16p' "$0"; exit 0 ;;
	*) die "unknown option: $1" ;;
	esac
done

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
case "$(uname -m)" in
x86_64 | amd64) arch=amd64 ;;
aarch64 | arm64) arch=arm64 ;;
*) die "unsupported processor: $(uname -m) (amd64 and arm64 are supported)" ;;
esac
[ -f "$here/$arch/myportal-tray-service" ] || die "$here/$arch/myportal-tray-service is missing"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"

has_desktop() {
	# graphical.target is also the default on some headless servers. Check
	# installed sessions instead, so detection also works over SSH or sudo.
	for session in /usr/share/xsessions/*.desktop /usr/share/wayland-sessions/*.desktop \
		/usr/local/share/xsessions/*.desktop /usr/local/share/wayland-sessions/*.desktop; do
		[ -f "$session" ] && return 0
	done
	return 1
}

if [ "$upgrade" -eq 1 ]; then
	[ -f "$ENV_FILE" ] || die "the tray is not installed ($ENV_FILE is missing)"
	if [ "$mode" = auto ]; then
		# Keep what the computer has.
		if [ -f "$PREFIX/myportal-tray-ui" ]; then mode=desktop; else mode=headless; fi
	fi
else
	if [ -z "$url" ] || [ -z "$token" ]; then
		[ -f "$ENV_FILE" ] || die "--url and --token are required"
	else
		case "$url" in
		https://*) ;;
		*) die "--url must start with https://" ;;
		esac
		case "$url" in
		*[!A-Za-z0-9._~:/%-]*) die "--url contains characters that are not allowed" ;;
		esac
		case "$token" in
		*[!A-Za-z0-9._~-]*) die "--token contains characters that are not allowed" ;;
		esac
		install -d -m 0755 "$CONF_DIR"
		umask 077
		{
			echo "MYPORTAL_URL=${url%/}"
			echo "ENROL_TOKEN=$token"
			echo "AUTO_UPDATE=${auto_update:-true}"
		} >"$ENV_FILE.tmp"
		chmod 0600 "$ENV_FILE.tmp"
		mv -f "$ENV_FILE.tmp" "$ENV_FILE"
		umask 022
	fi
	if [ -n "$auto_update" ] && [ -z "$url" ]; then
		sed -i 's/^AUTO_UPDATE=.*/AUTO_UPDATE=false/' "$ENV_FILE"
	fi
	if [ "$mode" = auto ]; then
		if has_desktop; then mode=desktop; else mode=headless; fi
	fi
fi

echo "Installing the MyPortal tray ($mode, $arch) in $PREFIX"
systemctl stop "$SERVICE" 2>/dev/null || true

install -d -m 0755 "$PREFIX"
install -m 0755 "$here/$arch/myportal-tray-service" "$PREFIX/myportal-tray-service.new"
mv -f "$PREFIX/myportal-tray-service.new" "$PREFIX/myportal-tray-service"
install -m 0755 "$here/uninstall.sh" "$PREFIX/uninstall.sh"

if [ "$mode" = desktop ]; then
	install -m 0755 "$here/$arch/myportal-tray-ui" "$PREFIX/myportal-tray-ui.new"
	mv -f "$PREFIX/myportal-tray-ui.new" "$PREFIX/myportal-tray-ui"
	install -d -m 0755 "$(dirname "$AUTOSTART")"
	cat >"$AUTOSTART" <<DESKTOP
[Desktop Entry]
Type=Application
Name=MyPortal
Comment=MyPortal helpdesk tray icon
Exec=$PREFIX/myportal-tray-ui
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESKTOP
	chmod 0644 "$AUTOSTART"
else
	# The service treats the UI binary as the sign of a desktop install.
	rm -f "$PREFIX/myportal-tray-ui" "$AUTOSTART"
fi

if [ ! -f "/etc/systemd/system/$SERVICE.service" ]; then
	"$PREFIX/myportal-tray-service" install
fi
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"

if [ "$mode" = desktop ]; then
	echo "The tray icon appears the next time each user signs in to the desktop."
	echo "GNOME needs the AppIndicator extension (gnome-shell-extension-appindicator) to show tray icons."
fi
echo "Done. Logs: journalctl -u $SERVICE and $PREFIX/logs/service.log"
