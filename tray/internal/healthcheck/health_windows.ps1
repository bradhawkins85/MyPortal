# MyPortal health check snapshot (read-only). Runs as SYSTEM through the tray
# service and prints one JSON object: {"schema", "sections", "errors",
# "elapsed_ms"}. Every section is collected on its own so one failure never
# hides the rest. Nothing here changes the computer: it only reads CIM/WMI
# classes, the registry, event logs and the output of DISM /CheckHealth and
# the volume defragmentation analysis.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$days = 7
try { $days = [int]$env:MYPORTAL_HEALTH_DAYS } catch { $days = 7 }
if ($days -lt 1 -or $days -gt 90) { $days = 7 }
$since = (Get-Date).AddDays(-$days)
$since30 = (Get-Date).AddDays(-30)
$storageNs = 'root/Microsoft/Windows/Storage'
$result = [ordered]@{}
$errors = [ordered]@{}

function Add-Section([string]$Name, [scriptblock]$Body) {
    try { $result[$Name] = & $Body }
    catch { $errors[$Name] = Short ($_.Exception.Message) 300 }
}

function Short($Text, [int]$Max) {
    if ($null -eq $Text) { return $null }
    $value = ([string]$Text -replace '\s+', ' ').Trim()
    if ($value.Length -gt $Max) { return $value.Substring(0, $Max) }
    return $value
}

function Num($Value, [int]$Digits = 1) {
    if ($null -eq $Value) { return $null }
    try { return [math]::Round([double]$Value, $Digits) } catch { return $null }
}

function IsoDate($Value) {
    if ($null -eq $Value) { return $null }
    try {
        $date = [datetime]$Value
        if ($date.Year -lt 2000) { return $null }
        return $date.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    } catch { return $null }
}

function Get-Events([hashtable]$Filter, [int]$Max) {
    try { return @(Get-WinEvent -FilterHashtable $Filter -MaxEvents $Max -ErrorAction Stop) }
    catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*' -or $_.Exception.Message -like '*No events were found*') { return @() }
        throw
    }
}

function Count-Events([hashtable]$Filter, [int]$Max = 5000) {
    try { return @(Get-Events $Filter $Max).Count } catch { return $null }
}

function Format-Event($Event, [int]$MessageChars = 300) {
    [ordered]@{
        time = IsoDate $Event.TimeCreated
        id = $Event.Id
        provider = $Event.ProviderName
        level = $Event.LevelDisplayName
        message = Short $Event.Message $MessageChars
    }
}

function Recent-Events([hashtable]$Filter, [int]$Max = 5, [int]$MessageChars = 300) {
    # The comma keeps an empty or one-item list a list when it is returned.
    try { return ,@(Get-Events $Filter $Max | ForEach-Object { Format-Event $_ $MessageChars }) } catch { return ,@() }
}

# Get-WinEvent refuses a ProviderName that is not registered on this computer,
# so events from several possible providers are filtered after reading.
function Provider-Events([string]$LogName, [string[]]$Providers, $Start, [int[]]$Levels = @(), [int]$Max = 3000) {
    $filter = @{ LogName = $LogName; StartTime = $Start }
    if ($Levels.Count -gt 0) { $filter['Level'] = $Levels }
    try { return ,@(Get-Events $filter $Max | Where-Object { $Providers -contains $_.ProviderName }) } catch { return $null }
}

function Event-Data($Event) {
    $data = @{}
    try {
        $xml = [xml]$Event.ToXml()
        foreach ($item in @($xml.Event.EventData.Data)) {
            if ($item.Name) { $data[$item.Name] = $item.'#text' }
        }
    } catch { }
    return $data
}

function Map($Table, $Value) {
    if ($null -eq $Value) { return $null }
    $key = [int]$Value
    if ($Table.ContainsKey($key)) { return $Table[$key] }
    return [string]$Value
}

