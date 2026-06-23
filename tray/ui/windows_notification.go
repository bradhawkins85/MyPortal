//go:build windows

package main

import (
	"encoding/base64"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"unicode/utf16"
)

const windowsToastAppID = "MyPortal.Tray"

var ensureWindowsToastShortcutOnce sync.Once

func windowsToastEncodedCommand(title, body string) string {
	appID := windowsToastAppID
	script := windowsToastScript(title, body, "", appID, windowsToastIconURL(), true)
	return encodePowerShellCommand(script)
}

func windowsPersistentChatToastEncodedCommand(title, body, chatURL string) string {
	appID := windowsToastAppID
	script := windowsToastScript(title, body, "reminder", appID, windowsToastIconURL(), false) + `
$actions = $xml.CreateElement('actions')
$open = $xml.CreateElement('action')
$open.SetAttribute('content', 'Open chat')
$open.SetAttribute('activationType', 'protocol')
$open.SetAttribute('arguments', '` + powershellSingleQuotedString(chatURL) + `')
[void]$actions.AppendChild($open)
$input = $xml.CreateElement('input')
$input.SetAttribute('id', 'snoozeTime')
$input.SetAttribute('type', 'selection')
$input.SetAttribute('defaultInput', '15')
$selection = $xml.CreateElement('selection')
$selection.SetAttribute('id', '15')
$selection.SetAttribute('content', '15 minutes')
[void]$input.AppendChild($selection)
[void]$actions.AppendChild($input)
$snooze = $xml.CreateElement('action')
$snooze.SetAttribute('content', 'Snooze')
$snooze.SetAttribute('activationType', 'system')
$snooze.SetAttribute('arguments', 'snooze')
[void]$actions.AppendChild($snooze)
$dismiss = $xml.CreateElement('action')
$dismiss.SetAttribute('content', 'Dismiss')
$dismiss.SetAttribute('activationType', 'system')
$dismiss.SetAttribute('arguments', 'dismiss')
[void]$actions.AppendChild($dismiss)
[void]$toast.AppendChild($actions)
$notification = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notification.Tag = 'myportal-chat'
$notification.Group = 'myportal'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('` + powershellSingleQuotedString(appID) + `').Show($notification)`
	return encodePowerShellCommand(script)
}

func showChatSessionNotification(title, body, chatURL string) {
	ensureWindowsToastShortcut()
	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand", windowsPersistentChatToastEncodedCommand(title, body, chatURL))
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	_ = cmd.Start()
}

func ensureWindowsToastShortcut() {
	ensureWindowsToastShortcutOnce.Do(func() {
		exe, err := os.Executable()
		if err != nil {
			return
		}
		shortcut := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "MyPortal Tray.lnk")
		script := windowsToastShortcutScript(exe, shortcut, windowsToastAppID, trayDisplayName(gConfig), windowsToastIconPath())
		cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand", encodePowerShellCommand(script))
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
		_ = cmd.Run()
	})
}

