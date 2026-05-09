$ErrorActionPreference = "Stop"

$python = if (Test-Path ".\.venvb\Scripts\python.exe") { ".\.venvb\Scripts\python.exe" } else { "python" }
$name = "krr-action-cost-compiler-editor"

& $python -m PyInstaller --clean --onefile --name $name krr_compiler.py
