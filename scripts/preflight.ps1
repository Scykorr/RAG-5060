# Read-only preflight for local RAG (Windows host). Does not install or delete anything.
$ErrorActionPreference = 'Continue'
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
Write-Host "=== RAG preflight $runId ==="

function Show-FreeGB($letter) {
  $d = Get-PSDrive -Name $letter -ErrorAction SilentlyContinue
  if ($d) { [math]::Round($d.Free/1GB, 2) } else { 'N/A' }
}

Write-Host "C free GB:" (Show-FreeGB 'C')
Write-Host "D free GB:" (Show-FreeGB 'D')
$cFree = Show-FreeGB 'C'
if ($cFree -is [double] -and $cFree -lt 50) {
  Write-Host "FAIL: C: free space < 50 GB ($cFree)" -ForegroundColor Red
} else {
  Write-Host "OK: C: free space policy (>=50 GB)" -ForegroundColor Green
}

Write-Host '--- nvidia-smi ---'
nvidia-smi 2>&1 | Select-Object -First 20

Write-Host '--- wsl ---'
wsl --version 2>&1
wsl -l -v 2>&1

Write-Host '--- .wslconfig ---'
$cfg = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $cfg) { Get-Content $cfg } else { Write-Host 'absent' }

Write-Host '--- Ubuntu VHDX ---'
Get-ChildItem 'D:\RAG5060\wsl' -Recurse -Filter '*.vhdx' -ErrorAction SilentlyContinue |
  Select-Object FullName, @{N='SizeGB';E={[math]::Round($_.Length/1GB,2)}}

Write-Host '--- Docker Desktop processes (should be empty for Engine path) ---'
Get-Process -Name 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue |
  Select-Object Name, Id

Write-Host 'Preflight finished (read-only).'