$HealthNames = @{ 0 = 'Healthy'; 1 = 'Warning'; 2 = 'Unhealthy'; 5 = 'Unknown' }
$MediaNames = @{ 0 = 'Unspecified'; 3 = 'HDD'; 4 = 'SSD'; 5 = 'SCM' }
$BusNames = @{ 1 = 'SCSI'; 2 = 'ATAPI'; 3 = 'ATA'; 4 = '1394'; 5 = 'SSA'; 6 = 'Fibre Channel'; 7 = 'USB'; 8 = 'RAID'; 9 = 'iSCSI'; 10 = 'SAS'; 11 = 'SATA'; 12 = 'SD'; 13 = 'MMC'; 15 = 'File Backed Virtual'; 16 = 'Storage Spaces'; 17 = 'NVMe' }
$OperationalNames = @{ 1 = 'Other'; 2 = 'OK'; 3 = 'Degraded'; 4 = 'Stressed'; 5 = 'Predictive Failure'; 6 = 'Error'; 7 = 'Non-Recoverable Error'; 8 = 'Starting'; 9 = 'Stopping'; 10 = 'Stopped'; 11 = 'In Service'; 12 = 'No Contact'; 13 = 'Lost Communication'; 14 = 'Aborted'; 15 = 'Dormant'; 16 = 'Supporting Entity in Error'; 17 = 'Completed'; 18 = 'Power Mode' }

$os = $null
try { $os = Get-CimInstance Win32_OperatingSystem } catch { }

Add-Section 'system' {
    $cs = Get-CimInstance Win32_ComputerSystem
    $bios = Get-CimInstance Win32_BIOS
    $enclosure = Get-CimInstance Win32_SystemEnclosure -ErrorAction SilentlyContinue | Select-Object -First 1
    $nt = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue
    [ordered]@{
        os = $os.Caption
        version = $os.Version
        build = $os.BuildNumber
        display_version = $nt.DisplayVersion
        architecture = $os.OSArchitecture
        install_date = IsoDate $os.InstallDate
        last_boot = IsoDate $os.LastBootUpTime
        uptime_hours = Num ((Get-Date) - $os.LastBootUpTime).TotalHours
        manufacturer = $cs.Manufacturer
        model = $cs.Model
        pc_system_type = $cs.PCSystemType
        hypervisor_present = $cs.HypervisorPresent
        bios_version = $bios.SMBIOSBIOSVersion
        bios_date = IsoDate $bios.ReleaseDate
        chassis_types = @($enclosure.ChassisTypes)
    }
}

Add-Section 'cpu' {
    $procs = @(Get-CimInstance Win32_Processor)
    $loads = @(); $dpc = @(); $interrupt = @()
    for ($i = 0; $i -lt 3; $i++) {
        $sample = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
        $loads += [double]$sample.PercentProcessorTime
        $dpc += [double]$sample.PercentDPCTime
        $interrupt += [double]$sample.PercentInterruptTime
        if ($i -lt 2) { Start-Sleep -Milliseconds 700 }
    }
    $info = Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation -Filter "Name='_Total'" -ErrorAction SilentlyContinue
    $system = Get-CimInstance Win32_PerfFormattedData_PerfOS_System -ErrorAction SilentlyContinue
    $first = $procs | Select-Object -First 1
    [ordered]@{
        name = Short $first.Name 120
        sockets = $procs.Count
        cores = ($procs | Measure-Object NumberOfCores -Sum).Sum
        logical_processors = ($procs | Measure-Object NumberOfLogicalProcessors -Sum).Sum
        max_clock_mhz = $first.MaxClockSpeed
        current_clock_mhz = $first.CurrentClockSpeed
        load_percent = Num (($loads | Measure-Object -Average).Average)
        dpc_percent = Num (($dpc | Measure-Object -Average).Average)
        interrupt_percent = Num (($interrupt | Measure-Object -Average).Average)
        percent_of_max_frequency = Num $info.PercentofMaximumFrequency
        percent_performance_limit = Num $info.PercentPerformanceLimit
        processor_queue_length = Num $system.ProcessorQueueLength
        processes = Num $system.Processes 0
        threads = Num $system.Threads 0
    }
}

