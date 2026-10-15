# Reports the device's Windows Update state to MyPortal. Read-only: it searches the
# configured update service and reads update history, but never downloads or installs.
# The state is one line of gzip-compressed, base64-encoded JSON, which MyPortal reads
# and removes from the run output.
function Get-MyPortalWindowsUpdateState {
    # Stable classification IDs avoid localized Windows category names.
    $classifications = @{
        '0fa1201d-4330-4fa8-8ae9-b877473b6441' = 'Security Updates'
        'e6cf1350-c01b-414d-a61f-263d14d133b4' = 'Critical Updates'
        '28bc880e-0592-4cbf-8f95-c79b17911d5f' = 'Update Rollups'
        'cd5ffd1e-e932-4e3a-bf74-18bf0b1bbd83' = 'Updates'
        'e0789628-ce08-4437-be74-2495b842f43b' = 'Definition Updates'
        'b54e7d24-7add-428f-8b75-90a396fa584f' = 'Feature Packs'
        '68c5b0a3-d1a6-4553-ae49-01d3a7827828' = 'Service Packs'
        'b4832bd8-e735-4761-8daf-37f882276dab' = 'Tools'
        '3689bdc8-b205-4af4-8d4a-a63924c5e9d5' = 'Upgrades'
        'ebfc1fc5-71a4-4f7b-9aca-3b9a503104a0' = 'Drivers'
    }
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    function Format-Time($value, [string]$format) {
        if ($null -eq $value) { return $null }
        try { $time = [datetime]$value } catch { return $null }
        if ($time.Year -lt 1990) { return $null }
        return $time.ToString($format, $invariant)
    }
    function Format-Error($record) {
        # Messages can contain braces, so only the code goes through -f.
        return [string]$record.Exception.Message + ' (0x' + ('{0:X8}' -f [int]$record.Exception.HResult) + ')'
    }
    function Get-Classification($categories) {
        foreach ($category in @($categories)) {
            $id = ([string]$category.CategoryID).ToLowerInvariant()
            if ($classifications.ContainsKey($id)) { return $classifications[$id] }
            if ([string]$category.Type -eq 'UpdateClassification') { return [string]$category.Name }
        }
        return $null
    }
    $errors = New-Object System.Collections.Generic.List[string]
    $state = [ordered]@{
        version = 1
        scanned_at = Format-Time ([datetime]::UtcNow) "yyyy-MM-dd'T'HH:mm:ss'Z'"
        source = $null
        reboot_pending = $false
        errors = @()
        missing = $null
        history = $null
        hotfixes = $null
    }
    $searcher = $null
    try {
        $session = New-Object -ComObject Microsoft.Update.Session
        $session.ClientApplicationID = 'MyPortal Windows Update scan'
        $searcher = $session.CreateUpdateSearcher()
    } catch { $errors.Add('Windows Update is unavailable: ' + (Format-Error $_)) }
    if ($searcher) {
        try {
            $found = $searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
            $missing = New-Object System.Collections.Generic.List[object]
            foreach ($update in $found.Updates) {
                $optional = $false
                try { $optional = [bool]$update.BrowseOnly } catch { }
                $missing.Add([ordered]@{
                    update_id = [string]$update.Identity.UpdateID
                    revision = [int]$update.Identity.RevisionNumber
                    title = [string]$update.Title
                    kb = (@($update.KBArticleIDs) | Where-Object { $_ } | ForEach-Object { 'KB' + $_ }) -join ','
                    classification = Get-Classification $update.Categories
                    severity = [string]$update.MsrcSeverity
                    size_bytes = [int64]$update.MaxDownloadSize
                    released = Format-Time $update.LastDeploymentChangeTime 'yyyy-MM-dd'
                    downloaded = [bool]$update.IsDownloaded
                    reboot_behavior = [int]$update.InstallationBehavior.RebootBehavior
                    interactive = [bool]$update.InstallationBehavior.CanRequestUserInput
                    optional = $optional
                })
                if ($missing.Count -ge 500) { break }
            }
            $state.missing = $missing.ToArray()
        } catch { $errors.Add('The search for missing updates failed: ' + (Format-Error $_)) }
        try {
            # History is newest first. Definition updates install several times a day,
            # so only the latest few are kept to leave room for everything else.
            $history = New-Object System.Collections.Generic.List[object]
            $total = [int]$searcher.GetTotalHistoryCount()
            $definitions = 0
            if ($total -gt 0) {
                foreach ($entry in $searcher.QueryHistory(0, [Math]::Min($total, 5000))) {
                    $title = [string]$entry.Title
                    if (-not $title) { continue }
                    $classification = $null
                    try { $classification = Get-Classification $entry.Categories } catch { }
                    if ($classification -eq 'Definition Updates' -or $title -match 'KB2267602|KB2461484|KB915597') {
                        $definitions++
                        if ($definitions -gt 25) { continue }
                    }
                    $history.Add([ordered]@{
                        update_id = [string]$entry.UpdateIdentity.UpdateID
                        title = $title
                        classification = $classification
                        operation = [int]$entry.Operation
                        result = [int]$entry.ResultCode
                        hresult = '0x{0:X8}' -f [int]$entry.HResult
                        date = Format-Time $entry.Date "yyyy-MM-dd'T'HH:mm:ss'Z'"
                        client = [string]$entry.ClientApplicationID
                    })
                    if ($history.Count -ge 1500) { break }
                }
            }
            $state.history = $history.ToArray()
        } catch { $errors.Add('Update history could not be read: ' + (Format-Error $_)) }
    }
    try {
        # Updates installed outside Windows Update (DISM, standalone packages) appear here only.
        $hotfixes = New-Object System.Collections.Generic.List[object]
        foreach ($fix in @(Get-HotFix -ErrorAction Stop)) {
            $installed = $null
            try { if ($fix.InstalledOn) { $installed = Format-Time $fix.InstalledOn 'yyyy-MM-dd' } } catch { }
            $hotfixes.Add([ordered]@{
                kb = [string]$fix.HotFixID
                description = [string]$fix.Description
                installed_on = $installed
                installed_by = [string]$fix.InstalledBy
            })
            if ($hotfixes.Count -ge 1000) { break }
        }
        $state.hotfixes = $hotfixes.ToArray()
    } catch { $errors.Add('Installed hotfixes could not be read: ' + (Format-Error $_)) }
    try {
        $policy = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' -ErrorAction SilentlyContinue
        $au = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -ErrorAction SilentlyContinue
        if ($au -and $au.UseWUServer -eq 1 -and $policy -and $policy.WUServer) {
            $state.source = 'WSUS (' + [string]$policy.WUServer + ')'
        } else {
            $service = @((New-Object -ComObject Microsoft.Update.ServiceManager).Services | Where-Object { $_.IsDefaultAUService }) | Select-Object -First 1
            $state.source = if ($service) { [string]$service.Name } else { 'Windows Update' }
        }
    } catch { }
    try {
        $required = (New-Object -ComObject Microsoft.Update.SystemInfo).RebootRequired
        $state.reboot_pending = [bool]$required
    } catch { }
    if ((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
        (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')) {
        $state.reboot_pending = $true
    }
    $state.errors = $errors.ToArray()
    return $state
}

function Send-MyPortalWindowsUpdateState {
    $state = Get-MyPortalWindowsUpdateState
    $json = ConvertTo-Json -InputObject $state -Depth 5 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $buffer = New-Object IO.MemoryStream
    $gzip = New-Object IO.Compression.GZipStream($buffer, [IO.Compression.CompressionMode]::Compress)
    $gzip.Write($bytes, 0, $bytes.Length)
    $gzip.Dispose()
    # Written directly to stdout so PowerShell's formatting never wraps the long line.
    [Console]::Out.WriteLine('##myportal-windows-updates=' + [Convert]::ToBase64String($buffer.ToArray()))
    [Console]::Out.Flush()
    $script:MyPortalUpdateScanErrors = @($state.errors).Count
    foreach ($problem in @($state.errors)) { Write-Output "Windows Update scan: $problem" }
}
