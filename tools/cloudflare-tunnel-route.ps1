param(
    [Parameter(Position = 0)]
    [string] $AccountId
)

$ErrorActionPreference = "Stop"
$ApiBase = "https://api.cloudflare.com/client/v4"
$TunnelId = "b2bfd33b-b75c-4e94-9f08-b75f8b0c54ca"
$Hostname = "api.fdoc.app"
$Origin = "http://localhost:18082"

function Invoke-Cloudflare {
    param(
        [Parameter(Mandatory)] [ValidateSet("GET", "PUT")] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [object] $Body
    )

    $arguments = @{
        Method      = $Method
        Uri         = "$ApiBase$Path"
        Headers     = @{ Authorization = "Bearer $script:ApiToken" }
        ContentType = "application/json"
    }
    if ($null -ne $Body) {
        $arguments.Body = $Body | ConvertTo-Json -Depth 20 -Compress
    }

    try {
        $response = Invoke-RestMethod @arguments
    }
    catch {
        $status = if ($_.Exception.Response) { [int] $_.Exception.Response.StatusCode } else { "network" }
        $message = $_.Exception.Message
        if ($_.ErrorDetails.Message) {
            try {
                $errorBody = $_.ErrorDetails.Message | ConvertFrom-Json
                $apiMessages = @($errorBody.errors | ForEach-Object { "[$($_.code)] $($_.message)" })
                if ($apiMessages.Count -gt 0) {
                    $message = $apiMessages -join "; "
                }
            }
            catch {
                # Keep the original HTTP message when the response is not JSON.
            }
        }
        throw "Cloudflare API failed ($status): $message"
    }

    if (-not $response.success) {
        $messages = ($response.errors | ForEach-Object { "[$($_.code)] $($_.message)" }) -join "; "
        throw "Cloudflare API rejected the request: $messages"
    }
    return $response
}

if (-not $AccountId) {
    $AccountId = Read-Host "Cloudflare Account ID"
}
if ($AccountId -notmatch "^[a-fA-F0-9]{32}$") {
    [Console]::Error.WriteLine("Account ID phai gom dung 32 ky tu hex.")
    exit 2
}

$secureToken = Read-Host "Cloudflare API Token (Tunnel Write)" -AsSecureString
$script:ApiToken = [System.Net.NetworkCredential]::new("", $secureToken).Password.Trim()
if ([string]::IsNullOrWhiteSpace($script:ApiToken)) {
    [Console]::Error.WriteLine("API Token khong duoc de trong.")
    exit 2
}

try {
    $path = "/accounts/$AccountId/cfd_tunnel/$TunnelId/configurations"
    Write-Host "Reading existing tunnel routes..."
    $current = Invoke-Cloudflare -Method GET -Path $path
    $config = $current.result.config
    $ingress = @($config.ingress)
    if ($ingress.Count -eq 0 -or $ingress[-1].service -notmatch "^http_status:") {
        throw "Tunnel config khong co catch-all rule o cuoi; dung lai de tranh ghi de config la."
    }

    $existing = @($ingress | Where-Object { $_.hostname -eq $Hostname })
    if ($existing.Count -gt 1) {
        throw "Tunnel co nhieu route trung hostname $Hostname."
    }
    if ($existing.Count -eq 1) {
        $existing[0].service = $Origin
        $action = "updated"
    }
    else {
        $newRule = [pscustomobject]@{ hostname = $Hostname; service = $Origin }
        $ingress = @($ingress[0..($ingress.Count - 2)]) + @($newRule) + @($ingress[-1])
        $config.ingress = $ingress
        $action = "added"
    }

    Invoke-Cloudflare -Method PUT -Path $path -Body @{ config = $config } | Out-Null
    Write-Host ""
    Write-Host "OK: tunnel route $action" -ForegroundColor Green
    Write-Host "$Hostname -> $Origin"
    Write-Host ""
    Write-Host "DNS record can chuyen thanh:"
    Write-Host "Type: CNAME"
    Write-Host "Name: lumi"
    Write-Host "Target: $TunnelId.cfargotunnel.com"
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    $script:ApiToken = $null
}
