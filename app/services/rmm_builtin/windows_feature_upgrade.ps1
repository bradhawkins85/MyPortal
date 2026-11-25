# Windows feature upgrades use only offers from the installed Windows Update agent.
function Get-MyPortalWindowsRelease {
    $version = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
    return [pscustomobject]@{
        client = $version.InstallationType -eq 'Client'
        version = [string]$version.DisplayVersion
        build = [int]$version.CurrentBuildNumber
        ubr = [int]$version.UBR
    }
}

function Test-MyPortalUpdateCategory($update, [string]$category) {
    foreach ($item in $update.Categories) {
        if ($item.CategoryID -eq $category) { return $true }
    }
    return $false
}

function Test-MyPortalUpdateKB($update, [string]$kb) {
    return $kb -and @($update.KBArticleIDs) -contains $kb.Substring(2)
}

function Search-MyPortalFeatureOffers($searcher, [string]$criteria) {
    try { return $searcher.Search("($criteria and DeploymentAction='Installation') or ($criteria and DeploymentAction='OptionalInstallation')") }
    catch {
        # Older Windows Update agents can reject OptionalInstallation. Other
        # errors still fail the scan, so missing evidence cannot trigger a fallback hop.
        if (('{0:X8}' -f $_.Exception.GetBaseException().HResult) -ne '80240032') { throw }
        Write-Host 'This Windows Update agent does not support optional deployment searches. Checking regular offers.'
        return $searcher.Search($criteria)
    }
}

function Test-MyPortalFeatureUpdate($update, $releases) {
    if (Test-MyPortalUpdateCategory $update '3689bdc8-b205-4af4-8d4a-a63924c5e9d5') { return $true }
    foreach ($release in $releases) {
        if (Test-MyPortalUpdateKB $update $release.enablement_kb) { return $true }
    }
    return $false
}

function Get-MyPortalFeatureOffer($update, $releases) {
    if ($update.IsBeta -or $update.Title -match '\b(?:Insider|Preview)\b') { return }
    foreach ($release in $releases) {
        if (Test-MyPortalUpdateKB $update $release.enablement_kb) {
            return [pscustomobject]@{ release = $release; update = $update; enablement = $true }
        }
    }
    if ($update.Title -notmatch '\bWindows\s+11\b' -or
        -not (Test-MyPortalUpdateCategory $update '3689bdc8-b205-4af4-8d4a-a63924c5e9d5')) { return }
    # Catalog titles are localized, but the Windows product name and release token are stable.
    $versions = [regex]::Matches([string]$update.Title, '(?i)(?<![a-z0-9])[0-9]{2}H[12](?![a-z0-9])')
    if ($versions.Count -ne 1) { return }
    foreach ($release in $releases) {
        if ($versions[0].Value -eq $release.version) {
            return [pscustomobject]@{ release = $release; update = $update; enablement = $false }
        }
    }
}

function Get-MyPortalFeatureHold($update, $blocks, [datetime]$cutoff) {
    if (Test-MyPortalUpdateBlocked $update $blocks) { return 'blocked in MyPortal' }
    if ($update.IsHidden) { return 'hidden on this device' }
    if ($update.InstallationBehavior.CanRequestUserInput) { return 'requires someone at the device' }
    if ($update.LastDeploymentChangeTime.ToUniversalTime() -gt $cutoff) { return 'within the policy deferral period' }
}

function Find-MyPortalServicingUpdate($offers, $current, [int]$minimumUbr, [string]$kb, $blocks, [datetime]$cutoff) {
    # The exact Microsoft KB is safe on this branch when WUA says it applies. For
    # a successor, require an OS build revision in a security update title so an
    # unrelated KB, .NET update or optional preview cannot stand in for that CU.
    $buildRevision = '(?<![\d.])' + $current.build + '\.(\d+)(?![\d.])'
    $matching = foreach ($update in $offers) {
        if ($update.IsBeta) { continue }
        if (Test-MyPortalUpdateKB $update $kb) {
            [pscustomobject]@{ update = $update; revision = $minimumUbr }
        } elseif ($update.DeploymentAction -ne 4 -and $update.KBArticleIDs.Count -gt 0 -and
            $update.Title -notmatch '\.NET|\b(?:Insider|Preview)\b' -and
            (Test-MyPortalUpdateCategory $update '0fa1201d-4330-4fa8-8ae9-b877473b6441') -and
            $update.Title -match $buildRevision -and [int]$Matches[1] -ge $minimumUbr) {
            [pscustomobject]@{ update = $update; revision = [int]$Matches[1] }
        }
    }
    $best = $matching | Where-Object { -not (Get-MyPortalFeatureHold $_.update $blocks $cutoff) } |
        Sort-Object revision -Descending | Select-Object -First 1
    if ($best) { return $best.update }
}

