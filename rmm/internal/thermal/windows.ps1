$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -Path $env:MYPORTAL_THERMAL_DLL
$computer = New-Object LibreHardwareMonitor.Hardware.Computer
$computer.IsCpuEnabled = $true
$computer.IsGpuEnabled = $true
$computer.IsMotherboardEnabled = $true
$computer.IsMemoryEnabled = $true
$computer.IsStorageEnabled = $true
$computer.IsControllerEnabled = $true
$computer.IsBatteryEnabled = $true
$computer.IsNetworkEnabled = $true
$computer.IsPsuEnabled = $true
$computer.IsPowerMonitorEnabled = $true
$readings = New-Object 'System.Collections.Generic.List[object]'
function Read-Hardware($hardware) {
    try {
        $hardware.Update()
        foreach ($sensor in $hardware.Sensors) {
            if ($readings.Count -ge 256) { break }
            if ($sensor.SensorType.ToString() -eq 'Temperature' -and $null -ne $sensor.Value) {
                $readings.Add(@{id=$sensor.Identifier.ToString(); name=$sensor.Name; hardware=$hardware.Name; celsius=[double]$sensor.Value})
            }
        }
    } catch { }
    foreach ($child in $hardware.SubHardware) { Read-Hardware $child }
}
try {
    $computer.Open()
    foreach ($hardware in $computer.Hardware) { Read-Hardware $hardware }
    ConvertTo-Json -InputObject @($readings.ToArray()) -Depth 4 -Compress
} finally {
    $computer.Close()
}
