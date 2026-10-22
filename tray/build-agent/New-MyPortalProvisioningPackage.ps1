#Requires -Version 7.2
<#
.SYNOPSIS
    Helpers for encrypted Windows Configuration Designer provisioning packages.

.DESCRIPTION
    Dot-sourced by MyPortalBuildAgent.ps1. WCD is an optional ADK component;
    ordinary MSI/EXE jobs continue to work when it is not installed. All WCD
    output stays in memory because it contains the generated package password.
#>

function Resolve-MyPortalIcd([string]$RequestedPath) {
    $candidates = @()
    if ($RequestedPath) {
        $candidates += $RequestedPath
    } else {
        $command = Get-Command 'icd.exe' -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
        foreach ($base in ${env:ProgramFiles(x86)}, $env:ProgramFiles) {
            if ($base) {
                $candidates += Join-Path $base 'Windows Kits\10\Assessment and Deployment Kit\Imaging and Configuration Designer\x86\ICD.exe'
            }
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $candidatePath = (Resolve-Path -LiteralPath $candidate).Path
        # The common settings store omits desktop-specific customizations.
        $store = Join-Path (Split-Path -Parent $candidatePath) 'Microsoft-Desktop-Provisioning.dat'
        if (Test-Path -LiteralPath $store -PathType Leaf) {
            return $candidatePath
        }
    }
    return $null
}

function Get-MyPortalProvisioningProperty($Settings, [string]$Name) {
    if ($Settings -is [System.Collections.IDictionary]) {
        if ($Settings.Contains($Name)) { return ,$Settings[$Name] }
    } elseif ($null -ne $Settings -and $Settings.PSObject.Properties[$Name]) {
        return ,$Settings.$Name
    }
    return $null
}

function Get-MyPortalProvisioningValue($Settings, [string]$Name) {
    return [string](Get-MyPortalProvisioningProperty $Settings $Name)
}

function Add-MyPortalProvisioningXmlNode(
    [System.Xml.XmlNode]$Parent,
    [string]$Name,
    [AllowNull()][string]$Value = $null,
    [AllowNull()][string]$Namespace = $null
) {
    $document = if ($Parent -is [System.Xml.XmlDocument]) { $Parent } else { $Parent.OwnerDocument }
    if (-not $PSBoundParameters.ContainsKey('Namespace')) { $Namespace = $Parent.NamespaceURI }
    $element = $document.CreateElement($Name, $Namespace)
    if ($null -ne $Value) { $element.InnerText = $Value }
    [void]$Parent.AppendChild($element)
    # XmlElement implements IEnumerable; do not enumerate its children.
    return ,$element
}

function New-MyPortalProvisioningXml($Settings, [string]$MsiPath, [string]$ScriptPath, [int]$BuildId) {
    $password = Get-MyPortalProvisioningValue $Settings 'admin_password'
    $ssid = Get-MyPortalProvisioningValue $Settings 'wifi_ssid'
    $wifiPassword = Get-MyPortalProvisioningValue $Settings 'wifi_password'
    $computerName = Get-MyPortalProvisioningValue $Settings 'device_name_template'
    $directoryJoin = Get-MyPortalProvisioningProperty $Settings 'directory_join'
    $joinMode = ''
    $authority = ''
    $bulkToken = ''
    $domain = ''
    $domainUsername = ''
    $domainPassword = ''
    $accountOu = ''
    if ($null -ne $directoryJoin) {
        if ($directoryJoin -isnot [System.Collections.IDictionary] -and $directoryJoin -isnot [pscustomobject]) {
            throw 'The provisioning directory join settings are not valid.'
        }
        $joinMode = Get-MyPortalProvisioningValue $directoryJoin 'mode'
        switch -CaseSensitive ($joinMode) {
            'entra' {
                $bulkToken = Get-MyPortalProvisioningValue $directoryJoin 'bulk_token'
                $authority = Get-MyPortalProvisioningValue $directoryJoin 'authority'
                if (-not $authority) { $authority = 'https://login.microsoftonline.com/common' }
                if (-not $bulkToken -or $bulkToken.Length -gt 16384 -or $bulkToken -match '[\s\x00-\x1f\x7f]') {
                    throw 'The provisioning Microsoft Entra bulk token is not valid.'
                }
                if ($authority -notmatch '^https://login\.(?:microsoftonline\.com|microsoftonline\.us|chinacloudapi\.cn)/[^/?#:\s]+/?$') {
                    throw 'The provisioning Microsoft Entra authority is not valid.'
                }
                $authority = $authority.TrimEnd('/')
            }
            'active_directory' {
                $domain = Get-MyPortalProvisioningValue $directoryJoin 'domain_name'
                $domainUsername = Get-MyPortalProvisioningValue $directoryJoin 'username'
                $domainPassword = Get-MyPortalProvisioningValue $directoryJoin 'password'
                $accountOu = Get-MyPortalProvisioningValue $directoryJoin 'organizational_unit'
                $domainLabels = $domain.Split('.')
                if ($domain.Length -gt 253 -or $domainLabels.Count -lt 2) {
                    throw 'The provisioning Active Directory domain is not valid.'
                }
                foreach ($label in $domainLabels) {
                    if ($label -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$') {
                        throw 'The provisioning Active Directory domain is not valid.'
                    }
                }
                if ($domainUsername.Length -gt 256 -or $domainUsername -match '[\x00-\x1f\x7f]' -or
                    $domainUsername -notmatch '^[^\\]+\\[^\\]+$|^[^@]+@[^@]+$') {
                    throw 'The provisioning Active Directory join username is not valid.'
                }
                if (-not $domainPassword -or $domainPassword.Length -gt 127 -or $domainPassword -match '[\x00-\x1f\x7f]') {
                    throw 'The provisioning Active Directory join password is not valid.'
                }
                if ($accountOu.Length -gt 1024 -or $accountOu -match '[\x00-\x1f\x7f]') { throw 'The provisioning Active Directory organizational unit is not valid.' }
                # HideOobe plus a domain join should always have a unique
                # computer name and the bootstrap local administrator.
                if (-not $computerName) { $computerName = 'MP-%RAND:8%' }
                if ($computerName -notmatch '%RAND:([1-9]|1[0-5])%') {
                    throw 'The provisioning Active Directory computer name must include a random name token.'
                }
            }
            default { throw 'The provisioning directory join mode is not supported.' }
        }
    }
    if ($password.Length -lt 12 -or $password.Length -gt 127 -or $password -match '[\x00-\x1f\x7f]') {
        throw 'The provisioning administrator password is not valid.'
    }
    if ([System.Text.Encoding]::UTF8.GetByteCount($ssid) -gt 32 -or $ssid -match '[\x00-\x1f\x7f]') {
        throw 'The provisioning Wi-Fi SSID is not valid.'
    }
    if ($wifiPassword -and -not $ssid) { throw 'A provisioning Wi-Fi password requires an SSID.' }
    if ($wifiPassword -and $wifiPassword -notmatch '^[\x20-\x7e]{8,63}$') {
        throw 'Provisioning Wi-Fi requires an 8 to 63 character WPA2-Personal password, or no password for an open network.'
    }
    if ($computerName) {
        $randomTokens = [regex]::Matches($computerName, '%RAND:([1-9]|1[0-5])%')
        $expanded = [regex]::Replace($computerName, '%RAND:([1-9]|1[0-5])%', {
            param($token)
            return '0' * [int]$token.Groups[1].Value
        })
        if ($randomTokens.Count -gt 1 -or $expanded -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]$|^[A-Za-z]$' -or
            $expanded.Length -gt 15 -or $expanded -notmatch '[A-Za-z]') {
            throw 'The provisioning computer name template is not valid.'
        }
    }

    $doc = [System.Xml.XmlDocument]::new()
    [void]$doc.AppendChild($doc.CreateXmlDeclaration('1.0', 'utf-8', $null))
    $root = Add-MyPortalProvisioningXmlNode $doc 'WindowsCustomizations'
    $package = Add-MyPortalProvisioningXmlNode $root 'PackageConfig' -Namespace 'urn:schemas-Microsoft-com:Windows-ICD-Package-Config.v1.0'
    [void](Add-MyPortalProvisioningXmlNode $package 'ID' ([guid]::NewGuid().ToString('B')))
    [void](Add-MyPortalProvisioningXmlNode $package 'Name' "MyPortal Provisioning $BuildId")
    [void](Add-MyPortalProvisioningXmlNode $package 'Version' '1.0')
    [void](Add-MyPortalProvisioningXmlNode $package 'OwnerType' 'ITAdmin')
    [void](Add-MyPortalProvisioningXmlNode $package 'Rank' '0')
    $settingsNode = Add-MyPortalProvisioningXmlNode $root 'Settings' -Namespace 'urn:schemas-microsoft-com:windows-provisioning'
    $customizations = Add-MyPortalProvisioningXmlNode $settingsNode 'Customizations'
    $common = Add-MyPortalProvisioningXmlNode $customizations 'Common'
    $accounts = Add-MyPortalProvisioningXmlNode $common 'Accounts'
    if ($joinMode -ceq 'entra') {
        $azure = Add-MyPortalProvisioningXmlNode $accounts 'Azure'
        [void](Add-MyPortalProvisioningXmlNode $azure 'Authority' $authority)
        [void](Add-MyPortalProvisioningXmlNode $azure 'BPRT' $bulkToken)
    }
    if ($computerName -or $joinMode -ceq 'active_directory') {
        $computer = Add-MyPortalProvisioningXmlNode $accounts 'ComputerAccount'
        [void](Add-MyPortalProvisioningXmlNode $computer 'ComputerName' $computerName)
        if ($joinMode -ceq 'active_directory') {
            [void](Add-MyPortalProvisioningXmlNode $computer 'DomainName' $domain)
            [void](Add-MyPortalProvisioningXmlNode $computer 'Account' $domainUsername)
            [void](Add-MyPortalProvisioningXmlNode $computer 'Password' $domainPassword)
            if ($accountOu) { [void](Add-MyPortalProvisioningXmlNode $computer 'AccountOU' $accountOu) }
        }
    }
    $users = Add-MyPortalProvisioningXmlNode $accounts 'Users'
    $user = Add-MyPortalProvisioningXmlNode $users 'User'
    $user.SetAttribute('UserName', 'ProvisioningAdmin')
    [void](Add-MyPortalProvisioningXmlNode $user 'Password' $password)
    # WCD maps this built-in group selection to Accounts CSP's admin group.
    [void](Add-MyPortalProvisioningXmlNode $user 'UserGroup' 'Administrators')
    $oobe = Add-MyPortalProvisioningXmlNode $common 'OOBE'
    $desktop = Add-MyPortalProvisioningXmlNode $oobe 'Desktop'
    [void](Add-MyPortalProvisioningXmlNode $desktop 'HideOobe' 'True')
    if ($ssid) {
        $connectivity = Add-MyPortalProvisioningXmlNode $common 'ConnectivityProfiles'
        $wlan = Add-MyPortalProvisioningXmlNode $connectivity 'WLAN'
        $wlanSetting = Add-MyPortalProvisioningXmlNode $wlan 'WLANSetting'
        $wlanConfig = Add-MyPortalProvisioningXmlNode $wlanSetting 'WLANConfig'
        $wlanConfig.SetAttribute('SSID', $ssid)
        $wlanXml = Add-MyPortalProvisioningXmlNode $wlanConfig 'WLANXmlSettings'
        $security = if ($wifiPassword) { 'WPA2-Personal' } else { 'Open' }
        [void](Add-MyPortalProvisioningXmlNode $wlanXml 'SecurityType' $security)
        if ($wifiPassword) { [void](Add-MyPortalProvisioningXmlNode $wlanXml 'SecurityKey' $wifiPassword) }
        [void](Add-MyPortalProvisioningXmlNode $wlanXml 'AutoConnect' 'True')
    }
    $commands = Add-MyPortalProvisioningXmlNode $common 'ProvisioningCommands'
    $primary = Add-MyPortalProvisioningXmlNode $commands 'PrimaryContext'
    $command = Add-MyPortalProvisioningXmlNode $primary 'Command'
    $commandConfig = Add-MyPortalProvisioningXmlNode $command 'CommandConfig'
    $commandConfig.SetAttribute('Name', 'InstallMyPortalTray')
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'CommandFile' $ScriptPath)
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'CommandLine' 'cmd.exe /d /c Install-MyPortalTray.cmd')
    $dependencies = Add-MyPortalProvisioningXmlNode $commandConfig 'DependencyPackages'
    $msi = Add-MyPortalProvisioningXmlNode $dependencies 'Dependency' $MsiPath
    $msi.SetAttribute('Name', 'myportal-tray.msi')
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'ContinueInstall' 'False')
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'RestartRequired' 'False')
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'ReturnCodeRestart' '3010')
    [void](Add-MyPortalProvisioningXmlNode $commandConfig 'ReturnCodeSuccess' '0')
    return ,$doc
}

