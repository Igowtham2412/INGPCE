param(
    [switch]$Cpu
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = if ($Cpu) {
    Join-Path $ProjectRoot "requirements-face-cpu.txt"
} else {
    Join-Path $ProjectRoot "requirements-face-gpu.txt"
}

if (-not (Test-Path $Python)) {
    throw "The project virtual environment was not found at .venv\Scripts\python.exe"
}

& $Python -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the optional face stack"
}

if (-not $Cpu) {
    & $Python -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.30.0
    if ($LASTEXITCODE -ne 0) {
        throw "Could not activate the pinned GPU ONNX Runtime package"
    }

    & $Python -c "import onnxruntime as ort; providers=ort.get_available_providers(); print('ONNX Runtime providers:', providers); raise SystemExit(0 if 'CUDAExecutionProvider' in providers else 'CUDAExecutionProvider is unavailable; use -Cpu or inspect CUDA/cuDNN DLL compatibility')"
    if ($LASTEXITCODE -ne 0) {
        throw "The GPU face runtime failed its provider check"
    }
} else {
    & $Python -c "import onnxruntime as ort; print('ONNX Runtime providers:', ort.get_available_providers())"
    if ($LASTEXITCODE -ne 0) {
        throw "The CPU face runtime failed its provider check"
    }
}

& $Python -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "The face stack installation has dependency conflicts"
}
