# W7900D validation — 2026-10-11

User-selected model: ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF, IQ3_S (no MTP head).
11,771,546,784 bytes, downloaded directly from ModelScope to the assigned instance.
SHA-256 verified: `64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810`.
Revision: `fc62412a020e0c1ee3a8f9a12f10aea2102478f0`.
The corrected Flash-Next model was not downloaded.

## Actual environment

One visible AMD Radeon PRO W7900D, gfx1100, 51,522,830,336 bytes physical VRAM.
Host starts with Ubuntu 22.04, ROCm 7.1.0, Python 3.10.12 and no torch/vLLM.
Container memory quota is 64 GiB; host-wide sysfs exposes additional unassigned GPUs,
but `/dev/dri` and rocminfo expose only the assigned card1/renderD128 to this container.

Official llama.cpp package: b11552, commit 23b0202a1, ROCm 10.0 build.
Archive SHA-256: `256673debf9313a8e8994a739ccf49de15d4091029b460af1d5889491c63eb84`.
Its Ubuntu ABI requires GLIBC 2.38 / newer libstdc++; Ubuntu 24.04 user-space library
packages were hash-checked and extracted under `/tmp/rtl-runtime/compat/`.
The loader explicitly uses that directory without modifying the host system libraries.
The initial smoke used host ROCm 7.1 libraries with the ROCm 10.0 build.
That phase alone did not prove an SDK10 runtime. The subsequent installation and
model switch below address that distinction. vLLM is checked separately.

Update after initial smoke: PyTorch 2.12.0+rocm10.0.0, Triton
3.8.0+git4cff872c.rocm10.0.0 and ROCm SDK core/libraries/gfx1100 10.0.0 were
installed in `/tmp/rtl-runtime/framework-venv` using Python 3.14.8. A real GPU
tensor operation returned `[2,2,2,2]`; maps confirm the SDK10 HIP library is loaded.
`torch.version.hip` reports its internal build identifier `7.15.26333`.
The model was then tested against SDK10 libraries on a separate port and returned
PASS. The live model process was switched to SDK10 while retaining the API process;
another authenticated solve and health request passed. Host `/opt/rocm` remains 7.1.
Observed VRAM maximum across this single-model switch/startup/request was
**12.451 GiB**, sampled every 0.1 seconds. Reproduction: `serve/llamacpp-sdk10.sh`.
Final health after stability/package checks observed **12.537 GiB**, ready=true.
The manifest records that higher observation. Long-context worst-case memory has
not been established; these observations are not an absolute peak guarantee.
vLLM was then installed and checked separately as recorded below.

## Final inference environment

| Component | Installed version | Executed check |
| --- | --- | --- |
| Python | 3.14.8 | Isolated environment |
| ROCm SDK core/libraries/gfx1100 | 10.0.0 | Loaded HIP library paths checked |
| PyTorch | 2.12.0+rocm10.0.0 | Real W7900D GPU tensor operation |
| torchvision | 0.27.0+rocm10.0.0 | AMD build retained |
| Triton | 3.8.0+git4cff872c.rocm10.0.0 | Package version recorded |
| vLLM | 0.27.1.dev5+rocm10.0.0.gf46a9dfe2.d20260826 | Import, native _C extension, RocmPlatform and serve CLI help passed |
| llama.cpp | b11552 / 23b0202a1 | Selected GGUF real GPU generation and HTTP service |

The vLLM wheel is the AMD-documented 0.27-series development build, not a claim of
stable v0.27.0. The selected GSQ-RCO GGUF is served with llama.cpp; vLLM model
inference on this GGUF was not tested. All binary dependencies used for provisioning
were retained in the server's uv cache; many exact wheels/hashes are also local in
runtime-downloads. This is not proof of clean offline container reproduction.

Dependency consistency is NOT fully clean. `uv pip check` reports two incompatibilities:

- vLLM metadata pins tensorizer 2.10.1; AMD's published workaround requires the
  subsequent upgrade to 2.12.1. That documented override was applied without changing
  vendor metadata. The later install explicitly pinned Torch to its ROCm build.