Add-Section 'memory' {
    $perf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    $modules = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue)
    $pageFiles = @(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue)
    [ordered]@{
        total_mb = Num ($os.TotalVisibleMemorySize / 1024) 0
        free_mb = Num ($os.FreePhysicalMemory / 1024) 0
        used_percent = Num (100 * (1 - $os.FreePhysicalMemory / $os.TotalVisibleMemorySize))
        commit_percent = Num $perf.PercentCommittedBytesInUse
        committed_mb = Num ($perf.CommittedBytes / 1MB) 0
        commit_limit_mb = Num ($perf.CommitLimit / 1MB) 0
        pages_per_sec = Num $perf.PagesPersec 0
        page_faults_per_sec = Num $perf.PageFaultsPersec 0
        pool_nonpaged_mb = Num ($perf.PoolNonpagedBytes / 1MB) 0
        modules = @($modules | ForEach-Object {
            [ordered]@{
                slot = $_.DeviceLocator
                capacity_gb = Num ($_.Capacity / 1GB)
                speed_mhz = $_.Speed
                configured_speed_mhz = $_.ConfiguredClockSpeed
                manufacturer = Short $_.Manufacturer 60
                part_number = Short $_.PartNumber 60
            }
        })
        page_files = @($pageFiles | ForEach-Object {
            [ordered]@{ name = $_.Name; allocated_mb = $_.AllocatedBaseSize; current_usage_mb = $_.CurrentUsage; peak_usage_mb = $_.PeakUsage }
        })
    }
}

$partitions = @()
try { $partitions = @(Get-CimInstance -Namespace $storageNs -ClassName MSFT_Partition | Where-Object { $_.DriveLetter -and [int][char]$_.DriveLetter -ne 0 }) } catch { }
$physicalDisks = @()
try { $physicalDisks = @(Get-CimInstance -Namespace $storageNs -ClassName MSFT_PhysicalDisk) } catch { $errors['physical_disks'] = Short ($_.Exception.Message) 300 }

Add-Section 'volumes' {
    $msftVolumes = @(Get-CimInstance -Namespace $storageNs -ClassName MSFT_Volume -ErrorAction SilentlyContinue)
    ,@(Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object { $_.DriveLetter } | ForEach-Object {
        $letter = ([string]$_.DriveLetter).TrimEnd(':')
        $partition = $partitions | Where-Object { [string]$_.DriveLetter -eq $letter } | Select-Object -First 1
        $msft = $msftVolumes | Where-Object { [string]$_.DriveLetter -eq $letter } | Select-Object -First 1
        $freePercent = $null
        if ($_.Capacity) { $freePercent = Num (100 * $_.FreeSpace / $_.Capacity) }
        $disk = $null
        if ($partition) { $disk = $physicalDisks | Where-Object { [string]$_.DeviceId -eq [string]$partition.DiskNumber } | Select-Object -First 1 }
        [ordered]@{
            drive = $_.DriveLetter
            label = Short $_.Label 60
            file_system = $_.FileSystem
            size_gb = Num ($_.Capacity / 1GB)
            free_gb = Num ($_.FreeSpace / 1GB)
            free_percent = $freePercent
            dirty = [bool]$_.DirtyBitSet
            health = Map $HealthNames $msft.HealthStatus
            boot_volume = [bool]$_.BootVolume
            disk_number = $partition.DiskNumber
            media_type = Map $MediaNames $disk.MediaType
        }
    })
}

