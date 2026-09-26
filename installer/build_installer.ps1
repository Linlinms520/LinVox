# 编译 LinVox 安装包（Inno Setup）
#
#   .\installer\build_installer.ps1
#
# 前置：dist\LinVox\LinVox.exe 已由 `pyinstaller LinVox.spec --noconfirm` 产出。
# 产物：installer\Output\LinVox_Setup_x.y.z.exe

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$iss  = Join-Path $PSScriptRoot "LinVox.iss"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)

# Inno Setup（Unicode）只认 UTF-8：给带中文的文件补上 BOM，避免编译后乱码
foreach ($f in @($iss, (Join-Path $PSScriptRoot "RVC_NOTICE.txt"))) {
    $text = [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($f, $text, $utf8Bom)
}

# ISCC 可能在用户级或机器级安装目录里
$compilerDir = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6",
    "$env:ProgramFiles\Inno Setup 6",
    "${env:ProgramFiles(x86)}\Inno Setup 6"
) | Where-Object { Test-Path (Join-Path $_ "ISCC.exe") } | Select-Object -First 1

if (-not $compilerDir) {
    throw "没找到 Inno Setup 6。先装：`n  winget install --id JRSoftware.InnoSetup -e --scope user"
}
$iscc = Join-Path $compilerDir "ISCC.exe"

# 缺简体中文语言文件时自动补：官方编译出来的是无 BOM 的 UTF-8，Inno 需要 BOM
$langTarget = Join-Path $compilerDir "Languages\ChineseSimplified.isl"
if (-not (Test-Path $langTarget)) {
    Write-Host "缺少简体中文语言文件，正在下载..."
    $tmp = Join-Path $env:TEMP "ChineseSimplified.isl"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl" `
                      -OutFile $tmp -UseBasicParsing
    [IO.File]::WriteAllText($langTarget, [IO.File]::ReadAllText($tmp, [Text.Encoding]::UTF8), $utf8Bom)
    Remove-Item $tmp -Force
}

if (-not (Test-Path (Join-Path $root "dist\LinVox\LinVox.exe"))) {
    throw "找不到 dist\LinVox\LinVox.exe，先跑：`n  pyinstaller LinVox.spec --noconfirm"
}

Write-Host "编译 $iss ..." -ForegroundColor Cyan
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC 退出码 $LASTEXITCODE" }

$out = Get-ChildItem (Join-Path $PSScriptRoot "Output") -Filter "*.exe" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    Write-Host "`n安装包已生成：" -ForegroundColor Green
    Write-Host ("  {0}  （{1:N1} MB）" -f $out.FullName, ($out.Length / 1MB))
}
