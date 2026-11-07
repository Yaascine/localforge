# Run with PowerShell. Installs privately and opens the real OpenClaw UI.
$ErrorActionPreference = 'Stop'
$Version = '0.1.3'
$Base = "https://github.com/Yaascine/localforge/releases/download/v$Version"
$Cache = Join-Path $env:LOCALAPPDATA "LocalForge-Setup\$Version"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$Arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($Arch -eq 'Arm64') {
    $Target = 'aarch64-pc-windows-msvc'
    $Hash = '1efb2654b06e7063d4ac1fc9d49a9bda9a6704d82f035b589a2751a592f14151'
} elseif ($Arch -eq 'X64') {
    $Target = 'x86_64-pc-windows-msvc'
    $Hash = 'a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2'
} else { throw 'Unsupported Windows architecture' }
$Uv = Join-Path $Cache 'uv.exe'
if (-not (Test-Path $Uv)) {
    $Zip = Join-Path $Cache 'uv.zip'
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/0.12.13/uv-$Target.zip" -OutFile $Zip
    if ((Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower() -ne $Hash) { throw 'uv checksum mismatch' }
    Expand-Archive $Zip -DestinationPath $Cache -Force
}
$Source = Join-Path $Cache 'LocalForge'
if (-not (Test-Path (Join-Path $Source 'uv.lock'))) {
    $Archive = "LocalForge-$Version-source.zip"
    $Zip = Join-Path $Cache 'source.zip'
    Invoke-WebRequest "$Base/$Archive" -OutFile $Zip
    $Sums = (Invoke-WebRequest "$Base/SHA256SUMS.txt").Content
    $Expected = ($Sums -split "`n" | Where-Object { $_.Trim().EndsWith("  $Archive") } | Select-Object -First 1) -split '\s+'
    if (-not $Expected -or (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower() -ne $Expected[0]) { throw 'Source checksum mismatch' }
    Expand-Archive $Zip -DestinationPath $Cache -Force
}
Set-Location $Source
Write-Host 'Starting OpenClaw setup. Close the LocalForge desktop app first.'
& $Uv run --frozen --python 3.12 python -m localforge.setup_openclaw @args
exit $LASTEXITCODE
