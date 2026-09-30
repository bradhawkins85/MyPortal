<#
.SYNOPSIS
    Example external script for a MyPortal "Pause For Webhook" onboarding step.

.DESCRIPTION
    1. Lists staff workflows paused on the webhook step.
    2. Creates each user in Active Directory from the request fields.
    3. Resumes the MyPortal workflow, returning the username and the generated
       password (as a secret value) or a failed outcome.

    Copy the webhook URL and POST key from the Pause For Webhook step in the
    company's onboarding workflow editor. Run on a host with the
    ActiveDirectory PowerShell module (for example a domain controller).
#>
param(
    [Parameter(Mandatory)] [string] $WebhookUrl,   # https://portal.example.com/api/staff/workflow-webhooks/<id>
    [Parameter(Mandatory)] [string] $PostKey,
    [string] $UserOu = "OU=Staff,DC=corp,DC=example,DC=com",
    [string] $UpnSuffix = "corp.example.com"
)

$ErrorActionPreference = "Stop"
Import-Module ActiveDirectory

function New-RandomPassword([int] $Length = 16) {
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_=+?"
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function Resume-Workflow($Item, [hashtable] $Body) {
    $Body.postKey = $PostKey
    $Body.staffId = $Item.staffId
    $Body.source  = "ad-onboarding-script"
    Invoke-RestMethod -Method Post -Uri $WebhookUrl `
        -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 5)
}

# 1. List workflows waiting on this webhook (includes all request fields).
$pending = Invoke-RestMethod -Method Get -Uri "$WebhookUrl/pending" `
    -Headers @{ "X-Webhook-Post-Key" = $PostKey }

foreach ($item in @($pending)) {
    $staff = $item.staff
    Write-Host "Processing $($staff.firstName) $($staff.lastName) (execution $($item.executionId))"
    try {
        # 2. Do the on-premises work using the submitted request fields.
        $sam = ("{0}.{1}" -f $staff.firstName, $staff.lastName).ToLower() -replace "[^a-z0-9.]", ""
        $sam = $sam.Substring(0, [Math]::Min(20, $sam.Length))
        $password = New-RandomPassword

        New-ADUser -Name "$($staff.firstName) $($staff.lastName)" `
            -GivenName $staff.firstName -Surname $staff.lastName `
            -SamAccountName $sam -UserPrincipalName "$sam@$UpnSuffix" `
            -EmailAddress $staff.email -Title $staff.jobTitle -Department $staff.department `
            -Path $UserOu -Enabled $true -ChangePasswordAtLogon $true `
            -AccountPassword (ConvertTo-SecureString $password -AsPlainText -Force)

        # Custom fields from the request are available by their internal name, e.g.:
        # if ($staff.customFields.needs_vpn) { Add-ADGroupMember -Identity "VPN Users" -Members $sam }

        # 3. Resume MyPortal. Later steps can use ${vars.ad_username},
        #    ${vars.ad_status} and ${vars.ad_password}.
        $result = Resume-Workflow $item @{
            values       = @{ ad_username = $sam; ad_upn = "$sam@$UpnSuffix"; ad_status = "pass" }
            secretValues = @{ ad_password = $password }
        }
        Write-Host "  Resumed: workflow state is now '$($result.state)'"
    }
    catch {
        # Tell MyPortal the step failed so the workflow is marked failed and a ticket raised.
        Write-Warning "  Failed: $($_.Exception.Message)"
        Resume-Workflow $item @{
            outcome = "failed"
            error   = "AD account creation failed: $($_.Exception.Message)"
            values  = @{ ad_status = "fail" }
        } | Out-Null
    }
}