Add-Section 'disks' {
    $drives = @(Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue)
    ,@($physicalDisks | ForEach-Object {
        $disk = $_
        $counter = $null
        try { $counter = Get-CimAssociatedInstance -InputObject $disk -ResultClassName MSFT_StorageReliabilityCounter -ErrorAction Stop | Select-Object -First 1 } catch { }
        $drive = $drives | Where-Object { [string]$_.Index -eq [string]$disk.DeviceId } | Select-Object -First 1
        [ordered]@{
            device_id = $disk.DeviceId
            name = Short $disk.FriendlyName 100
            media_type = Map $MediaNames $disk.MediaType
            bus_type = Map $BusNames $disk.BusType
            size_gb = Num ($disk.Size / 1GB)
            health = Map $HealthNames $disk.HealthStatus
            operational_status = @($disk.OperationalStatus | ForEach-Object { Map $OperationalNames $_ })
            firmware = $disk.FirmwareVersion
            drive_status = $drive.Status
            reliability_counters = [bool]$counter
            temperature_c = Num $counter.Temperature 0
            temperature_max_c = Num $counter.TemperatureMax 0
            wear_percent = Num $counter.Wear 0
            power_on_hours = Num $counter.PowerOnHours 0
            start_stop_count = Num $counter.StartStopCycleCount 0
            read_errors_total = Num $counter.ReadErrorsTotal 0
            read_errors_corrected = Num $counter.ReadErrorsCorrected 0
            read_errors_uncorrected = Num $counter.ReadErrorsUncorrected 0
            write_errors_total = Num $counter.WriteErrorsTotal 0
            write_errors_uncorrected = Num $counter.WriteErrorsUncorrected 0
            read_latency_max_ms = Num $counter.ReadLatencyMax 0
            write_latency_max_ms = Num $counter.WriteLatencyMax 0
        }
    })
}

Add-Section 'smart_predictions' {
    ,@(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction Stop | ForEach-Object {
        [ordered]@{ instance = Short $_.InstanceName 160; predict_failure = [bool]$_.PredictFailure; reason = $_.Reason }
    })
}

Add-Section 'chkdsk' {
    $boot = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name BootExecute -ErrorAction SilentlyContinue).BootExecute
    $bootText = (@($boot) -join '; ')
    $ntfs = Provider-Events 'System' @('Ntfs', 'Microsoft-Windows-Ntfs') $since @(1, 2, 3)
    $diskEvents = Provider-Events 'System' @('disk', 'storahci', 'stornvme', 'iaStorA', 'iaStorAC', 'iaStorAVC', 'volmgr') $since @(1, 2, 3)
    $checks = Provider-Events 'Application' @('Microsoft-Windows-Wininit', 'Chkdsk') (Get-Date).AddDays(-180) @() 2000
    [ordered]@{
        boot_execute = Short $bootText 300
        scheduled = ($bootText -match 'autochk' -and $bootText -notmatch '^autocheck autochk \*$' -and $bootText -match '\\\?\?\\|/p|/r')
        dirty_volumes = @(Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object { $_.DirtyBitSet -and $_.DriveLetter } | ForEach-Object { $_.DriveLetter })
        ntfs_errors = if ($null -eq $ntfs) { $null } else { $ntfs.Count }
        disk_errors = if ($null -eq $diskEvents) { $null } else { $diskEvents.Count }
        recent_disk_errors = @(@($diskEvents) + @($ntfs) | Where-Object { $_ } | Sort-Object TimeCreated -Descending | Select-Object -First 5 | ForEach-Object { Format-Event $_ })
        recent_checks = @(@($checks) | Where-Object { $_ } | Select-Object -First 3 | ForEach-Object { Format-Event $_ 800 })
        window_days = $days
    }
}

