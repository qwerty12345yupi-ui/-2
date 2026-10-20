$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python) {
    throw "Python 3 was not found. Install Python 3 and run this script again."
}

$ownerChatId = Read-Host "Enter the owner chat ID (send /whoami to your bot to find it)"
if ($ownerChatId -notmatch '^-?\d+$') {
    throw "The owner chat ID must be numeric."
}
$secureToken = Read-Host "Enter the NEW BotFather token (input is hidden)" -AsSecureString
if ($secureToken.Length -eq 0) {
    throw "The token is empty. Revoke the old token with @BotFather and get a new one."
}

$tokenPointer = [IntPtr]::Zero
try {
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $env:TELEGRAM_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    $env:TELEGRAM_OWNER_CHAT_ID = $ownerChatId
    $env:PIZZA_HOST = "127.0.0.1"
    $env:PIZZA_PORT = "8765"
    $env:PIZZA_ALLOWED_ORIGINS = "http://127.0.0.1:8765,http://localhost:8765"

    Push-Location $projectRoot
    try {
        Write-Host "Open the site: http://127.0.0.1:8765/pizza-test.html"
        Write-Host "Health check: http://127.0.0.1:8765/health"
        Write-Host "Keep this window open. Press Ctrl+C to stop."
        & $python.Source ".\pizza_bot.py"
        if ($LASTEXITCODE -ne 0) {
            throw "Сервер завершился с кодом $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Remove-Item Env:TELEGRAM_BOT_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:TELEGRAM_OWNER_CHAT_ID -ErrorAction SilentlyContinue
    Remove-Item Env:PIZZA_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:PIZZA_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:PIZZA_ALLOWED_ORIGINS -ErrorAction SilentlyContinue
    if ($tokenPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
    if ($secureToken) {
        $secureToken.Dispose()
    }
    Remove-Variable ownerChatId -ErrorAction SilentlyContinue
}
