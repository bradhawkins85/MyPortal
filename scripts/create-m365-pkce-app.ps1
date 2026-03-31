<#
.SYNOPSIS
    Creates (or re-creates) the MyPortal PKCE public-client app registration in
    a Microsoft 365 / Azure AD partner tenant.

.DESCRIPTION
    MyPortal uses a multi-tenant PKCE public-client app registration to drive the
    tenant-discovery and provisioning OAuth sign-in flows.  This app is normally
    created automatically when you first provision the M365 integration via the
    Admin → Modules → m365-admin settings page.

    If the app is accidentally deleted or needs to be re-created (e.g. after an
    AADSTS700016 error), run this script from your CSP / Lighthouse partner tenant
    to provision a fresh registration and obtain the Application (client) ID.

.PARAMETER RedirectUri
    The OAuth redirect URI registered on the app.  Must match the /m365/callback
    endpoint of your MyPortal instance, e.g. https://portal.example.com/m365/callback

.PARAMETER DisplayName
    Display name for the app registration.  Defaults to "MyPortal Bootstrap".

.PARAMETER TenantId
    The Azure AD tenant ID (or domain) of your CSP / Lighthouse partner tenant.
    If omitted, the script uses the tenant of the currently signed-in account.

.EXAMPLE
    .\create-m365-pkce-app.ps1 -RedirectUri "https://portal.example.com/m365/callback"

.EXAMPLE
    .\create-m365-pkce-app.ps1 `
        -RedirectUri "https://portal.example.com/m365/callback" `
        -TenantId "contoso.onmicrosoft.com" `
        -DisplayName "MyPortal Bootstrap"

.NOTES
    Prerequisites:
      * PowerShell 7+ (or Windows PowerShell 5.1)
      * Microsoft.Graph PowerShell SDK  -or-  Azure CLI (az)

    Install the Microsoft.Graph SDK (if not already installed):
        Install-Module Microsoft.Graph -Scope CurrentUser

    Permissions required to run this script:
        Application.ReadWrite.All (delegated, on behalf of a Global Admin)
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [string] $RedirectUri,

    [Parameter(Mandatory = $false)]
    [string] $DisplayName = "MyPortal Bootstrap",

    [Parameter(Mandatory = $false)]
    [string] $TenantId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helper: prefer Microsoft.Graph SDK, fall back to raw REST via az CLI token
# ---------------------------------------------------------------------------

function Get-GraphToken {
    # Try Microsoft.Graph SDK first
    if (Get-Command Get-MgContext -ErrorAction SilentlyContinue) {
        $ctx = Get-MgContext -ErrorAction SilentlyContinue
        if ($ctx) {
            return $null  # Graph SDK is connected; callers use Invoke-MgGraphRequest
        }
    }
    # Fall back to Azure CLI
    if (Get-Command az -ErrorAction SilentlyContinue) {
        $tokenJson = az account get-access-token --resource https://graph.microsoft.com 2>$null | ConvertFrom-Json
        if ($tokenJson.accessToken) {
            return $tokenJson.accessToken
        }
    }
    throw "No authentication context found.  Sign in with 'Connect-MgGraph' or 'az login' first."
}

function Invoke-GraphPost {
    param([string] $Uri, [hashtable] $Body, [string] $AccessToken)
    $json = $Body | ConvertTo-Json -Depth 10
    if ($AccessToken) {
        $response = Invoke-RestMethod -Uri $Uri -Method Post `
            -Headers @{ Authorization = "Bearer $AccessToken"; "Content-Type" = "application/json" } `
            -Body $json
    } else {
        $response = Invoke-MgGraphRequest -Uri $Uri -Method Post -Body $json -ContentType "application/json"
    }
    return $response
}

function Invoke-GraphGet {
    param([string] $Uri, [string] $AccessToken)
    if ($AccessToken) {
        return Invoke-RestMethod -Uri $Uri -Method Get `
            -Headers @{ Authorization = "Bearer $AccessToken" }
    } else {
        return Invoke-MgGraphRequest -Uri $Uri -Method Get
    }
}

