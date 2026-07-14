param(
  [string]$Log = 'C:\Users\wooja\AppData\Local\Temp\claude\C--GemmyStudioSummer\83c0eccb-4e41-4331-9ab7-755da18bf404\tasks\b7sjfobtf.output'
)
$Host.UI.RawUI.WindowTitle = 'gdpval local run - live log'
Write-Host "Tailing gdpval local run log:" -ForegroundColor Cyan
Write-Host "  $Log" -ForegroundColor DarkGray
Write-Host "(filtered to progress + errors; Ctrl+C to close)`n" -ForegroundColor DarkGray
while (-not (Test-Path -LiteralPath $Log)) { Start-Sleep -Milliseconds 500 }
Get-Content -LiteralPath $Log -Wait -Tail 100 | ForEach-Object {
  $line = $_ -replace "`e\[[0-9;?]*[a-zA-Z]", ''      # strip ANSI
  if ($line -match 'GENERATOR|REFLECTOR|CURATOR|\[ace_gdpval\]|\[PASS|\[ERR|score=|rubric=|summary|Traceback|Error|Loading|Running ace') {
    if     ($line -match '\[PASS|score=0\.[5-9]') { Write-Host $line -ForegroundColor Green }
    elseif ($line -match '\[ERR|Error|Traceback|failed')  { Write-Host $line -ForegroundColor Red }
    elseif ($line -match '\[ace_gdpval\]')        { Write-Host $line -ForegroundColor Yellow }
    else                                          { Write-Host $line }
  }
}
