param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$manage = Join-Path $root 'manage.py'

if (-not (Test-Path $python)) {
    throw "Nerastas venv Python: $python. Pirma sukurk/aktyvuok venv (.venv)."
}

& $python $manage @Args