function Invoke-GraphPatch {
    param([string] $Uri, [hashtable] $Body, [string] $AccessToken)
    $json = $Body | ConvertTo-Json -Depth 10
    if ($AccessToken) {
        Invoke-RestMethod -Uri $Uri -Method Patch `
            -Headers @{ Authorization = "Bearer $AccessToken"; "Content-Type" = "application/json" } `
            -Body $json | Out-Null
    } else {
        Invoke-MgGraphRequest -Uri $Uri -Method Patch -Body $json -ContentType "application/json" | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------

$accessToken = $null

if (Get-Command Connect-MgGraph -ErrorAction SilentlyContinue) {
    Write-Host "Connecting to Microsoft Graph via Microsoft.Graph SDK..." -ForegroundColor Cyan
    $connectParams = @{
        Scopes = @("Application.ReadWrite.All")
    }
    if ($TenantId) { $connectParams["TenantId"] = $TenantId }
    Connect-MgGraph @connectParams | Out-Null
    Write-Host "Connected." -ForegroundColor Green
} elseif (Get-Command az -ErrorAction SilentlyContinue) {
    Write-Host "Signing in via Azure CLI..." -ForegroundColor Cyan
    $azLoginArgs = @("login")
    if ($TenantId) { $azLoginArgs += @("--tenant", $TenantId) }
    az @azLoginArgs | Out-Null
    $accessToken = (az account get-access-token --resource https://graph.microsoft.com | ConvertFrom-Json).accessToken
    Write-Host "Signed in." -ForegroundColor Green
} else {
    Write-Error "Neither the Microsoft.Graph PowerShell SDK nor the Azure CLI is installed.`n`nInstall one of them and re-run this script.`n`n  Install-Module Microsoft.Graph -Scope CurrentUser`n  -or-`n  winget install Microsoft.AzureCLI"
    exit 1
}

# ---------------------------------------------------------------------------
# Delete any existing app registrations with the same display name
# ---------------------------------------------------------------------------

Write-Host "Checking for existing app registrations named '$DisplayName'..." -ForegroundColor Cyan
$filterUri = "https://graph.microsoft.com/v1.0/applications?`$filter=displayName eq '$DisplayName'"
$existing = Invoke-GraphGet -Uri $filterUri -AccessToken $accessToken
foreach ($app in $existing.value) {
    if ($PSCmdlet.ShouldProcess($app.appId, "Delete existing app registration '$($app.displayName)'")) {
        Write-Host "  Deleting stale app: $($app.appId)" -ForegroundColor Yellow
        if ($accessToken) {
            Invoke-RestMethod -Uri "https://graph.microsoft.com/v1.0/applications/$($app.id)" `
                -Method Delete -Headers @{ Authorization = "Bearer $accessToken" }
        } else {
            Invoke-MgGraphRequest -Uri "https://graph.microsoft.com/v1.0/applications/$($app.id)" -Method Delete
        }
    }
}

# ---------------------------------------------------------------------------
# Create the new PKCE public-client app registration
# ---------------------------------------------------------------------------

if ($PSCmdlet.ShouldProcess($DisplayName, "Create PKCE public-client app registration")) {
    Write-Host "Creating PKCE app registration '$DisplayName'..." -ForegroundColor Cyan

    $appBody = @{
        displayName     = $DisplayName
        # AzureADMultipleOrgs lets customer Global Admins sign in without the app
        # being pre-registered in their tenant.
        signInAudience  = "AzureADMultipleOrgs"
        # Enable PKCE / device code / native-app flows (no client secret required).
        isFallbackPublicClient = $true
        publicClient    = @{
            redirectUris = @($RedirectUri)
        }
    }

    $newApp = Invoke-GraphPost `
        -Uri "https://graph.microsoft.com/v1.0/applications" `
        -Body $appBody `
        -AccessToken $accessToken

    $clientId = $newApp.appId
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "  PKCE app registration created successfully!" -ForegroundColor Green
    Write-Host "  Application (client) ID: $clientId" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Set M365_PKCE_CLIENT_ID=$clientId in your MyPortal .env file." -ForegroundColor White
    Write-Host "     -or-" -ForegroundColor White
    Write-Host "  2. Use the 'Re-provision PKCE app' button in Admin → Modules → m365-admin" -ForegroundColor White
    Write-Host "     to auto-store the new app ID (requires CSP admin credentials to be set)." -ForegroundColor White
    Write-Host ""
}