Add-Section 'defrag' {
    $task = $null
    try {
        $service = New-Object -ComObject Schedule.Service
        $service.Connect()
        $scheduled = $service.GetFolder('\Microsoft\Windows\Defrag').GetTask('ScheduledDefrag')
        $task = [ordered]@{ enabled = [bool]$scheduled.Enabled; state = $scheduled.State; last_run = IsoDate $scheduled.LastRunTime; last_result = $scheduled.LastTaskResult; next_run = IsoDate $scheduled.NextRunTime }
    } catch { $task = [ordered]@{ error = Short ($_.Exception.Message) 200 } }
    $trim = $null
    try {
        $notify = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name DisableDeleteNotification -ErrorAction Stop).DisableDeleteNotification
        $trim = ([int]$notify -eq 0)
    } catch { }
    $analysis = @()
    $hddDisks = @($physicalDisks | Where-Object { [int]$_.MediaType -eq 3 } | ForEach-Object { [string]$_.DeviceId })
    if ($hddDisks.Count -gt 0 -and $env:MYPORTAL_HEALTH_DEFRAG_ANALYSIS -ne '0') {
        $letters = @($partitions | Where-Object { $hddDisks -contains [string]$_.DiskNumber } | ForEach-Object { ([string]$_.DriveLetter) + ':' })
        if ($letters.Count -gt 0) {
            # The analysis is read-only but slow on large disks, so it runs in a
            # job that is stopped after a few minutes.
            $job = Start-Job -ArgumentList (, $letters) -ScriptBlock {
                param($Drives)
                foreach ($volume in @(Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object { $Drives -contains $_.DriveLetter })) {
                    try {
                        $r = Invoke-CimMethod -InputObject $volume -MethodName DefragAnalysis -ErrorAction Stop
                        [pscustomobject]@{ drive = $volume.DriveLetter; defrag_recommended = [bool]$r.DefragRecommended; return_value = $r.ReturnValue; total_fragmentation_percent = $r.DefragAnalysis.TotalPercentFragmentation; file_fragmentation_percent = $r.DefragAnalysis.FilePercentFragmentation; free_space_fragmentation_percent = $r.DefragAnalysis.FreeSpacePercentFragmentation }
                    } catch { [pscustomobject]@{ drive = $volume.DriveLetter; error = [string]$_.Exception.Message } }
                }
            }
            if (Wait-Job $job -Timeout 180) {
                $analysis = @(Receive-Job $job | ForEach-Object {
                    [ordered]@{ drive = $_.drive; defrag_recommended = $_.defrag_recommended; return_value = $_.return_value; total_fragmentation_percent = $_.total_fragmentation_percent; file_fragmentation_percent = $_.file_fragmentation_percent; free_space_fragmentation_percent = $_.free_space_fragmentation_percent; error = Short $_.error 200 }
                })
            } else {
                Stop-Job $job
                $analysis = @([ordered]@{ error = 'The fragmentation analysis did not finish within 3 minutes.' })
            }
            Remove-Job $job -Force
        }
    }
    [ordered]@{
        scheduled_task = $task
        trim_enabled = $trim
        hdd_volumes = @($partitions | Where-Object { $hddDisks -contains [string]$_.DiskNumber } | ForEach-Object { ([string]$_.DriveLetter) + ':' })
        analysis = $analysis
        recent_runs = Recent-Events @{ LogName = 'Application'; ProviderName = 'Microsoft-Windows-Defrag'; StartTime = (Get-Date).AddDays(-60) } 6 200
    }
}

Add-Section 'thermal' {
    $zones = @()
    try {
        $zones += @(Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation -ErrorAction Stop | ForEach-Object {
            $kelvinTenths = $_.HighPrecisionTemperature
            if (-not $kelvinTenths) { $kelvinTenths = 10 * $_.Temperature }
            [ordered]@{ zone = Short $_.Name 80; celsius = Num ($kelvinTenths / 10 - 273.15); passive_limit_percent = $_.PercentPassiveLimit; throttle_reasons = $_.ThrottleReasons; source = 'performance counter' }
        })
    } catch { }
    if ($zones.Count -eq 0) {
        try {
            $zones += @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | ForEach-Object {
                $critical = $null
                if ($_.CriticalTripPoint) { $critical = Num ($_.CriticalTripPoint / 10 - 273.15) }
                [ordered]@{ zone = Short $_.InstanceName 80; celsius = Num ($_.CurrentTemperature / 10 - 273.15); critical_celsius = $critical; source = 'ACPI' }
            })
        } catch { }
    }
    [ordered]@{
        zones = $zones
        thermal_events = Count-Events @{ LogName = 'Microsoft-Windows-Kernel-Power/Thermal-Operational'; StartTime = $since }
        recent_thermal_events = Recent-Events @{ LogName = 'Microsoft-Windows-Kernel-Power/Thermal-Operational'; StartTime = $since } 5
        window_days = $days
    }
}

