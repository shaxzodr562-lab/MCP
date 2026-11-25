# Opens a public https link to the locally running web app (port 8000) with ngrok.
# Before the first use (once):
#   1) winget install ngrok.ngrok            (or download from https://ngrok.com/download)
#   2) sign up at https://dashboard.ngrok.com and copy your Authtoken
#   3) ngrok config add-authtoken <YOUR_TOKEN>     <- run it yourself; never share the token
# Every time:
#   1) start the app (run-web.bat or F5) and leave it running
#   2) run this script:  powershell -ExecutionPolicy Bypass -File .\share-ngrok.ps1
# The https://....ngrok-free.app link printed by ngrok works while this window stays open and the computer is on.

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Host "ngrok is not installed. Run:  winget install ngrok.ngrok   then open a NEW terminal." -ForegroundColor Yellow
    exit 1
}
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    Write-Host "App is running (engine: $($h.engine))." -ForegroundColor Green
} catch {
    Write-Host "The app is not running on port 8000. Start run-web.bat first." -ForegroundColor Yellow
    exit 1
}
ngrok http 127.0.0.1:8000