- amd-quark 0.12.post1 declares Python >=3.11,<3.14, while the AMD vLLM wheel requires
  cp314. Quark quantization was not tested; the selected GGUF path does not use it.

The GPU checks and CLI passed despite these metadata issues. A formal submission
image must resolve or explicitly validate them; no universal vLLM/Quark compatibility
claim is made. Full package versions and check output are retained in evidence.

## Initial smoke checks

- Explicit device listing reports `ROCm0: AMD Radeon PRO W7900D`.
- Model server forced to use `--device ROCm0 --n-gpu-layers 99`; no CPU fallback accepted.
- Shared model/context for agent and baseline: 16384 context, temperature 0, top_p 1,
  maximum output 8192, thinking disabled for both at server level.
- Direct inference returned the requested `PASS`.
- Authenticated health: ready=true, track=rtl, correct model alias.
- Six solve requests on one running backend, each with one actual model call.
- Sampled VRAM maximum during requests: **12.431 GiB**; sampled GPU utilization reached 100%.
  Sampling interval 0.2 seconds, starting after model loading. This is an observed
  request-phase maximum, not a guarantee of the loading-phase or hardware peak.
- Server log shows roughly 36 generated tokens/s on these short RTL outputs.

| Public task | Agent HTTP seconds | Baseline HTTP seconds |
| --- | ---: | ---: |
| popcount8 | 8.378 | 2.821 |
| detect_1101 | 14.588 | 4.974 |
| lfsr8 | 4.573 | 4.273 |

References and testbenches stayed on the local trusted evaluator host. Only prompt
text was uploaded. The remote has no Vivado; these requests verify generation and
protocol, not L1/L2/L3 or functional correctness. No gain or accuracy claim is made
from three public examples. Physical 32GB-card validation remains outstanding.

## Subsequent stability and local Vivado checks

200 real canary requests passed before the runtime change. A second 200 passed
with the final diagnostic and SDK10 model process: 283.776 seconds total, maximum
request 1.568 seconds, 100 agent / 100 baseline. Model PID 2785 and API PID 1363
remained unchanged throughout the second run. Canaries verify process/protocol
stability, not diverse-task accuracy or long-context stress.

All three official references passed local Windows Vivado 2026.1 xvlog/xelab/xsim
and the official synthesis Tcl on xczu3eg-sbva484-1-e. Fixtures stayed outside the
remote model workspace. This is a native Windows smoke check, not a Linux score run.

| Initial generated sample | Agent | Baseline |
| --- | --- | --- |
| popcount8 | Compile/simulation/synthesis pass, 0/257 mismatches | Same |
| detect_1101 | Compile/simulation/synthesis pass, 0/226 mismatches | Compile pass, 4/226 mismatches |
| lfsr8 | Compile failed: procedural assignment to wire output | Same failure |

A generic output-variable diagnostic and skill guidance were added; re-generated
LFSR passed compile/simulation/synthesis, 0/229 mismatches. That later artifact is
kept as agent-repaired; initial results were not replaced. No aggregate accuracy or
gain is estimated across revisions. Twenty engineering tests passed after the change.
Local evaluator evidence: trusted-evaluation/w7900d-20261011.

## Evidence and running files

Local retained evidence: `out/remote-validation/evidence/`, including model metadata,
device probe, direct response, health, six sources/traces, GPU samples and server log.
The evidence archive excludes the API bearer token and all SSH material.

Remote model: `/tmp/rtl-runtime/model-27b/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`.
Remote engine: `/tmp/rtl-runtime/llama/llama-b11552/`.
Model endpoint: `127.0.0.1:8000/v1`; task endpoint: `127.0.0.1:7860`.
API token is stored only remotely at `/tmp/rtl-runtime/api-token` with mode 0600.
Process IDs: `/tmp/rtl-runtime/llama-server.pid`, `api.pid`, `telemetry.pid`.
All remote files are in temporary instance storage and may not survive termination.