func windowsToastShortcutScript(exePath, shortcutPath, appID, appName, iconPath string) string {
	iconScript := ""
	if iconPath != "" {
		iconScript = `$shortcut.IconLocation = '` + powershellSingleQuotedString(iconPath) + `'
`
	}
	return `$shortcutPath = '` + powershellSingleQuotedString(shortcutPath) + `'
$shortcutDir = Split-Path -Parent $shortcutPath
if (-not (Test-Path -LiteralPath $shortcutDir)) { [void](New-Item -ItemType Directory -Path $shortcutDir -Force) }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = '` + powershellSingleQuotedString(exePath) + `'
$shortcut.WorkingDirectory = '` + powershellSingleQuotedString(filepath.Dir(exePath)) + `'
$shortcut.Description = '` + powershellSingleQuotedString(appName) + `'
` + iconScript + `$shortcut.Save()
$code = @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink {}
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
interface IShellLinkW { void GetPath(IntPtr pszFile, int cchMaxPath, IntPtr pfd, uint fFlags); void GetIDList(out IntPtr ppidl); void SetIDList(IntPtr pidl); void GetDescription(IntPtr pszName, int cchMaxName); void SetDescription(string pszName); void GetWorkingDirectory(IntPtr pszDir, int cchMaxPath); void SetWorkingDirectory(string pszDir); void GetArguments(IntPtr pszArgs, int cchMaxPath); void SetArguments(string pszArgs); void GetHotkey(out short pwHotkey); void SetHotkey(short wHotkey); void GetShowCmd(out int piShowCmd); void SetShowCmd(int iShowCmd); void GetIconLocation(IntPtr pszIconPath, int cchIconPath, out int piIcon); void SetIconLocation(string pszIconPath, int iIcon); void SetRelativePath(string pszPathRel, uint dwReserved); void Resolve(IntPtr hwnd, uint fFlags); void SetPath(string pszFile); }
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010b-0000-0000-C000-000000000046")]
interface IPersistFile { void GetClassID(out Guid pClassID); void IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode); void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName); void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName); }
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("00000138-0000-0000-C000-000000000046")]
interface IPropertyStore { void GetCount(out uint cProps); void GetAt(uint iProp, out PROPERTYKEY pkey); void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv); void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv); void Commit(); }
[StructLayout(LayoutKind.Sequential, Pack=4)] struct PROPERTYKEY { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Sequential)] struct PROPVARIANT { public ushort vt; public ushort w1; public ushort w2; public ushort w3; public IntPtr p; public int p2; }
public static class ToastShortcut { public static void SetAppId(string path, string appId) { var link=(IShellLinkW)new CShellLink(); ((IPersistFile)link).Load(path, 0); var store=(IPropertyStore)link; var key=new PROPERTYKEY{fmtid=new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid=5}; var pv=new PROPVARIANT{vt=31, p=Marshal.StringToCoTaskMemUni(appId)}; try { store.SetValue(ref key, ref pv); store.Commit(); ((IPersistFile)link).Save(path, true); } finally { Marshal.FreeCoTaskMem(pv.p); } } }
'@
Add-Type -TypeDefinition $code -ErrorAction Stop
[ToastShortcut]::SetAppId($shortcutPath, '` + powershellSingleQuotedString(appID) + `')
`
}

func windowsToastScript(title, body, scenario, appID, iconURL string, show bool) string {
	scenarioScript := ""
	if scenario != "" {
		scenarioScript = `$toast.SetAttribute('scenario', '` + powershellSingleQuotedString(scenario) + `')
`
	}
	logoScript := ""
	if iconURL != "" {
		logoScript = `$binding = $xml.GetElementsByTagName('binding').Item(0)
$appLogo = $xml.CreateElement('image')
$appLogo.SetAttribute('placement', 'appLogoOverride')
$appLogo.SetAttribute('src', '` + powershellSingleQuotedString(iconURL) + `')
[void]$binding.InsertBefore($appLogo, $binding.FirstChild)
`
	}
	script := `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastGeneric)
$toast = $xml.GetElementsByTagName('toast')[0]
` + scenarioScript + logoScript + `$textNodes = $xml.GetElementsByTagName('text')
[void]$textNodes.Item(0).AppendChild($xml.CreateTextNode('` + powershellSingleQuotedString(title) + `'))
[void]$textNodes.Item(1).AppendChild($xml.CreateTextNode('` + powershellSingleQuotedString(body) + `'))`
	if show {
		script += `
try {
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('` + powershellSingleQuotedString(appID) + `').Show([Windows.UI.Notifications.ToastNotification]::new($xml))
} catch {
` + windowsFormsBalloonFallbackScript(title, body) + `
}`
	}
	return script
}

func windowsFormsBalloonFallbackScript(title, body string) string {
	return `    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notify.BalloonTipTitle = '` + powershellSingleQuotedString(title) + `'
    $notify.BalloonTipText = '` + powershellSingleQuotedString(body) + `'
    $notify.Visible = $true
    $notify.ShowBalloonTip(10000)
    Start-Sleep -Seconds 11
    $notify.Dispose()`
}

func windowsToastIconURL() string {
	portalURL := strings.TrimSpace(gPortalURL)
	if portalURL == "" {
		return ""
	}
	return strings.TrimRight(portalURL, "/") + "/tray/icon.ico"
}

func windowsToastIconPath() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return exe
}

func powershellSingleQuotedString(value string) string {
	return strings.ReplaceAll(value, `'`, `''`)
}

func encodePowerShellCommand(script string) string {
	encoded := utf16.Encode([]rune(script))
	buf := make([]byte, 0, len(encoded)*2)
	for _, r := range encoded {
		buf = append(buf, byte(r), byte(r>>8))
	}
	return base64.StdEncoding.EncodeToString(buf)
}
