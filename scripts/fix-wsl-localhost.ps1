# Fix stale WSL localhost relay: apply mirrored networking, restart WSL, bring RAG stack up.
$ErrorActionPreference = "Continue"
Write-Host "=== wsl --shutdown (applies .wslconfig networkingMode=mirrored) ==="
wsl.exe --shutdown
Start-Sleep -Seconds 5
Write-Host "=== start Ubuntu + docker compose ==="
wsl.exe -d Ubuntu-24.04 -- bash -lc "cd '/mnt/d/RAG 5060' && docker compose --profile core up -d"
Write-Host "=== wait health on 127.0.0.1:18080 ==="
$ok = $false
for ($i = 1; $i -le 60; $i++) {
  try {
    $r = curl.exe -sS -m 3 http://127.0.0.1:18080/api/health 2>$null
    if ($r -match '"status"\s*:\s*"ok"') {
      Write-Host "localhost_ok try=$i body=$r"
      $ok = $true
      break
    }
  } catch {}
  Write-Host "wait=$i"
  Start-Sleep -Seconds 2
}
if (-not $ok) {
  $ip = (wsl.exe -d Ubuntu-24.04 -- bash -lc "ip -4 -o addr show eth0 | awk '{print `$4}' | cut -d/ -f1 | head -1").Trim()
  Write-Host "localhost still broken; try http://${ip}:18080/"
  curl.exe -sS -m 5 "http://${ip}:18080/api/health"
  Start-Process "http://${ip}:18080/"
  exit 1
}
Start-Process "http://127.0.0.1:18080/"
Write-Host "OPENED http://127.0.0.1:18080/"
