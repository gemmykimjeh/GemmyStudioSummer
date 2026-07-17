param(
  [string]$Log = 'C:\Users\wooja\AppData\Local\Temp\claude\C--GemmyStudioSummer\83c0eccb-4e41-4331-9ab7-755da18bf404\tasks\bwq0zt5l7.output'
)
$Host.UI.RawUI.WindowTitle = 'gdpval baseline (Gemini) - live log'
Write-Host "Tailing gdpval 50-task baseline run:" -ForegroundColor Cyan
Write-Host "  $Log" -ForegroundColor DarkGray
Write-Host "(progress + errors; Ctrl+C to close)`n" -ForegroundColor DarkGray
while (-not (Test-Path -LiteralPath $Log)) { Start-Sleep -Milliseconds 500 }
Get-Content -LiteralPath $Log -Wait -Tail 100 | ForEach-Object {
  $line = $_ -replace "`e\[[0-9;?]*[a-zA-Z]", ''
  if ($line -match 'GENERATOR|REFLECTOR|CURATOR|VERIFIER|\[ace_gdpval\]|\[ace_dual\]|Dual learning|\[PASS|\[ERR|score=|rubric=|mean score|Traceback|Error|RateLimit|429|Running ace') {
    if     ($line -match '\[PASS|score=0\.[5-9]|score=1\.') { Write-Host $line -ForegroundColor Green }
    elseif ($line -match '\[ERR|Error|Traceback|429|RateLimit|failed') { Write-Host $line -ForegroundColor Red }
    elseif ($line -match '\[ace_gdpval\]|\[ace_dual\]')     { Write-Host $line -ForegroundColor Yellow }
    else                                                    { Write-Host $line -ForegroundColor Gray }
  }
}