function Get-MyPortalFeatureStep($feature, $current, $offered, $blocks, [datetime]$cutoff) {
    $releases = @($feature.releases)
    $target = $releases | Where-Object { $_.version -eq $feature.target } | Select-Object -First 1
    if (-not $target) { throw 'The target Windows release is missing from the upgrade catalog.' }
    if (-not $current.client -or $current.build -lt 10240) {
        return @{ message = 'Only Windows 10 and Windows 11 client devices can use this policy.' }
    }
    if ($current.version -eq '26H1' -or $current.build -eq 28000) {
        return @{ message = 'Windows 11 26H1 is on a separate servicing branch. No in-place update is planned.' }
    }
    if ($current.build -ge $target.build) {
        return @{ message = "The device is already at or beyond the target (build $($current.build).$($current.ubr))." }
    }

    $currentRelease = $releases | Where-Object { $_.build -eq $current.build } | Select-Object -First 1
    $qualityOffers = @($offered | Where-Object { -not (Test-MyPortalFeatureUpdate $_ $releases) })
    $featureOffers = @($offered | ForEach-Object { Get-MyPortalFeatureOffer $_ $releases })
    $route = @($target)
    if ($target.prerequisite_versions -notcontains $currentRelease.version) {
        $route += @($releases | Where-Object { $_.build -gt $current.build -and $_.build -lt $target.build -and
            $target.prerequisite_versions -contains $_.version } | Sort-Object build -Descending)
    }

    $waiting = $null
    foreach ($release in $route) {
        $canEnable = $release.prerequisite_versions -contains $currentRelease.version
        $fullMinimum = 0
        $enablementMinimum = 0
        if ($release.full_upgrade_prerequisite_builds -contains $current.build) {
            $fullMinimum = [int]$release.full_upgrade_minimum_ubr
        }
        if ($canEnable) { $enablementMinimum = [int]$release.minimum_ubr }
        $hold = $null
        # Prefer the smaller enablement package when the device already meets its requirements.
        $matching = $featureOffers | Where-Object { $_.release.version -eq $release.version } |
            Sort-Object -Property @{ Expression = 'enablement'; Descending = $true },
                                  @{ Expression = { $_.update.LastDeploymentChangeTime }; Descending = $true }
        foreach ($offer in $matching) {
            $reason = Get-MyPortalFeatureHold $offer.update $blocks $cutoff
            if ($reason) { $hold = $reason; continue }
            if ($offer.enablement -and (-not $canEnable -or $current.ubr -lt $enablementMinimum)) { continue }
            if (-not $offer.enablement -and $current.ubr -lt $fullMinimum) { continue }
            $message = if ($release.version -eq $target.version) { "Installing the $($target.version) feature update." }
                else { "Installing the $($release.version) prerequisite feature update first." }
            return @{ message = $message; update = $offer.update; feature = $true }
        }
        if ($hold) {
            if ($release.version -eq $target.version) {
                return @{ message = "The $($release.version) feature update is $hold." }
            }
            if (-not $waiting) { $waiting = "The $($release.version) prerequisite feature update is $hold." }
            continue
        }

        $minimumUbr = $fullMinimum
        $kb = $release.full_upgrade_prerequisite_kb
        if ($enablementMinimum -gt $minimumUbr) {
            $minimumUbr = $enablementMinimum
            $kb = $release.prerequisite_kb
        }
        if ($current.ubr -lt $minimumUbr) {
            $update = Find-MyPortalServicingUpdate $qualityOffers $current $minimumUbr $kb $blocks $cutoff
            if ($update) { return @{ message = "Installing the servicing prerequisite for $($release.version)."; update = $update } }
            if (-not $waiting) {
                $waiting = "Waiting for an applicable $kb or later cumulative update to reach build $($current.build).$minimumUbr for $($release.version)."
            }
        }
    }
    if (-not $waiting) {
        $waiting = "The configured Windows Update source offers no applicable feature update on the path to $($target.version) from build $($current.build). Check approvals or compatibility holds."
    }
    return @{ message = $waiting }
}

