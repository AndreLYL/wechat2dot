$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $tempInstaller = Join-Path ([IO.Path]::GetTempPath()) ("uv-" + [guid]::NewGuid() + ".ps1")
    try {
        Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile $tempInstaller
        & $tempInstaller
    } finally { Remove-Item -LiteralPath $tempInstaller -ErrorAction SilentlyContinue }
    $env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ';' + $env:Path
}
$package = 'https://github.com/AndreLYL/wechat2dot/releases/download/v2.0.0a2/wechat2dot-2.0.0a2-py3-none-any.whl'
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'wechat2dot/dot'))) { $package = $PSScriptRoot }
& uv tool install --upgrade $package
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$toolBin = & uv tool dir --bin
& (Join-Path $toolBin 'wechat2dot.exe') setup @args
exit $LASTEXITCODE
