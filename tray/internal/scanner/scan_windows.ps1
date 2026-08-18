$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

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
$localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.InterfaceAlias -notmatch 'Loopback' })
if ($localAddresses.Count -eq 0) { throw 'No connected IPv4 subnet found' }

$targets = [Collections.Generic.HashSet[string]]::new()
foreach ($address in $localAddresses) {
    $octets = $address.IPAddress.Split('.')
    $prefix = '{0}.{1}.{2}' -f $octets[0], $octets[1], $octets[2]
    foreach ($hostNumber in 1..254) { [void]$targets.Add("$prefix.$hostNumber") }
}

$results = [Collections.Generic.List[object]]::new()
$ping = [Net.NetworkInformation.Ping]::new()
foreach ($ip in $targets) {
    $reply = $null
    try { $reply = $ping.Send($ip, 150) } catch { }
    $openPorts = [Collections.Generic.List[string]]::new()
    $connections = [Collections.Generic.List[object]]::new()
    foreach ($port in $ports) {
        $client = [Net.Sockets.TcpClient]::new()
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
    $neighbor = Get-NetNeighbor -IPAddress $ip -ErrorAction SilentlyContinue |
        Where-Object { $_.State -ne 'Unreachable' -and $_.LinkLayerAddress } | Select-Object -First 1
    if (($null -eq $reply -or $reply.Status -ne 'Success') -and $openPorts.Count -eq 0 -and $null -eq $neighbor) { continue }
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
        mac_address = if ($null -ne $neighbor) { $neighbor.LinkLayerAddress } else { '' }
        hostname = $hostname
        vendor = ''
        os_details = $os
        open_ports = $openPorts -join ', '
    })
}
$ping.Dispose()
ConvertTo-Json -InputObject @($results) -Compress -Depth 3
