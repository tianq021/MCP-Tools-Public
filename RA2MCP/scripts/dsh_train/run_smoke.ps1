# One-shot smoke test of the DSH RA2 local-learning framework.
# Runs: synthetic demo episodes -> dataset -> train (torch/CUDA if available) -> eval -> runtime inference.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\dsh_train\run_smoke.ps1
$ErrorActionPreference = 'Stop'
$ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$TrainPy = 'F:\conda\envs\python_mmx_LLM\python.exe'
if (-not (Test-Path $TrainPy)) { $TrainPy = 'F:\conda\envs\qiaohaotian_new\python.exe' }
if (-not (Test-Path $TrainPy)) { throw "no training python found" }

$InferPy = Join-Path $ROOT '.venv\Scripts\python.exe'
$Train  = Join-Path $PSScriptRoot 'train_bc.py'
$Eps    = Join-Path $ROOT 'reports\dsh-learning\episodes-demo'
$Data   = Join-Path $ROOT 'reports\dsh-learning\dataset-demo'
$Model  = Join-Path $ROOT 'reports\dsh-learning\models\bc-demo'

Write-Host "== 1/5 demo episodes =="
& $TrainPy (Join-Path $PSScriptRoot 'make_demo_episodes.py') --out $Eps
Write-Host "== 2/5 dataset =="
& $TrainPy (Join-Path $PSScriptRoot 'dataset.py') --episodes $Eps --out $Data
Write-Host "== 3/5 train =="
& $TrainPy $Train --data $Data --out $Model --backend auto --epochs 25
Write-Host "== 4/5 offline eval =="
& $TrainPy (Join-Path $PSScriptRoot 'eval_offline.py') --model $Model --data $Data
Write-Host "== 5/5 runtime inference (controller venv, disabled gate) =="
& $InferPy (Join-Path $PSScriptRoot 'infer.py') --model $Model
Write-Host "== 5b/5 runtime inference (enabled, numpy backend) =="
& $InferPy (Join-Path $PSScriptRoot 'infer.py') --model $Model --bench 1000 --enabled
Write-Host "SMOKE OK"
