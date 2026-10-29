# Local validation — 2026-10-10

Update 2026-10-11: Vivado 2026.1 was located at
`D:\AMDDesignTools\2026.1\Vivado`; its wrappers were not on the initial PATH.
Version output and session-only tool discovery have now been checked. See
`windows-vivado.md`. The earlier PATH result below remains a record of that check,
not evidence that Vivado was uninstalled. Subsequent GPU and local Windows Vivado
smoke results are recorded in w7900d-validation.md. After the observed procedural
output-net failure was reproduced and fixed generically, the expanded regression
suite passed all 20 tests in 85.602 seconds; log: out/local-tests-20261011.txt.

Environment: native Windows, Python 3.13.7. No AMD instance is available.

Executed `python -m unittest discover -s tests -v`: **17 tests passed**,
68.618 seconds. This includes 200 sequential HTTP solve requests in one service
process, alternating agent/baseline, plus:

- Authentication on both endpoints and malformed request rejection.
- Exact task_id round trip, random scratch paths, public-text-only CLI staging.
- Official baseline: one model request, fixed generation settings, no skill/tools.
- Interface repair, numeric widths/directions, grouped ANSI/non-ANSI declarations.
- Null model content and rejection of generated external file directives.
- Queue budget, slow-model termination and subsequent service recovery.
- Fresh/stale/over-budget GPU telemetry readiness using synthetic telemetry.
- Reference evaluation gate rejecting missing, failed and license-error verdicts.

The initial sandbox denied loopback socket connections (Windows error 10013).
Tests were rerun with approved execution outside that network restriction. No
external inference API or actual GPU was used. Full local test output is in
`out/local-tests.txt` (ignored runtime evidence).

Executed Python `compileall` for agent, serve, tools and tests; passed.
Executed evaluation CLI `--help`; argument parsing works. The real evaluation
driver has not been exercised against Vivado or a GPU model server.
Executed preflight: xvlog/xelab/xsim/vivado/vllm were absent from PATH;
Docker CLI exists, but daemon/container build was not tested.
Linux shell wrappers and Dockerfile have not been executed on their target platform.

Byte SHA-256 comparison against organizer checkout: both match exactly.

| File | SHA-256 |
| --- | --- |
| baseline.py | 5ce8ea355ee9743b8c0280c8cece5ccd4da8fe52e987a49ec5f01980f65c6416 |
| run_baseline.sh | f3fdfd218a55c337cf7b585729886d209f09e46c2c9b5b33c04e8e4b490bb089 |

Source freeze generation and verification passed. The current inventory is saved
in `out/source-hashes.json`; regenerate it after any source/document change.
This is a local source snapshot, not a final model/runtime/container freeze.

No real RTL grade, model accuracy, gain, throughput, GPU utilization, peak VRAM,
ROCm compatibility, offline reproduction or final delivery result is asserted.
