#Requires -Version 7.2
<#
.SYNOPSIS
    Exercises provisioning XML generation and fail-closed password extraction.
.DESCRIPTION
    Run with pwsh -NoProfile -File ./Test-MyPortalProvisioningPackage.ps1.
    No administrator privileges or Windows ADK installation are required.
    A real encrypted WCD build and OOBE run must also be validated on Windows.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3
. (Join-Path $PSScriptRoot 'New-MyPortalProvisioningPackage.ps1')

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-Throws([scriptblock]$Action, [string]$Message, [string[]]$Forbidden = @()) {
    $threw = $false
    try { [void](& $Action) } catch {
        $threw = $true
        foreach ($secret in $Forbidden) {
            if ($_.Exception.ToString().Contains($secret)) { throw 'A provisioning diagnostic disclosed a source value.' }
        }
    }
    Assert-Condition $threw $Message
}

$settings = @{
    admin_password = 'Example<&"Admin123!'
    wifi_ssid = 'Office <&" network'
    wifi_password = 'Example<&"Wifi123!'
    device_name_template = 'MP-%RAND:8%'
}
$msiPath = 'C:\Private Build & Assets\myportal-tray.msi'
$cmdPath = 'C:\Private Build & Assets\Install-MyPortalTray.cmd'
$xml = New-MyPortalProvisioningXml $settings $msiPath $cmdPath 42
# Serialize and parse again so invalid escaping and namespace resets surface.
$parsed = [xml]$xml.OuterXml
foreach ($section in @(
    @{ Name = 'PackageConfig'; Namespace = 'urn:schemas-Microsoft-com:Windows-ICD-Package-Config.v1.0' },
    @{ Name = 'Settings'; Namespace = 'urn:schemas-microsoft-com:windows-provisioning' }
)) {
    $sectionNode = $parsed.DocumentElement.SelectSingleNode("*[local-name()='$($section.Name)']")
    foreach ($node in @($sectionNode) + @($sectionNode.SelectNodes('.//*'))) {
        Assert-Condition ($node.NamespaceURI -ceq $section.Namespace) 'A provisioning XML namespace was lost.'
    }
}
$ns = [System.Xml.XmlNamespaceManager]::new($parsed.NameTable)
$ns.AddNamespace('p', 'urn:schemas-microsoft-com:windows-provisioning')
$common = '/WindowsCustomizations/p:Settings/p:Customizations/p:Common'
$user = $parsed.SelectSingleNode("$common/p:Accounts/p:Users/p:User", $ns)
Assert-Condition ($user.GetAttribute('UserName') -eq 'ProvisioningAdmin') 'Provisioning account missing.'
Assert-Condition ($user.SelectSingleNode('p:Password', $ns).InnerText -ceq $settings.admin_password) 'Admin password did not survive XML serialization.'
Assert-Condition ($user.SelectSingleNode('p:UserGroup', $ns).InnerText -eq 'Administrators') 'Admin group missing.'
Assert-Condition ($parsed.SelectSingleNode("$common/p:OOBE/p:Desktop/p:HideOobe", $ns).InnerText -eq 'True') 'OOBE setting missing.'
Assert-Condition ($parsed.SelectSingleNode("$common/p:Accounts/p:ComputerAccount/p:ComputerName", $ns).InnerText -eq 'MP-%RAND:8%') 'Naming template changed.'
$wifi = $parsed.SelectSingleNode("$common/p:ConnectivityProfiles/p:WLAN/p:WLANSetting/p:WLANConfig", $ns)
Assert-Condition ($wifi.GetAttribute('SSID') -ceq $settings.wifi_ssid) 'SSID did not survive XML serialization.'
Assert-Condition ($wifi.SelectSingleNode('p:WLANXmlSettings/p:SecurityKey', $ns).InnerText -ceq $settings.wifi_password) 'Wi-Fi password did not survive XML serialization.'
$command = $parsed.SelectSingleNode("$common/p:ProvisioningCommands/p:PrimaryContext/p:Command/p:CommandConfig", $ns)
Assert-Condition ($command.SelectSingleNode('p:CommandFile', $ns).InnerText -ceq $cmdPath) 'Command file path changed.'
Assert-Condition ($command.SelectSingleNode('p:DependencyPackages/p:Dependency', $ns).InnerText -ceq $msiPath) 'Embedded MSI path changed.'
Assert-Condition ($command.SelectSingleNode('p:ContinueInstall', $ns).InnerText -eq 'False') 'Installation failures must stop provisioning.'

