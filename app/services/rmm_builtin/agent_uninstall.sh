#!/bin/bash
# Removes the MyPortal Tray Agent and the MyPortal RMM agent from this Linux or
# macOS computer.
#
# The RMM agent runs this script, so it cannot remove itself while the script is
# still running: the script writes the removal steps to /var/tmp and starts them
# outside the RMM agent's service (a transient systemd unit on Linux, a one-off
# LaunchDaemon on macOS), then exits. They wait for the RMM agent to report this
# run to MyPortal, remove the tray (service, files, settings and enrolment) and
# then the RMM agent (service, files and enrolment). Their log is kept at
# /var/tmp/myportal-uninstall.log.
set -u

CLEANUP=/var/tmp/myportal-uninstall.sh
LOG=/var/tmp/myportal-uninstall.log
LABEL=io.myportal.uninstall

if [ "$(id -u)" -ne 0 ]; then
	echo 'The uninstall must run as root. Queue it again without "Run as logged in user".' >&2
	exit 1
fi

cat >"$CLEANUP" <<'CLEANUP_SCRIPT'
#!/bin/bash
LOG=/var/tmp/myportal-uninstall.log
LABEL=io.myportal.uninstall
log() { printf '%s %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >>"$LOG"; }

log 'Waiting for the RMM agent to report the uninstall run'
sleep 20

if [ "$(uname -s)" = Darwin ]; then
	# 1. The tray, first, so it cannot reinstall the RMM agent.
	agent_plist=/Library/LaunchAgents/io.myportal.tray.agent.plist
	daemon_plist=/Library/LaunchDaemons/io.myportal.tray.service.plist
	for uid in $(/bin/ps -axo uid= 2>/dev/null | /usr/bin/sort -u); do
		if [ "$uid" -ge 500 ] 2>/dev/null; then
			/bin/launchctl bootout "gui/$uid" "$agent_plist" 2>/dev/null
			/bin/launchctl asuser "$uid" /bin/launchctl unload "$agent_plist" 2>/dev/null
		fi
	done
	/bin/launchctl bootout system "$daemon_plist" 2>/dev/null || /bin/launchctl unload "$daemon_plist" 2>/dev/null
	/bin/rm -f "$daemon_plist" "$agent_plist" /Library/Preferences/io.myportal.tray.env
	/bin/rm -rf /Library/MyPortal/Tray "/Library/Application Support/MyPortal/Tray" /Library/Logs/MyPortal/tray
	/usr/sbin/pkgutil --forget io.myportal.tray >/dev/null 2>&1
	/bin/rmdir /Library/MyPortal "/Library/Application Support/MyPortal" /Library/Logs/MyPortal 2>/dev/null
	log 'Removed the MyPortal Tray Agent'

	# 2. The RMM agent.
	rmm_dir="/Library/Application Support/MyPortal RMM"
	[ -x "$rmm_dir/myportal-rmm" ] && log "myportal-rmm uninstall: $("$rmm_dir/myportal-rmm" uninstall 2>&1)"
	/bin/launchctl bootout system/MyPortalRMMAgent 2>/dev/null
	/bin/rm -f /Library/LaunchDaemons/MyPortalRMMAgent.plist
	/usr/bin/pkill -f "$rmm_dir/myportal-rmm" 2>/dev/null
	/bin/rm -rf "$rmm_dir"
	log 'Removed the MyPortal RMM agent'

	/bin/rm -f "/Library/LaunchDaemons/$LABEL.plist" "$0"
	# Ends this script too, so it comes last.
	/bin/launchctl bootout "system/$LABEL" 2>/dev/null || /bin/launchctl remove "$LABEL" 2>/dev/null
	exit 0
fi

# Linux. 1. The tray, first, so it cannot reinstall the RMM agent.
systemctl disable --now MyPortalTrayService >/dev/null 2>&1
[ -x /opt/myportal-tray/myportal-tray-service ] && /opt/myportal-tray/myportal-tray-service uninstall >/dev/null 2>&1
pkill -f /opt/myportal-tray/ 2>/dev/null
rm -f /etc/systemd/system/MyPortalTrayService.service /etc/xdg/autostart/myportal-tray.desktop
rm -rf /opt/myportal-tray /etc/myportal-tray /var/lib/myportal-tray /var/log/myportal-tray
log 'Removed the MyPortal Tray Agent'

# 2. The RMM agent.
[ -x /opt/myportal-rmm/myportal-rmm ] && log "myportal-rmm uninstall: $(/opt/myportal-rmm/myportal-rmm uninstall 2>&1)"
systemctl disable --now MyPortalRMMAgent >/dev/null 2>&1
rm -f /etc/systemd/system/MyPortalRMMAgent.service
pkill -f /opt/myportal-rmm/ 2>/dev/null
rm -rf /opt/myportal-rmm /var/lib/myportal-rmm
systemctl daemon-reload >/dev/null 2>&1
log 'Removed the MyPortal RMM agent'
rm -f "$0"
exit 0
CLEANUP_SCRIPT
chmod 700 "$CLEANUP" || exit 1

if [ "$(uname -s)" = Darwin ]; then
	plist="/Library/LaunchDaemons/$LABEL.plist"
	cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$CLEANUP</string></array>
  <key>RunAtLoad</key><true/>
  <key>LaunchOnlyOnce</key><true/>
</dict>
</plist>
PLIST
	chmod 644 "$plist"
	/bin/launchctl bootout "system/$LABEL" 2>/dev/null
	if ! /bin/launchctl bootstrap system "$plist" 2>/dev/null && ! /bin/launchctl load "$plist"; then
		echo "Could not start $plist." >&2
		exit 1
	fi
elif command -v systemd-run >/dev/null 2>&1; then
	# A transient unit outside the RMM agent's service, which systemd stops with it.
	if ! systemd-run --unit="myportal-uninstall-$$" --quiet /bin/bash "$CLEANUP"; then
		echo 'Could not start the uninstall with systemd-run.' >&2
		exit 1
	fi
else
	setsid /bin/bash "$CLEANUP" </dev/null >/dev/null 2>&1 &
fi

echo 'Started the uninstall. In about 30 seconds it removes the MyPortal Tray Agent and then the MyPortal RMM agent.'
echo "Its log is kept at $LOG."
exit 0
