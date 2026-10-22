# Runs as LocalSystem through the existing RMM runner. All policy data is JSON, never executable text.
$ErrorActionPreference = 'Stop'
$plan = $env:MYPORTAL_PATCH_PLAN | ConvertFrom-Json
$mutex = New-Object Threading.Mutex($false, 'Global\MyPortalPatchManagement')
if (-not $mutex.WaitOne(0)) { throw 'Another patch operation is running.' }
$failed = $false
$rebootRequired = $false
function Pending-Reboot {
    return (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
        (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
        [bool](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
}
function Compare-Version($installed, $latest) {
    # Normalize dotted numeric versions, padding components; unknown vendor formats fail closed.
    $installed = ([string]$installed).Trim(); $latest = ([string]$latest).Trim()
    if ($installed -notmatch '^v?\d+(\.\d+)*$' -or $latest -notmatch '^v?\d+(\.\d+)*$') {
        throw "Cannot safely compare installed version '$installed' with '$latest'."
    }
    $a = @($installed.TrimStart([char[]]'vV').Split('.')); $b = @($latest.TrimStart([char[]]'vV').Split('.'))
    for ($i = 0; $i -lt [Math]::Max($a.Count, $b.Count); $i++) {
        $av = if ($i -lt $a.Count) { [decimal]$a[$i] } else { 0 }
        $bv = if ($i -lt $b.Count) { [decimal]$b[$i] } else { 0 }
        if ($av -lt $bv) { return -1 }; if ($av -gt $bv) { return 1 }
    }
    return 0
}
function Install-PatchUpdates($session, $updates) {
    # Throws when any update fails; a partial installation still records its restart need.
    $downloader = $session.CreateUpdateDownloader(); $downloader.Updates = $updates
    $download = $downloader.Download()
    if ($download.ResultCode -ne 2) { throw "Windows Update download failed: $($download.ResultCode)" }
    $installer = $session.CreateUpdateInstaller(); $installer.Updates = $updates
    $installer.AllowSourcePrompts = $false
    $result = $installer.Install()
    if ($result.RebootRequired) { $script:rebootRequired = $true }
    for ($i = 0; $i -lt $updates.Count; $i++) {
        $detail = $result.GetUpdateResult($i)
        Write-Output "$($updates.Item($i).Title): result=$($detail.ResultCode), HRESULT=$($detail.HResult)"
    }
    if ($result.ResultCode -ne 2) { throw "Windows Update installation failed or partially succeeded: $($result.ResultCode)" }
}
function Choco-Output([string[]]$arguments) {
    $output = @(& $script:choco @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Chocolatey failed ($LASTEXITCODE)." }
    return $output
}
function Latest-ChocolateyVersion([string]$id) {
    # Search each source separately so a newer community version cannot override
    # a package in a preferred company feed.
    foreach ($source in @($plan.sources | Where-Object { $_.enabled } | Sort-Object { [int]$_.priority })) {
        $rows = @(Choco-Output @('search', $id, '--exact', '--limit-output', "--source=$($source.name)"))
        $matchingRows = @($rows | Where-Object { "$_" -match "^$([regex]::Escape($id))\|" })
        if ($matchingRows.Count -gt 1) { throw "No unique stable Chocolatey version for $id in $($source.name)." }
        if ($matchingRows.Count -eq 1) { return ([string]$matchingRows[0]).Split('|')[1] }
    }
    throw "No stable Chocolatey version for $id in the enabled sources."
}
function Run-ChocolateyPackage([string[]]$arguments) {
    Write-Output "Chocolatey: $($arguments -join ' ')"
    & $script:choco @arguments | ForEach-Object { Write-Output $_ }
    $code = $LASTEXITCODE
    if ($code -in @(1641, 3010)) { $script:rebootRequired = $true }
    elseif ($code -ne 0) { throw "Chocolatey returned $code for $($arguments[1])." }
}
try {
    if (@($plan.apps).Count -gt 0) {
        if ($null -eq $plan.sources) { throw 'Company Chocolatey sources are missing.' }
        Sync-ChocolateySources @($plan.sources)
        if ($plan.manual_apps.mode -ne 'uninstall' -and @($plan.sources | Where-Object { $_.enabled }).Count -eq 0) {
            throw 'No Chocolatey sources are enabled.'
        }
    }
    # Only manually pushed app actions can opt out of the reboot gate.
    # Missing options on older queued app jobs use the same default as the UI.
    $ignorePendingReboots = $plan.manual_apps -and $plan.manual_apps.ignore_pending_reboots -ne $false
    $pendingReboot = Pending-Reboot
    if ($pendingReboot) { $rebootRequired = $true }
    if ($pendingReboot -and -not $ignorePendingReboots) {
        Write-Output 'Installation deferred: a restart is already pending.'
    } else {
        if ($pendingReboot) { Write-Output 'Proceeding despite a pending restart as requested.' }
        if ($plan.windows) {
            try {
                $session = New-Object -ComObject Microsoft.Update.Session
                $session.ClientApplicationID = 'MyPortal Patch Management'
                $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
                $updates = New-Object -ComObject Microsoft.Update.UpdateColl
                $cutoff = (Get-Date).ToUniversalTime().AddDays(-[int]$plan.windows.deferral_days)
                # Stable category IDs avoid localized Windows category names.
                $ids = @{
                    'Security Updates' = '0fa1201d-4330-4fa8-8ae9-b877473b6441'
                    'Critical Updates' = 'e6cf1350-c01b-414d-a61f-263d14d133b4'
                    'Update Rollups' = '28bc880e-0592-4cbf-8f95-c79b17911d5f'
                    'Updates' = 'cd5ffd1e-e932-4e3a-bf74-18bf0b1bbd83'
                    'Definition Updates' = 'e0789628-ce08-4437-be74-2495b842f43b'
                }
                $allowed = @($plan.windows.categories | ForEach-Object { $ids[$_] })
                $blocks = Get-MyPortalUpdateBlocks
                foreach ($update in $found.Updates) {
                    if (Test-MyPortalUpdateBlocked $update $blocks) { Write-Output "Blocked in MyPortal, skipped: $($update.Title)"; continue }
                    $match = @($update.Categories | Where-Object { $allowed -contains $_.CategoryID }).Count -gt 0
                    if ($match -and $update.LastDeploymentChangeTime.ToUniversalTime() -le $cutoff -and
                        -not $update.InstallationBehavior.CanRequestUserInput) {
                        if (-not $update.EulaAccepted) { $update.AcceptEula() }
                        [void]$updates.Add($update)
                        Write-Output "Windows Update: $($update.Title)"
                    }
                }
                if ($updates.Count -gt 0) { Install-PatchUpdates $session $updates }
                else { Write-Output 'No applicable Windows updates.' }
            } catch { $failed = $true; Write-Output "Windows Update error: $_" }
        }
        if ($plan.manual_windows) {
            # A technician chose these updates on the asset page; categories and deferral do not apply.
            try {
                $wanted = @($plan.manual_windows.update_ids | ForEach-Object { ([string]$_).ToLowerInvariant() })
                $session = New-Object -ComObject Microsoft.Update.Session
                $session.ClientApplicationID = 'MyPortal Patch Management'
                # A technician may choose an update someone hid on the device; blocked ones are never installed.
                $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Software'")
                $updates = New-Object -ComObject Microsoft.Update.UpdateColl
                $blocks = Get-MyPortalUpdateBlocks
                foreach ($update in $found.Updates) {
                    if ($wanted -notcontains ([string]$update.Identity.UpdateID).ToLowerInvariant()) { continue }
                    if (Test-MyPortalUpdateBlocked $update $blocks) { Write-Output "Blocked in MyPortal, skipped: $($update.Title)"; continue }
                    if ($update.InstallationBehavior.CanRequestUserInput) {
                        Write-Output "Skipped, needs someone at the device: $($update.Title)"; continue
                    }
                    if ($update.IsHidden) { $update.IsHidden = $false }
                    if (-not $update.EulaAccepted) { $update.AcceptEula() }
                    [void]$updates.Add($update)
                    Write-Output "Windows Update: $($update.Title)"
                }
                if ($updates.Count -lt $wanted.Count) {
                    Write-Output "$($wanted.Count - $updates.Count) selected update(s) are no longer needed or cannot install unattended."
                }
                if ($updates.Count -gt 0) { Install-PatchUpdates $session $updates }
                else { Write-Output 'None of the selected updates can be installed.' }
            } catch { $failed = $true; Write-Output "Windows Update error: $_" }
        }
        if (@($plan.apps).Count -gt 0) {
            # Sync-ChocolateySources already installed/resolved Chocolatey and
            # verified company policy. Reuse that executable even with stale PATH.
            $chocoVersion = @(Choco-Output @('--version'))[-1]
            $listArgs = @('list', '--limit-output')
            if ([int]($chocoVersion.Split('.')[0]) -lt 2) { $listArgs += '--local-only' }
            $local = @(Choco-Output $listArgs)
            $installedApps = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue)
            foreach ($app in $plan.apps) {
                try {
                    $id = [string]$app.package_id
                    if ($id -notmatch '^[a-z0-9][a-z0-9._-]{0,127}$') { throw 'Invalid package ID.' }
                    if ($plan.manual_apps) {
                        $mode = [string]$plan.manual_apps.mode
                        if ($mode -notin @('install', 'upgrade', 'uninstall')) { throw 'Invalid Chocolatey action.' }
                        $chocoArgs = @($mode, $id, '--yes', '--no-progress', '--use-package-exit-codes')
                        if ($plan.manual_apps.force) { $chocoArgs += '--force' }
                        if ($ignorePendingReboots) { $chocoArgs += '--ignore-detected-reboot' }
                        if ($mode -eq 'upgrade') { $chocoArgs += '--fail-on-not-installed' }
                        if ($mode -ne 'uninstall') {
                            $latest = Latest-ChocolateyVersion $id
                            $chocoArgs += "--version=$latest"
                        }
                        # Uninstall uses local package metadata; it must still
                        # work when a package has been removed from the feeds.
                        Run-ChocolateyPackage $chocoArgs
                        continue
                    }
                    $latest = Latest-ChocolateyVersion $id
                    $managed = @($local | Where-Object { "$_" -match "^$([regex]::Escape($id))\|" })
                    $chocoArgs = @('--yes', '--no-progress', '--use-package-exit-codes', '--ignore-detected-reboot',
                        "--version=$latest")
                    $external = @($installedApps | Where-Object { $_.DisplayName -ieq $app.display_name })
                    if ($external.Count -gt 1) { throw "Multiple installations match $($app.display_name); review before patching." }
                    # Auto-updated binaries may be newer than Chocolatey's local metadata or catalog.
                    if ($external.Count -eq 1 -and (Compare-Version $external[0].DisplayVersion $latest) -ge 0) {
                        Write-Output "$id is current ($($external[0].DisplayVersion))."; continue
                    }
                    if ($managed.Count -gt 1) { throw "Multiple Chocolatey entries match $id." }
                    if ($managed.Count -eq 1) {
                        $version = ([string]$managed[0]).Split('|')[1]
                        if ((Compare-Version $version $latest) -ge 0) { Write-Output "$id is current ($version)."; continue }
                        $chocoArgs = @('upgrade', $id) + $chocoArgs
                    } else {
                        if ($external.Count -eq 0 -and -not $app.install_missing) { Write-Output "$id is not installed; skipped."; continue }
                        $chocoArgs = @('install', $id, '--force') + $chocoArgs
                    }
                    Run-ChocolateyPackage $chocoArgs
                } catch { $failed = $true; Write-Output "Application patch error: $_" }
            }
        }
    }
    $rebootRequired = $rebootRequired -or (Pending-Reboot)
    Write-Output "Reboot pending: $rebootRequired"
    if ($plan.windows -or $plan.manual_windows) {
        # Refresh the asset page's Windows updates list; a failed scan never fails the patch run.
        try { Send-MyPortalWindowsUpdateState } catch { Write-Output "Could not report Windows Update status: $_" }
    }
    if ($rebootRequired -and -not $failed -and $plan.reboot.reboot_mode -ne 'never') {
        $start = [int]$plan.reboot.active_start; $end = [int]$plan.reboot.active_end
        function Active-Hour([datetime]$time) {
            $hour = $time.Hour
            if ($start -lt $end) { return $hour -ge $start -and $hour -lt $end }
            return $hour -ge $start -or $hour -lt $end
        }
        $delay = [int]$plan.reboot.reboot_delay_minutes
        $safe = $true
        # Check the whole countdown, including overnight active hours. A technician's
        # install from the asset page restarts when they asked, regardless of active hours.
        if (-not $plan.reboot.immediate) {
            for ($i = 0; $i -le $delay; $i++) { if (Active-Hour (Get-Date).AddMinutes($i)) { $safe = $false } }
        }
        if ($plan.reboot.reboot_mode -eq 'no_users') {
            # WTS includes disconnected RDP sessions; query failures prevent reboot.
            Add-Type @'
using System;
using System.Runtime.InteropServices;
public class PatchSessions {
    [StructLayout(LayoutKind.Sequential)] public struct Session { public int Id; public IntPtr Name; public int State; }
    [DllImport("wtsapi32.dll")] static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr data, out int count);
    [DllImport("wtsapi32.dll")] static extern bool WTSQuerySessionInformation(IntPtr server, int id, int info, out IntPtr data, out int bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr data);
    public static bool HasUsers() {
        IntPtr data; int count;
        if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out data, out count)) throw new Exception("Cannot enumerate sessions");
        try {
            int size = Marshal.SizeOf(typeof(Session));
            for (int i = 0; i < count; i++) {
                var s = (Session)Marshal.PtrToStructure(IntPtr.Add(data, i * size), typeof(Session));
                IntPtr user; int bytes;
                if (!WTSQuerySessionInformation(IntPtr.Zero, s.Id, 5, out user, out bytes)) throw new Exception("Cannot query session");
                try { if (!String.IsNullOrEmpty(Marshal.PtrToStringAnsi(user))) return true; }
                finally { WTSFreeMemory(user); }
            }
            return false;
        } finally { WTSFreeMemory(data); }
    }
}
'@
            if ([PatchSessions]::HasUsers()) { $safe = $false; Write-Output 'Reboot deferred: a user session exists.' }
        }
        if ($safe) {
            & shutdown.exe /r /t ($delay * 60) /d p:2:17 /c 'MyPortal: updates require a restart. Save your work.'
            if ($LASTEXITCODE -ne 0) { throw "Could not schedule restart ($LASTEXITCODE)." }
            Write-Output "Restart scheduled in $delay minutes."
        } else { Write-Output 'Reboot deferred by active hours or user sessions; reconsidered at the next policy run.' }
    }
    if ($failed) { exit 1 }
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