Add-Section 'performance' {
    $bootEvents = @(Get-Events @{ LogName = 'Microsoft-Windows-Diagnostics-Performance/Operational'; Id = 100; StartTime = $since30 } 10)
    $boots = @($bootEvents | ForEach-Object {
        $data = Event-Data $_
        [ordered]@{ time = IsoDate $_.TimeCreated; boot_ms = Num $data['BootTime'] 0; main_path_ms = Num $data['MainPathBootTime'] 0; post_boot_ms = Num $data['BootPostBootTime'] 0 }
    })
    $degradations = @()
    try {
        $degradations = @(Get-Events @{ LogName = 'Microsoft-Windows-Diagnostics-Performance/Operational'; StartTime = $since30 } 300 |
            Where-Object { $_.Id -ne 100 -and $_.Id -ne 200 } | Select-Object -First 15 | ForEach-Object {
                [ordered]@{ time = IsoDate $_.TimeCreated; id = $_.Id; message = Short $_.Message 300 }
            })
    } catch { }
    $logical = [double]((Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors)
    if ($logical -lt 1) { $logical = 1 }
    $processes = @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne '_Total' -and $_.Name -ne 'Idle' })
    $stability = $null
    try { $stability = Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction Stop | Sort-Object TimeGenerated -Descending | Select-Object -First 1 } catch { }
    $disk = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'" -ErrorAction SilentlyContinue
    [ordered]@{
        boots = $boots
        degradations = $degradations
        stability_index = Num $stability.SystemStabilityIndex
        disk_busy_percent = Num (100 - $disk.PercentIdleTime)
        disk_queue_length = Num $disk.CurrentDiskQueueLength
        top_cpu = @($processes | Sort-Object PercentProcessorTime -Descending | Select-Object -First 8 | ForEach-Object {
            [ordered]@{ name = $_.Name; cpu_percent = Num ($_.PercentProcessorTime / $logical); private_mb = Num ($_.WorkingSetPrivate / 1MB) 0 }
        })
        top_memory = @($processes | Sort-Object WorkingSetPrivate -Descending | Select-Object -First 8 | ForEach-Object {
            [ordered]@{ name = $_.Name; private_mb = Num ($_.WorkingSetPrivate / 1MB) 0; handles = $_.HandleCount }
        })
        startup_items = @(Get-CimInstance Win32_StartupCommand -ErrorAction SilentlyContinue | ForEach-Object { Short $_.Name 80 } | Select-Object -Unique -First 40)
    }
}

Add-Section 'stability' {
    $crashEvents = @(Get-Events @{ LogName = 'Application'; ProviderName = 'Application Error'; Id = 1000; StartTime = $since30 } 500)
    $topCrashes = @($crashEvents | Group-Object { try { [string]$_.Properties[0].Value } catch { 'unknown' } } | Sort-Object Count -Descending | Select-Object -First 5 | ForEach-Object {
        [ordered]@{ app = Short $_.Name 80; crashes = $_.Count }
    })
    [ordered]@{
        window_days = 30
        bugcheck_count = Count-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = $since30 }
        bugchecks = Recent-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = $since30 } 5
        unexpected_shutdowns = Count-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $since30 }
        recent_unexpected_shutdowns = Recent-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $since30 } 3
        dirty_shutdowns = Count-Events @{ LogName = 'System'; Id = 6008; StartTime = $since30 }
        whea_errors = Count-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; StartTime = $since30 }
        recent_whea = Recent-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; StartTime = $since30 } 5
        app_crashes = $crashEvents.Count
        top_crashing_apps = $topCrashes
        app_hangs = Count-Events @{ LogName = 'Application'; ProviderName = 'Application Hang'; Id = 1002; StartTime = $since30 }
        service_crashes = Count-Events @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = @(7031, 7034); StartTime = $since30 }
        memory_diagnostics = Recent-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-MemoryDiagnostics-Results'; StartTime = (Get-Date).AddDays(-365) } 2
    }
}