$openSettings = @{ admin_password = 'ExampleAdmin123!'; wifi_ssid = 'Open Office' }
$open = New-MyPortalProvisioningXml $openSettings $msiPath $cmdPath 43
$openNs = [System.Xml.XmlNamespaceManager]::new($open.NameTable)
$openNs.AddNamespace('p', 'urn:schemas-microsoft-com:windows-provisioning')
$openWifi = $open.SelectSingleNode("$common/p:ConnectivityProfiles/p:WLAN/p:WLANSetting/p:WLANConfig/p:WLANXmlSettings", $openNs)
Assert-Condition ($openWifi.SelectSingleNode('p:SecurityType', $openNs).InnerText -eq 'Open') 'Open Wi-Fi must remain available.'
Assert-Condition ($null -eq $openWifi.SelectSingleNode('p:SecurityKey', $openNs)) 'Open Wi-Fi must omit its key.'
$minimum = New-MyPortalProvisioningXml @{ admin_password = 'ExampleAdmin123!' } $msiPath $cmdPath 44
Assert-Condition ($minimum.OuterXml -notmatch 'ComputerAccount|ConnectivityProfiles') 'Optional settings must be omitted.'

$entraSettings = @{
    admin_password = 'ExampleAdmin123!'
    directory_join = @{ mode = 'entra'; bulk_token = 'ExampleOpaqueBulkToken<&"123!' }
}
$entra = [xml](New-MyPortalProvisioningXml $entraSettings $msiPath $cmdPath 46).OuterXml
$entraAzure = $entra.SelectSingleNode("$common/p:Accounts/p:Azure", $ns)
Assert-Condition ($entraAzure.SelectSingleNode('p:Authority', $ns).InnerText -ceq 'https://login.microsoftonline.com/common') 'Default Entra authority is missing.'
Assert-Condition ($entraAzure.SelectSingleNode('p:BPRT', $ns).InnerText -ceq $entraSettings.directory_join.bulk_token) 'Entra bulk token did not survive XML serialization.'
Assert-Condition ($entra.SelectSingleNode("$common/p:Accounts/p:Users/p:User", $ns).GetAttribute('UserName') -ceq 'ProvisioningAdmin') 'Entra join removed the bootstrap administrator.'
Assert-Condition ($entra.SelectSingleNode("$common/p:OOBE/p:Desktop/p:HideOobe", $ns).InnerText -eq 'True') 'Entra join removed the OOBE setting.'
Assert-Condition ($null -eq $entra.SelectSingleNode("$common/p:Accounts/p:ComputerAccount", $ns)) 'Entra join unexpectedly added an AD account.'
# API jobs deserialize as PSCustomObject, including nested join settings.
$entraSettings.directory_join.authority = 'https://login.microsoftonline.us/example.onmicrosoft.us/'
$entraObject = $entraSettings | ConvertTo-Json -Depth 5 | ConvertFrom-Json
$entraNational = New-MyPortalProvisioningXml $entraObject $msiPath $cmdPath 47
Assert-Condition ($entraNational.SelectSingleNode("$common/p:Accounts/p:Azure/p:Authority", $ns).InnerText -ceq 'https://login.microsoftonline.us/example.onmicrosoft.us') 'A supported Entra authority was not preserved.'

$adSettings = @{
    admin_password = 'ExampleAdmin123!'
    device_name_template = 'AD-%RAND:8%'
    directory_join = @{
        mode = 'active_directory'
        domain_name = 'ad.example.com'
        username = 'AD\JoinAccount'
        password = 'ExampleDomain<&"123!'
        organizational_unit = 'OU=Provisioning & Support,OU=Computers,DC=ad,DC=example,DC=com'
    }
}
$ad = [xml](New-MyPortalProvisioningXml $adSettings $msiPath $cmdPath 48).OuterXml
$computer = $ad.SelectSingleNode("$common/p:Accounts/p:ComputerAccount", $ns)
Assert-Condition ($ad.SelectNodes("$common/p:Accounts/p:ComputerAccount", $ns).Count -eq 1) 'AD join created duplicate computer account nodes.'
Assert-Condition ($computer.SelectSingleNode('p:ComputerName', $ns).InnerText -ceq 'AD-%RAND:8%') 'AD computer name was not preserved.'
Assert-Condition ($computer.SelectSingleNode('p:DomainName', $ns).InnerText -ceq $adSettings.directory_join.domain_name) 'AD domain missing.'
Assert-Condition ($computer.SelectSingleNode('p:Account', $ns).InnerText -ceq $adSettings.directory_join.username) 'AD join account missing.'
Assert-Condition ($computer.SelectSingleNode('p:Password', $ns).InnerText -ceq $adSettings.directory_join.password) 'AD join password did not survive XML serialization.'
Assert-Condition ($computer.SelectSingleNode('p:AccountOU', $ns).InnerText -ceq $adSettings.directory_join.organizational_unit) 'AD organizational unit did not survive XML serialization.'
Assert-Condition ($ad.SelectSingleNode("$common/p:Accounts/p:Users/p:User/p:Password", $ns).InnerText -ceq $adSettings.admin_password) 'AD join changed the bootstrap administrator password.'
Assert-Condition ($ad.SelectSingleNode("$common/p:OOBE/p:Desktop/p:HideOobe", $ns).InnerText -eq 'True') 'AD join removed the OOBE setting.'
Assert-Condition ($null -eq $ad.SelectSingleNode("$common/p:Accounts/p:Azure", $ns)) 'AD join unexpectedly added Entra settings.'
$adSettings.Remove('device_name_template')
$adSettings.directory_join.Remove('organizational_unit')
$adSettings.directory_join.username = 'join.account@ad.example.com'
$adDefault = New-MyPortalProvisioningXml $adSettings $msiPath $cmdPath 49
Assert-Condition ($adDefault.SelectSingleNode("$common/p:Accounts/p:ComputerAccount/p:ComputerName", $ns).InnerText -ceq 'MP-%RAND:8%') 'AD join must default to a unique computer name.'
Assert-Condition ($null -eq $adDefault.SelectSingleNode("$common/p:Accounts/p:ComputerAccount/p:AccountOU", $ns)) 'An omitted AD OU must remain omitted.'

