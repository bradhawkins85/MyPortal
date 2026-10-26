#!/bin/sh
# Remove the MyPortal tray from Linux.
#
#   sudo /opt/myportal-tray/uninstall.sh            # keep the enrolment
#   sudo /opt/myportal-tray/uninstall.sh --purge    # also forget the enrolment and settings
#   sudo /opt/myportal-tray/uninstall.sh --with-rmm # also remove the MyPortal RMM agent
set -eu

PREFIX=/opt/myportal-tray
SERVICE=MyPortalTrayService
purge=0
with_rmm=0

for arg in "$@"; do
	case "$arg" in
	--purge) purge=1 ;;
	--with-rmm) with_rmm=1 ;;
	-h | --help) sed -n '2,6p' "$0"; exit 0 ;;
	*) echo "uninstall.sh: unknown option: $arg" >&2; exit 1 ;;
	esac
done

[ "$(id -u)" -eq 0 ] || { echo "uninstall.sh: run as root (sudo)" >&2; exit 1; }

systemctl stop "$SERVICE" 2>/dev/null || true
if [ -x "$PREFIX/myportal-tray-service" ]; then
	"$PREFIX/myportal-tray-service" uninstall || true
fi
rm -f "/etc/systemd/system/$SERVICE.service" /etc/xdg/autostart/myportal-tray.desktop
systemctl daemon-reload 2>/dev/null || true
pkill -f "$PREFIX/myportal-tray-ui" 2>/dev/null || true

if [ "$with_rmm" -eq 1 ] && [ -x /opt/myportal-rmm/myportal-rmm ]; then
	/opt/myportal-rmm/myportal-rmm uninstall || true
	rm -rf /opt/myportal-rmm
	[ "$purge" -eq 1 ] && rm -rf /var/lib/myportal-rmm
fi

rm -rf "$PREFIX"
if [ "$purge" -eq 1 ]; then
	rm -rf /etc/myportal-tray /var/lib/myportal-tray /var/log/myportal-tray
fi
echo "The MyPortal tray has been removed."