function Initialize-MyPortalFeatureCommit {
    if ('MyPortalFeatureUpdateCommit' -as [type]) { return }
    # Windows PowerShell can see a WUA type library without Commit in its
    # automation interface. Query the public IUpdateInstaller4 COM interface.
    # https://learn.microsoft.com/windows/win32/api/wuapi/nf-wuapi-iupdateinstaller4-commit
    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;

[ComImport, Guid("EF8208EA-2304-492D-9109-23813B0958E1"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IMyPortalUpdateInstaller4 {
    void _VtblGap0_29(); // IDispatch and the methods before Commit in wuapi.h.
    [PreserveSig]
    int Commit(uint flags);
}

public static class MyPortalFeatureUpdateCommit {
    public static void Commit(object installer) {
        Marshal.ThrowExceptionForHR(((IMyPortalUpdateInstaller4)installer).Commit(0));
    }
}
'@
}

function Get-MyPortalFeatureBootId {
    $boot = (Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime
    if (-not $boot) { throw 'Could not identify the current Windows boot session.' }
    return [string]$boot.ToUniversalTime().Ticks
}

function Get-MyPortalPendingFeature {
    $path = 'HKLM:\SOFTWARE\MyPortal\PatchManagement'
    $raw = (Get-ItemProperty $path -Name PendingFeature -ErrorAction SilentlyContinue).PendingFeature
    if (-not $raw) { return }
    $state = $raw | ConvertFrom-Json
    if ($state.boot -ne (Get-MyPortalFeatureBootId)) {
        Clear-MyPortalPendingFeature
        return
    }
    return $state
}

function Save-MyPortalPendingFeature($state) {
    $path = 'HKLM:\SOFTWARE\MyPortal\PatchManagement'
    if (-not (Test-Path $path)) { [void](New-Item $path -Force) }
    Set-ItemProperty $path -Name PendingFeature -Value ($state | ConvertTo-Json -Compress)
}

function Clear-MyPortalPendingFeature {
    Remove-ItemProperty 'HKLM:\SOFTWARE\MyPortal\PatchManagement' -Name PendingFeature -ErrorAction Stop
}

function Get-MyPortalPendingFeatureInstaller($state) {
    # A restart can become disallowed while setup is running. A later run must
    # bind the pending update to an installer before it can commit that update.
    $id = [string]$state.id
    $revision = [int]$state.revision
    if ($id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' -or $revision -lt 0) {
        throw 'The pending Windows feature update identity is invalid.'
    }
    $session = New-Object -ComObject Microsoft.Update.Session
    $session.ClientApplicationID = 'MyPortal Patch Management'
    $searcher = $session.CreateUpdateSearcher()
    $searcher.Online = $false
    $criteria = "UpdateID='$id' and RevisionNumber=$revision and RebootRequired=1"
    $found = Search-MyPortalFeatureOffers $searcher $criteria
    if ($found.ResultCode -ne 2) { throw "Could not verify the staged feature update (search result $($found.ResultCode))." }
    if ($found.Updates.Count -eq 0) { return }
    if ($found.Updates.Count -ne 1) { throw 'Windows Update returned multiple records for the exact staged feature update.' }
    $updates = New-Object -ComObject Microsoft.Update.UpdateColl
    [void]$updates.Add($found.Updates.Item(0))
    $installer = $session.CreateUpdateInstaller()
    $installer.Updates = $updates
    return $installer
}

function Get-MyPortalPendingFeatureHold($state, $feature, $blocks) {
    if (-not $state -or $state.stage -eq 'committed') { return }
    if ($state.target -ne $feature.target) { return 'A policy for the staged feature target must authorize its restart.' }
    $update = @{ Identity = @{ UpdateID = $state.id }; KBArticleIDs = $state.kbs }
    if (Test-MyPortalUpdateBlocked $update $blocks) { return 'The staged feature update is blocked in MyPortal.' }
}

function Invoke-MyPortalFeatureCommit($installer) {
    [MyPortalFeatureUpdateCommit]::Commit($installer)
}

function Complete-MyPortalPendingFeature($state, $installer) {
    if ($state.stage -eq 'committed') { return }
    if ($state.stage -eq 'commit_started') {
        throw 'A previous attempt to finalize the Windows feature update did not report success. Review Windows Update before restarting.'
    }
    if (-not $installer) { $installer = Get-MyPortalPendingFeatureInstaller $state }
    if (-not $installer) { throw 'Windows Update did not return the staged feature update for finalization.' }
    Initialize-MyPortalFeatureCommit
    # WUA requires at most one Commit for a prepared update before restarting.
    $state.stage = 'commit_started'
    Save-MyPortalPendingFeature $state
    Invoke-MyPortalFeatureCommit $installer
    $state.stage = 'committed'
    Save-MyPortalPendingFeature $state
    Write-Output "Windows feature update finalized for restart: $($state.title)"
}
