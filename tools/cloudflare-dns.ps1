param(
    [Parameter(Position = 0)]
    [string] $VpsIp,

    [Parameter(Position = 1)]
    [string] $ZoneId
)

$ErrorActionPreference = "Stop"
$RootDomain = "fdoc.app"
$RecordName = "lumi.fdoc.app"
$ApiBase = "https://api.cloudflare.com/client/v4"

function Read-ApiToken {
    $secureToken = Read-Host "Cloudflare API Token" -AsSecureString
    return [System.Net.NetworkCredential]::new("", $secureToken).Password
}

function Invoke-Cloudflare {
    param(
        [Parameter(Mandatory)] [ValidateSet("GET", "POST", "PATCH")] [string] $Method,
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
        $arguments.Body = $Body | ConvertTo-Json -Compress
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
        $messages = ($response.errors | ForEach-Object { $_.message }) -join "; "
        throw "Cloudflare API rejected the request: $messages"
    }
    return $response
}

if (-not $VpsIp) {
    $VpsIp = Read-Host "Public IPv4 cua VPS"
}

$parsedIp = $null
if (-not [System.Net.IPAddress]::TryParse($VpsIp, [ref] $parsedIp) -or
    $parsedIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    [Console]::Error.WriteLine("VPS IP phai la mot dia chi IPv4 hop le.")
    exit 2
}

$script:ApiToken = Read-ApiToken
if ([string]::IsNullOrWhiteSpace($script:ApiToken)) {
    [Console]::Error.WriteLine("API Token khong duoc de trong.")
    exit 2
}

try {
    if (-not $ZoneId) {
        Write-Host "Finding zone $RootDomain..."
        $encodedDomain = [Uri]::EscapeDataString($RootDomain)
        $zones = Invoke-Cloudflare -Method GET -Path "/zones?name=$encodedDomain&status=active"
        if ($zones.result.Count -ne 1) {
            throw "Khong tim thay zone active $RootDomain. Token can Zone Read cho domain nay."
        }
        $ZoneId = $zones.result[0].id
    }
    elseif ($ZoneId -notmatch "^[a-fA-F0-9]{32}$") {
        throw "Zone ID phai gom dung 32 ky tu hex."
    }

    $encodedRecord = [Uri]::EscapeDataString($RecordName)
    $records = Invoke-Cloudflare -Method GET -Path "/zones/$ZoneId/dns_records?type=A&name=$encodedRecord"
    if ($records.result.Count -gt 1) {
        throw "Tim thay nhieu hon mot A record cho $RecordName; hay don DNS records truoc."
    }

    $record = @{
        type    = "A"
        name    = $RecordName
        content = $VpsIp
        ttl     = 1
        proxied = $false
        comment = "Lumi backend VPS"
    }

    if ($records.result.Count -eq 1) {
        $recordId = $records.result[0].id
        Write-Host "Updating existing record $RecordName..."
        $result = Invoke-Cloudflare -Method PATCH -Path "/zones/$ZoneId/dns_records/$recordId" -Body $record
        $action = "updated"
    }
    else {
        Write-Host "Creating record $RecordName..."
        $result = Invoke-Cloudflare -Method POST -Path "/zones/$ZoneId/dns_records" -Body $record
        $action = "created"
    }

    Write-Host ""
    Write-Host "OK: $RecordName $action -> $VpsIp" -ForegroundColor Green
    Write-Host "Proxy: DNS only; TTL: Auto"
    Write-Host ""
    Write-Host "Them vao .env production tren VPS:"
    Write-Host "LUMI_DOMAIN=$RecordName"
    Write-Host "ACME_EMAIL=email-that-cua-bro"
    Write-Host ""
    Write-Host "Kiem tra sau khi DNS cap nhat:"
    Write-Host "nslookup $RecordName"
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    $script:ApiToken = $null
}
