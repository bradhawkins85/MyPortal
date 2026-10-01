$ErrorActionPreference = 'Stop'

# Do not set [Console]::OutputEncoding here. The scanner is launched by the
# Windows service without an attached console, so accessing that property can
# throw "The handle is invalid" before discovery has even started. The JSON
# emitted below is ASCII-safe (ConvertTo-Json escapes non-ASCII characters) and
# is read directly from PowerShell's redirected stdout by the service.

# A deliberately bounded, dependency-free approximation of a basic Nmap scan.
# Every connected network is limited to its local /24 to avoid unexpectedly
# probing very large corporate or VPN address ranges.
$serviceNames = @{
    22 = 'ssh'; 25 = 'smtp'; 53 = 'domain'; 80 = 'http'; 110 = 'pop3'
    135 = 'msrpc'; 139 = 'netbios-ssn'; 143 = 'imap'; 443 = 'https'
    445 = 'microsoft-ds'; 3389 = 'ms-wbt-server'; 5985 = 'wsman'; 5986 = 'wsmans'
    8080 = 'http-proxy'; 8443 = 'https-alt'
}
$ports = @($serviceNames.Keys | Sort-Object)
$legacyWindows = [Environment]::OSVersion.Version.Major -lt 10
$localMacAddresses = @{}
if ($legacyWindows) {
    $localAdapters = @(Get-WmiObject -Class Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = True' -ErrorAction Stop)
    $localAddresses = @($localAdapters | ForEach-Object {
        $adapter = $_
        foreach ($adapterAddress in $adapter.IPAddress) {
            if ($adapterAddress -match '^\d{1,3}(\.\d{1,3}){3}$' -and $adapterAddress -notlike '127.*') {
                if ($adapter.MACAddress) { $localMacAddresses[$adapterAddress] = $adapter.MACAddress }
                $adapterAddress
            }
        }
    })
} else {
    $localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.InterfaceAlias -notmatch 'Loopback' } |
        ForEach-Object {
            $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
            if ($null -ne $adapter -and $adapter.MacAddress) { $localMacAddresses[$_.IPAddress] = $adapter.MacAddress }
            $_.IPAddress
        })
}
if ($localAddresses.Count -eq 0) { throw 'No connected IPv4 subnet found' }

$targets = New-Object 'Collections.Generic.HashSet[string]'
foreach ($cidr in ($env:MYPORTAL_SCAN_TARGETS -split ',')) {
    if ($cidr -notmatch '^(.+)/(\d+)$') { continue }
    $bytes = [Net.IPAddress]::Parse($matches[1]).GetAddressBytes()
    [Array]::Reverse($bytes)
    $address = [BitConverter]::ToUInt32($bytes, 0)
    $prefixLength = [int]$matches[2]
    $mask = [uint32]::MaxValue -shl (32 - $prefixLength)
    $first = ($address -band $mask)
    $last = ($address -bor (-bnot $mask))
    if ($prefixLength -lt 31) { $first++; $last-- }
    foreach ($value in $first..$last) {
        $hostBytes = [BitConverter]::GetBytes([uint32]$value)
        [Array]::Reverse($hostBytes)
        [void]$targets.Add((New-Object Net.IPAddress (,$hostBytes)).ToString())
    }
}

$results = New-Object 'Collections.Generic.List[object]'
$ping = New-Object Net.NetworkInformation.Ping
foreach ($ip in $targets) {
    $reply = $null
    try { $reply = $ping.Send($ip, 150) } catch { }
    $openPorts = New-Object 'Collections.Generic.List[string]'
    $connections = New-Object 'Collections.Generic.List[object]'
    foreach ($port in $ports) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $connect = $client.BeginConnect($ip, $port, $null, $null)
            $connections.Add([pscustomobject]@{ Port = $port; Client = $client; Connect = $connect })
        } catch { $client.Dispose() }
    }
    # Start every connection first so closed/filtered ports share one timeout
    # instead of making a host scan take timeout multiplied by port count.
    foreach ($connection in $connections) {
        try {
            if ($connection.Connect.AsyncWaitHandle.WaitOne(100) -and $connection.Client.Connected) {
                $connection.Client.EndConnect($connection.Connect)
                $openPorts.Add(('{0}/tcp {1}' -f $connection.Port, $serviceNames[$connection.Port]))
            }
        } catch { } finally { $connection.Client.Dispose() }
    }
    $macAddress = ''
    if ($localMacAddresses.ContainsKey($ip)) {
        # A host does not have an ARP/neighbor entry for itself, so obtain the
        # scanner's MAC directly from the interface that owns its local IP.
        $macAddress = $localMacAddresses[$ip]
    } elseif ($legacyWindows) {
        # The NetTCPIP module can fail to load on Windows Server 2012 R2. Use
        # the inbox WMI and arp.exe interfaces only on these older systems.
        $escapedIP = [Regex]::Escape($ip)
        foreach ($line in (& "$env:SystemRoot\System32\arp.exe" -a $ip 2>$null)) {
            if ($line -match ("^\s*{0}\s+([0-9a-fA-F]{{2}}(?:(?:-|:)?[0-9a-fA-F]{{2}}){{5}})\s+" -f $escapedIP)) {
                $macAddress = $matches[1]
                break
            }
        }
    } else {
        $neighbor = Get-NetNeighbor -IPAddress $ip -ErrorAction SilentlyContinue |
            Where-Object { $_.State -ne 'Unreachable' -and $_.LinkLayerAddress } | Select-Object -First 1
        if ($null -ne $neighbor) { $macAddress = $neighbor.LinkLayerAddress }
    }
    if ($macAddress) {
        $macHex = $macAddress -replace '[^0-9a-fA-F]', ''
        if ($macHex.Length -eq 12) {
            $macAddress = (([Regex]::Matches($macHex, '.{2}') | ForEach-Object { $_.Value }) -join ':').ToUpperInvariant()
        }
    }
    if (($null -eq $reply -or $reply.Status -ne 'Success') -and $openPorts.Count -eq 0 -and -not $macAddress) { continue }
    $hostname = ''
    try { $hostname = [Net.Dns]::GetHostEntry($ip).HostName } catch { }
    $os = ''
    if ($null -ne $reply -and $reply.Status -eq 'Success') {
        if ($reply.Options.Ttl -le 64) { $os = 'Unix-like (TTL estimate)' }
        elseif ($reply.Options.Ttl -le 128) { $os = 'Windows (TTL estimate)' }
        else { $os = 'Network device (TTL estimate)' }
    }
    $results.Add([pscustomobject]@{
        ip_address = $ip
        mac_address = $macAddress
        hostname = $hostname
        vendor = ''
        os_details = $os
        open_ports = $openPorts -join ', '
    })
}
$ping.Dispose()
if ($results.Count -eq 0) {
    # Windows PowerShell can emit no stdout when an empty collection reaches
    # ConvertTo-Json. Always preserve the scanner's JSON output contract.
    Write-Output '[]'
} else {
    # Windows PowerShell 5.1's dynamic binder can throw "Argument types do not
    # match" when an array subexpression enumerates List[object]. Convert the
    # generic list explicitly while retaining an array for the single-host case.
    $resultArray = $results.ToArray()
    ConvertTo-Json -InputObject $resultArray -Compress -Depth 3
}
