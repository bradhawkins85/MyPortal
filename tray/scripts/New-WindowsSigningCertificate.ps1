[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\signing'),

    [Parameter()]
    [securestring]$Password = (Read-Host 'Enter a password for the signing PFX' -AsSecureString),

    [Parameter()]
    [ValidateRange(1, 10)]
    [int]$ValidYears = 5
)

$ErrorActionPreference = 'Stop'
$subject = 'CN=MyPortal Tray Build'
$certificate = $null

try {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $outputPath = (Resolve-Path $OutputDirectory).Path
    $pfxPath = Join-Path $outputPath 'myportal-tray-signing.pfx'
    $base64Path = Join-Path $outputPath 'myportal-tray-signing.pfx.base64'
    $cerPath = Join-Path $outputPath 'myportal-tray-signing.cer'

    foreach ($path in @($pfxPath, $base64Path, $cerPath)) {
        if (Test-Path $path) {
            throw "Refusing to overwrite existing signing material: $path"
        }
    }

    $certificate = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $subject `
        -FriendlyName 'MyPortal Tray Build' `
        -CertStoreLocation 'Cert:\CurrentUser\My' `
        -KeyAlgorithm RSA `
        -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddYears($ValidYears)

    Export-PfxCertificate -Cert $certificate -FilePath $pfxPath -Password $Password | Out-Null
    Export-Certificate -Cert $certificate -FilePath $cerPath | Out-Null
    [IO.File]::WriteAllText($base64Path, [Convert]::ToBase64String([IO.File]::ReadAllBytes($pfxPath)))

    Write-Host "Created signing certificate $($certificate.Thumbprint) (expires $($certificate.NotAfter.ToString('u')))."
    Write-Host "PFX:          $pfxPath"
    Write-Host "GitHub value: $base64Path"
    Write-Host "Public cert:  $cerPath"
} finally {
    if ($null -ne $certificate) {
        Remove-Item "Cert:\CurrentUser\My\$($certificate.Thumbprint)" -Force -ErrorAction SilentlyContinue
    }
}
