# Collects the Windows inventory as one JSON object (see windows.go).
# Each part runs on its own so a failing WMI class does not lose the rest.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$errors = New-Object System.Collections.Generic.List[string]
function Part([string]$Name, [scriptblock]$Block) {
    try { & $Block } catch { $errors.Add("${Name}: $($_.Exception.Message)"); $null }
}
function Iso($Value) { if ($Value) { ([datetime]$Value).ToUniversalTime().ToString('o') } else { $null } }

$cs = Part 'computer' { Get-CimInstance Win32_ComputerSystem }
$os = Part 'os' { Get-CimInstance Win32_OperatingSystem }
$bios = Part 'bios' { Get-CimInstance Win32_BIOS }
$current = Part 'os_version' { Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' }

$result = [ordered]@{
    manufacturer = $cs.Manufacturer
    model        = $cs.Model
    domain       = if ($cs.PartOfDomain) { $cs.Domain } else { $null }
    workgroup    = if ($cs -and -not $cs.PartOfDomain) { $cs.Domain } else { $null }
    ram_bytes    = $cs.TotalPhysicalMemory
    serial       = $bios.SerialNumber
    bios_version = $bios.SMBIOSBIOSVersion
    os_name      = $os.Caption
    os_version   = $os.Version
    display_version = $current.DisplayVersion
    build        = $os.BuildNumber
    ubr          = $current.UBR
    last_boot    = Iso $os.LastBootUpTime
    system_drive = $env:SystemDrive
}

$result.cpus = @(Part 'cpu' {
    Get-CimInstance Win32_Processor | ForEach-Object {
        [ordered]@{ name = $_.Name; cores = $_.NumberOfCores; logical = $_.NumberOfLogicalProcessors }
    }
})
$result.memory = @(Part 'memory' {
    Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
        [ordered]@{ slot = $_.DeviceLocator; size = $_.Capacity; speed = $(if ($_.ConfiguredClockSpeed) { $_.ConfiguredClockSpeed } else { $_.Speed })
                    manufacturer = $_.Manufacturer; part = $_.PartNumber }
    }
})
$result.volumes = @(Part 'volumes' {
    Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
        [ordered]@{ name = $_.DeviceID; label = $_.VolumeName; fs = $_.FileSystem; size = $_.Size; free = $_.FreeSpace }
    }
})
$result.drives = @(Part 'physical_disks' {
    if (Get-Command Get-PhysicalDisk -ErrorAction SilentlyContinue) {
        Get-PhysicalDisk | ForEach-Object {
            [ordered]@{ model = $_.FriendlyName; size = $_.Size; media = [string]$_.MediaType
                        bus = [string]$_.BusType; serial = $_.SerialNumber }
        }
    } else {
        Get-CimInstance Win32_DiskDrive | ForEach-Object {
            [ordered]@{ model = $_.Model; size = $_.Size; media = $_.MediaType; bus = $_.InterfaceType; serial = $_.SerialNumber }
        }
    }
})
$result.gpus = @(Part 'gpus' { Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name } })
$result.network = @(Part 'network' {
    Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | ForEach-Object {
        [ordered]@{ name = $_.Description; mac = $_.MACAddress; addresses = @($_.IPAddress) }
    }
})

$result.software = @(Part 'software' {
    $keys = @(
        @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'; Scope = 'machine' },
        @{ Path = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'; Scope = 'machine' }
    )
    # Per-user installs of every signed-in user (their hives are loaded).
    $keys += @(Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } | ForEach-Object {
            @{ Path = "Registry::HKEY_USERS\$($_.PSChildName)\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"; Scope = 'user' }
        })
    foreach ($key in $keys) {
        Get-ItemProperty $key.Path -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -and $_.SystemComponent -ne 1 -and -not $_.ParentKeyName -and -not $_.ReleaseType } |
            ForEach-Object {
                [ordered]@{ name = $_.DisplayName; version = [string]$_.DisplayVersion; publisher = $_.Publisher
                            install_date = [string]$_.InstallDate; scope = $key.Scope }
            }
    }
})

$result.errors = @($errors)
[Console]::Out.Write(($result | ConvertTo-Json -Depth 5 -Compress))