foreach ($invalidJoin in @(
    @{ mode = 'UnsupportedSensitiveMode' },
    @{},
    @{ mode = 'entra' },
    @{ mode = 'entra'; bulk_token = "ExampleSecretToken`nValue" },
    @{ mode = 'entra'; bulk_token = 'ExampleSecretToken'; authority = 'https://untrusted.example.com/common' },
    @{ mode = 'active_directory'; username = 'AD\JoinAccount'; password = 'ExampleDomain123!' },
    @{ mode = 'active_directory'; domain_name = 'ad.example.com'; password = 'ExampleDomain123!' },
    @{ mode = 'active_directory'; domain_name = 'ad.example.com'; username = 'AD\JoinAccount' },
    @{ mode = 'active_directory'; domain_name = 'invalid-domain'; username = 'AD\JoinAccount'; password = 'ExampleDomain123!' }
)) {
    $invalidSettings = @{ admin_password = 'ExampleAdmin123!'; directory_join = $invalidJoin }
    Assert-Throws { New-MyPortalProvisioningXml $invalidSettings $msiPath $cmdPath 50 } 'An invalid directory join was accepted.' @('ExampleSecretToken', 'ExampleDomain123!', 'UnsupportedSensitiveMode')
}
$adSettings.device_name_template = 'STATIC-PC'
Assert-Throws { New-MyPortalProvisioningXml $adSettings $msiPath $cmdPath 51 } 'An AD join with a static computer name was accepted.' @('ExampleDomain')

foreach ($invalidName in 'MP-%SERIAL%', '%RAND:8%', 'MP-%RAND:8%-%RAND:2%', '-PC', 'PC-', 'MP-%RAND:15%', 'Name with spaces', '123') {
    $testSettings = @{ admin_password = 'ExampleAdmin123!'; device_name_template = $invalidName }
    Assert-Throws { New-MyPortalProvisioningXml $testSettings $msiPath $cmdPath 45 } 'An invalid computer name was accepted.'
}
Assert-Throws { New-MyPortalProvisioningXml @{ admin_password = 'short' } $msiPath $cmdPath 45 } 'A short administrator password was accepted.'
Assert-Throws { New-MyPortalProvisioningXml @{ admin_password = 'ExampleAdmin123!'; wifi_password = 'ExampleWifi123!' } $msiPath $cmdPath 45 } 'Wi-Fi password without SSID was accepted.'
Assert-Throws { New-MyPortalProvisioningXml @{ admin_password = 'ExampleAdmin123!'; wifi_ssid = 'Office'; wifi_password = '123' } $msiPath $cmdPath 45 } 'A short Wi-Fi password was accepted.'

Assert-Condition ((Get-MyPortalProvisioningPassword 'Encryption password: ExamplePackage123!') -ceq 'ExamplePackage123!') 'Encryption password not extracted.'
Assert-Condition ((Get-MyPortalProvisioningPassword "Build succeeded`nPassword: 'ExamplePackage123!'") -ceq 'ExamplePackage123!') 'Quoted password not extracted.'
Assert-Condition ((Get-MyPortalProvisioningPassword "Encryption password: ExamplePackage123!`nPassword: ExamplePackage123!") -ceq 'ExamplePackage123!') 'Repeated password was rejected.'
Assert-Throws { Get-MyPortalProvisioningPassword 'Build succeeded' } 'Missing encryption password was accepted.'
Assert-Throws { Get-MyPortalProvisioningPassword "Password: ExamplePackage123!`nPassword: DifferentPackage123!" } 'Ambiguous encryption passwords were accepted.'
Assert-Throws { Get-MyPortalProvisioningPassword '<Password>ExampleAdmin123!</Password>' } 'A source XML password was accepted as an encryption password.'
Write-Host 'Provisioning helper checks passed. Validate WCD encryption output and OOBE on Windows before deployment.'