Add-Section 'drivers' {
    $problems = @(Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode <> 0 AND ConfigManagerErrorCode <> 45' -ErrorAction Stop)
    $signed = @(Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue | Where-Object { @('DISPLAY', 'NET', 'SCSIADAPTER', 'HDC', 'BLUETOOTH') -contains ([string]$_.DeviceClass).ToUpper() -and $_.DeviceName })
    [ordered]@{
        problem_count = $problems.Count
        problem_devices = @($problems | Select-Object -First 25 | ForEach-Object {
            [ordered]@{ name = Short $_.Name 120; class = $_.PNPClass; error_code = $_.ConfigManagerErrorCode; status = $_.Status }
        })
        key_drivers = @($signed | Select-Object -First 25 | ForEach-Object {
            [ordered]@{ device = Short $_.DeviceName 120; class = $_.DeviceClass; version = $_.DriverVersion; date = IsoDate $_.DriverDate; provider = Short $_.DriverProviderName 60 }
        })
    }
}

Add-Section 'services' {
    $stopped = @(Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" -ErrorAction Stop)
    [ordered]@{
        stopped_automatic = @($stopped | Select-Object -First 30 | ForEach-Object {
            [ordered]@{ name = $_.Name; display_name = Short $_.DisplayName 100; state = $_.State; exit_code = $_.ExitCode }
        })
        stopped_automatic_count = $stopped.Count
    }
}

Add-Section 'updates' {
    $hotfixes = @(Get-HotFix -ErrorAction SilentlyContinue | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending)
    $last = $null
    if ($hotfixes.Count -gt 0) { $last = [ordered]@{ id = $hotfixes[0].HotFixID; installed_on = IsoDate $hotfixes[0].InstalledOn } }
    [ordered]@{
        reboot_pending = [ordered]@{
            component_based_servicing = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
            windows_update = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
            pending_file_rename = [bool](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
        }
        last_hotfix = $last
        hotfix_count = $hotfixes.Count
        windows_update_service = (Get-CimInstance Win32_Service -Filter "Name='wuauserv'" -ErrorAction SilentlyContinue).StartMode
        failed_updates = Count-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WindowsUpdateClient'; Id = 20; StartTime = $since30 }
        recent_failed_updates = Recent-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WindowsUpdateClient'; Id = 20; StartTime = $since30 } 3
    }
}

Add-Section 'security' {
    $mp = $null
    try { $mp = Get-CimInstance -Namespace root/Microsoft/Windows/Defender -ClassName MSFT_MpComputerStatus -ErrorAction Stop } catch { }
    $defender = $null
    if ($mp) {
        $defender = [ordered]@{ antivirus_enabled = $mp.AntivirusEnabled; realtime = $mp.RealTimeProtectionEnabled; signature_age_days = $mp.AntivirusSignatureAge; quick_scan_age_days = $mp.QuickScanAge; full_scan_age_days = $mp.FullScanAge; tamper_protected = $mp.IsTamperProtected; running_mode = $mp.AMRunningMode }
    }
    $secureBoot = $null
    try { $secureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { }
    $tpm = $null
    try {
        $t = Get-CimInstance -Namespace root/cimv2/Security/MicrosoftTpm -ClassName Win32_Tpm -ErrorAction Stop
        if ($t) { $tpm = [ordered]@{ enabled = $t.IsEnabled_InitialValue; activated = $t.IsActivated_InitialValue; version = Short $t.SpecVersion 40 } }
    } catch { }
    [ordered]@{
        defender = $defender
        antivirus_products = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ name = Short $_.displayName 80; state = ('0x{0:X6}' -f [int]$_.productState) } })
        firewall = @(Get-CimInstance -Namespace root/StandardCimv2 -ClassName MSFT_NetFirewallProfile -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ profile = $_.Name; enabled = ([int]$_.Enabled -eq 1) } })
        bitlocker = @(Get-CimInstance -Namespace root/cimv2/Security/MicrosoftVolumeEncryption -ClassName Win32_EncryptableVolume -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ drive = $_.DriveLetter; protection_on = ([int]$_.ProtectionStatus -eq 1) } })
        secure_boot = $secureBoot
        tpm = $tpm
    }
}

