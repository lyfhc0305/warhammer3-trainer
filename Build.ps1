$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

python -m unittest discover -s tests -t .
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python src/build_ingame.py
if ($LASTEXITCODE -ne 0) { throw 'In-game pack build failed' }

function Package($name, $entry) {
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name $name --distpath dist `
        --workpath "build\$entry" --specpath build `
        --exclude-module PIL --exclude-module numpy --exclude-module lupa `
        --add-data "$PSScriptRoot\src\schema.json;." `
        --add-data "$PSScriptRoot\src\ingame\*.lua;ingame" `
        --add-data "$PSScriptRoot\src\ingame\list.twui.xml;ingame" `
        --add-data "$PSScriptRoot\output\ingame\zz_wh3_personal_trainer.pack;output/ingame" `
        --hidden-import compression.zstd "src\$entry.py"
    if ($LASTEXITCODE -ne 0) { throw "Packaging failed: $name" }
}
# The desktop console ("战锤3修改器.exe") is retired; drop one left by an older build.
if (Test-Path -LiteralPath 'dist\战锤3修改器.exe') { Remove-Item -LiteralPath 'dist\战锤3修改器.exe' -Force }
Package '战锤3内置修改器安装器' 'ingame_installer'

Copy-Item -LiteralPath reference -Destination dist -Recurse -Force
Copy-Item -LiteralPath docs -Destination dist -Recurse -Force

# A frozen exe can fail where the source runs: the exe rebuilds the Pack by itself.
foreach ($exe in Get-ChildItem -LiteralPath dist -Filter *.exe) {
    $report = Join-Path $PSScriptRoot "build\smoke-$($exe.BaseName).json"
    if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
    $process = Start-Process -FilePath $exe.FullName -ArgumentList '--smoke', "`"$report`"" -PassThru
    if (-not $process.WaitForExit(180000)) {
        $process.Kill()
        throw "Smoke test timed out: $($exe.Name)"
    }
    if (-not (Test-Path -LiteralPath $report)) { throw "Smoke test wrote no report: $($exe.Name)" }
    $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.error) { throw "Smoke test failed: $($exe.Name): $($result.error)" }
    Write-Host "$($exe.Name): v$($result.version), $($result.features) features, bundle $($result.bundle)"
}
