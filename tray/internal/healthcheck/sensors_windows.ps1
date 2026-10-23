# MyPortal health check hardware sensors (read-only). Loads the bundled
# LibreHardwareMonitor library from MYPORTAL_SENSOR_DLL and prints a JSON list
# of sensor readings. CPU sensors need the PawnIO driver, which the tray
# service installs before this runs and removes after it exits.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -Path $env:MYPORTAL_SENSOR_DLL

$units = @{ Temperature = 'C'; Fan = 'RPM'; Voltage = 'V'; Current = 'A'; Load = '%'; Clock = 'MHz'; Power = 'W'; Level = '%'; Control = '%' }
$readings = New-Object 'System.Collections.Generic.List[object]'

function Round($Value) {
    if ($null -eq $Value) { return $null }
    try {
        $number = [double]$Value
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) { return $null }
        return [math]::Round($number, 2)
    } catch { return $null }
}

function Read-Hardware($Hardware) {
    try {
        $Hardware.Update()
        foreach ($sensor in $Hardware.Sensors) {
            if ($readings.Count -ge 400) { break }
            $type = $sensor.SensorType.ToString()
            if (-not $units.ContainsKey($type)) { continue }
            $value = Round $sensor.Value
            if ($null -eq $value) { continue }
            $readings.Add([ordered]@{
                hardware_type = $Hardware.HardwareType.ToString()
                hardware = [string]$Hardware.Name
                type = $type
                name = [string]$sensor.Name
                value = $value
                min = Round $sensor.Min
                max = Round $sensor.Max
                unit = $units[$type]
            })
        }
    } catch { }
    foreach ($child in $Hardware.SubHardware) { Read-Hardware $child }
}

$computer = New-Object LibreHardwareMonitor.Hardware.Computer
$computer.IsCpuEnabled = $true
$computer.IsGpuEnabled = $true
$computer.IsMotherboardEnabled = $true
$computer.IsMemoryEnabled = $true
$computer.IsStorageEnabled = $true
$computer.IsControllerEnabled = $true
$computer.IsBatteryEnabled = $true
$computer.IsPsuEnabled = $true
try {
    $computer.Open()
    # The first update primes load and clock sensors; read on the second.
    foreach ($hardware in $computer.Hardware) { try { $hardware.Update() } catch { } }
    Start-Sleep -Milliseconds 1000
    foreach ($hardware in $computer.Hardware) { Read-Hardware $hardware }
} finally {
    $computer.Close()
}
ConvertTo-Json -InputObject @($readings.ToArray()) -Depth 4 -Compress
