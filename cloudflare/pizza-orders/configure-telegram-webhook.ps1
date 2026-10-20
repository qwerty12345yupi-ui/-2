param(
    [Parameter(Mandatory = $true)]
    [string]$WorkerUrl
)

$parsedUrl = $null
if (-not [Uri]::TryCreate($WorkerUrl, [UriKind]::Absolute, [ref]$parsedUrl) `
    -or $parsedUrl.Scheme -ne "https" `
    -or -not $parsedUrl.Host.EndsWith(".workers.dev", [StringComparison]::OrdinalIgnoreCase)) {
    Write-Error "Provide the HTTPS workers.dev URL printed by Wrangler."
    exit 1
}

$tokenSecure = Read-Host "Enter the new Telegram bot token" -AsSecureString
$secretSecure = Read-Host "Enter the same webhook secret stored in Cloudflare" -AsSecureString
$tokenPointer = [IntPtr]::Zero
$secretPointer = [IntPtr]::Zero

try {
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretSecure)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    $webhookSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    if ([string]::IsNullOrWhiteSpace($token) -or $webhookSecret.Length -lt 32) {
        throw "The token or webhook secret is invalid."
    }

    $body = @{
        url = "$($parsedUrl.AbsoluteUri.TrimEnd('/'))/telegram/webhook"
        secret_token = $webhookSecret
        allowed_updates = @("message")
    } | ConvertTo-Json -Compress

    $result = Invoke-RestMethod `
        -Method Post `
        -Uri "https://api.telegram.org/bot$token/setWebhook" `
        -ContentType "application/json" `
        -Body $body `
        -ErrorAction Stop
    if (-not $result.ok) {
        throw "Telegram did not accept the webhook."
    }
    Write-Output "Telegram webhook configured."
}
catch {
    Write-Error "Webhook setup failed. Check the token, webhook secret, and Worker URL."
    exit 1
}
finally {
    if ($tokenPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
    if ($secretPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
    $tokenSecure.Dispose()
    $secretSecure.Dispose()
    Remove-Variable token, webhookSecret, body, result -ErrorAction SilentlyContinue
}