Add-Section 'battery' {
    $batteries = @(Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)
    if ($batteries.Count -eq 0) { return [ordered]@{ present = $false } }
    $design = (@(Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData -ErrorAction SilentlyContinue) | Measure-Object DesignedCapacity -Sum).Sum
    $full = (@(Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue) | Measure-Object FullChargedCapacity -Sum).Sum
    $cycles = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount -ErrorAction SilentlyContinue) | Select-Object -First 1
    $health = $null
    if ($design -gt 0 -and $full -gt 0) { $health = Num (100 * $full / $design) }
    [ordered]@{
        present = $true
        charge_percent = $batteries[0].EstimatedChargeRemaining
        status = $batteries[0].BatteryStatus
        design_capacity_mwh = $design
        full_charge_capacity_mwh = $full
        health_percent = $health
        cycle_count = $cycles.CycleCount
    }
}

Add-Section 'network' {
    $adapters = @(Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True AND NetEnabled=True' -ErrorAction Stop)
    $interfaces = @(Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface -ErrorAction SilentlyContinue)
    [ordered]@{
        adapters = @($adapters | ForEach-Object { [ordered]@{ name = Short $_.Name 100; speed_mbps = Num ($_.Speed / 1000000) 0; status = $_.NetConnectionStatus } })
        interface_errors = @($interfaces | Where-Object { $_.PacketsReceivedErrors -or $_.PacketsOutboundErrors -or $_.PacketsReceivedDiscarded -or $_.PacketsOutboundDiscarded } | Select-Object -First 10 | ForEach-Object {
            [ordered]@{ name = Short $_.Name 100; received_errors = $_.PacketsReceivedErrors; outbound_errors = $_.PacketsOutboundErrors; received_discarded = $_.PacketsReceivedDiscarded; outbound_discarded = $_.PacketsOutboundDiscarded }
        })
    }
}

Add-Section 'power' {
    $plan = Get-CimInstance -Namespace root/cimv2/power -ClassName Win32_PowerPlan -Filter 'IsActive=True' -ErrorAction SilentlyContinue | Select-Object -First 1
    [ordered]@{
        active_plan = $plan.ElementName
        fast_startup = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
    }
}

Add-Section 'component_store' {
    $dism = Join-Path $env:SystemRoot 'System32\dism.exe'
    $info = New-Object System.Diagnostics.ProcessStartInfo $dism, '/English /Online /Cleanup-Image /CheckHealth'
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $false
    $info.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $output = $process.StandardOutput.ReadToEndAsync()
    if (-not $process.WaitForExit(120000)) {
        try { $process.Kill() } catch { }
        return [ordered]@{ state = 'unknown'; detail = 'DISM /CheckHealth did not finish within 2 minutes.' }
    }
    $text = [string]$output.Result
    $state = 'unknown'
    if ($text -match 'No component store corruption detected') { $state = 'healthy' }
    elseif ($text -match 'not repairable') { $state = 'not_repairable' }
    elseif ($text -match 'is repairable') { $state = 'repairable' }
    $line = @($text -split "`r?`n" | Where-Object { $_ -match 'component store' } | Select-Object -First 1)
    [ordered]@{ state = $state; exit_code = $process.ExitCode; detail = Short ($line -join ' ') 300 }
}

$output = [ordered]@{ schema = 1; sections = $result; errors = $errors; elapsed_ms = $sw.ElapsedMilliseconds }
ConvertTo-Json -InputObject $output -Depth 8 -Compress