function Get-MyPortalProvisioningPassword([string]$Output) {
    # ICD documents password output, but offers no machine-readable format.
    # Accept a labelled English password line only; an unfamiliar/localized
    # format fails closed rather than publishing an inaccessible package.
    $patterns = @(
        '(?im)^.*?\b(?:encryption|decryption|package)\s+password\s*(?::|=|\bis\b\s*:?)\s*(?<value>[^\r\n]+?)\s*$',
        '(?im)^\s*password\s*(?::|=)\s*(?<value>[^\r\n]+?)\s*$'
    )
    $passwords = @(
        foreach ($pattern in $patterns) {
            foreach ($match in [regex]::Matches($Output, $pattern)) {
                $value = $match.Groups['value'].Value.Trim()
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                if ($value.Length -ge 8 -and $value.Length -le 128 -and $value -notmatch '[\x00-\x1f\x7f]') { $value }
            }
        }
    ) | Select-Object -Unique
    if (@($passwords).Count -ne 1) {
        throw 'WCD did not return a recognizable package encryption password. Validate the English-language ADK/WCD installation before rebuilding.'
    }
    return [string](@($passwords)[0])
}

function New-MyPortalProvisioningPackage(
    $Settings,
    [string]$MsiPath,
    [string]$PackagePath,
    [string]$IcdPath,
    [int]$BuildId
) {
    if (-not $IcdPath) { throw 'Install Windows Configuration Designer from the Windows ADK to build provisioning packages.' }
    $project = Join-Path (Split-Path -Parent $PackagePath) 'provisioning-source'
    New-Item -ItemType Directory -Force -Path $project | Out-Null
    $process = $null
    try {
        $scriptPath = Join-Path $project 'Install-MyPortalTray.cmd'
        @'
@echo off
setlocal
set "LOGDIR=%ProgramData%\MyPortal\Provisioning"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
msiexec.exe /i "%~dp0myportal-tray.msi" /qn /norestart REBOOT=ReallySuppress /L*V "%LOGDIR%\tray-install.log" >nul 2>&1
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="1641" exit /b 3010
exit /b %RESULT%
'@ | Set-Content -LiteralPath $scriptPath -Encoding ascii
        $xmlPath = Join-Path $project 'customizations.xml'
        $xml = New-MyPortalProvisioningXml $Settings $MsiPath $scriptPath $BuildId
        $writerSettings = [System.Xml.XmlWriterSettings]::new()
        $writerSettings.Encoding = [System.Text.UTF8Encoding]::new($false)
        $writerSettings.Indent = $true
        $writer = [System.Xml.XmlWriter]::Create($xmlPath, $writerSettings)
        try { $xml.Save($writer) } finally { $writer.Dispose() }

        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $IcdPath
        $startInfo.WorkingDirectory = $project
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($argument in @(
            '/Build-ProvisioningPackage', "/CustomizationXML:$xmlPath", "/PackagePath:$PackagePath",
            "/StoreFile:$(Join-Path (Split-Path -Parent $IcdPath) 'Microsoft-Desktop-Provisioning.dat')",
            '+Encrypted', '+Overwrite'
        )) { $startInfo.ArgumentList.Add($argument) }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(1800000)) {
            $process.Kill($true)
            [void]$process.WaitForExit(5000)
            throw 'WCD exceeded the provisioning package build timeout.'
        }
        $output = $stdout.GetAwaiter().GetResult() + "`n" + $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
            # Diagnostics may contain administrator/Wi-Fi passwords and must
            # not be propagated to the portal or the persistent agent log.
            throw "WCD failed to build the encrypted provisioning package (exit code $($process.ExitCode)). Validate the ADK/WCD installation and provisioning settings."
        }
        $packagePassword = Get-MyPortalProvisioningPassword $output
        return [pscustomobject]@{ Path = $PackagePath; Password = $packagePassword }
    } catch {
        Remove-Item -LiteralPath $PackagePath -Force -ErrorAction SilentlyContinue
        # File/process/XML exceptions can include source values. Return only
        # our deliberate diagnostic messages, never a raw inner exception.
        $message = $_.Exception.Message
        if ($message -notmatch '^(WCD |Install Windows Configuration Designer|The provisioning |Provisioning Wi-Fi|A provisioning Wi-Fi)') {
            $message = 'The encrypted provisioning package could not be built. Validate the ADK/WCD installation and provisioning settings.'
        }
        throw $message
    } finally {
        if ($null -ne $process) { $process.Dispose() }
        Remove-Item -LiteralPath $project -Recurse -Force -ErrorAction SilentlyContinue
    }
}
