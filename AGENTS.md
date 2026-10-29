# Project conventions

Final deployment: one AMD Radeon PRO W7900 (gfx1100), 32GB VRAM budget,
full local Vivado 2026.1 toolchain and no external network access at runtime.
Development host checks do not replace clean-image offline acceptance. The agent uses only the model-driven autonomous loop.
See docs/autonomous-agent.md. CLI Vivado/lint/self-test/synthesis and
pinned Ross parser/guides are integrated; Ross MCP and full RAG are not.

Only RTL track. Keep organizer baseline.py and run_baseline.sh byte-identical to
the reference checkout. Do not add task answers or reference testbenches to the
agent workspace, skills, image or model input. Trusted evaluation is outside the
remote model workspace; native Windows smoke tooling lives in ../trusted-evaluation.

Runtime uses Python standard library and the selected local GGUF via ROCm llama.cpp.
Current model/version are in model/MODEL.md and manifest.json; pinned file/hash and
runtime defaults are in serve/runtime.json. Update these declarations when
the user changes the model. Current platform launcher is serve/llamacpp.sh; the SDK10 launcher is historical.
Provisioning scripts are trusted development tools, not solver tools.

Checks: `python -m unittest discover -s tests -v`, `python tools/preflight.py`,
`python tools/freeze.py --output out/source-hashes.json`, then the same freeze command
with `--check`. Loopback HTTP tests may require execution outside the network sandbox.
Use Vivado only for actual RTL compile/simulation/synthesis; do not substitute
iverilog, Verilator or Yosys. Missing tools must be reported, not marked passed.

Keep weights, downloaded runtimes, credentials and logs out of source commits.
Do not globally upgrade the validated ROCm Torch environment: pin Torch's local
ROCm version when applying vendor dependency workarounds. Record failed dependency
checks and distinguish native import/CLI checks from actual model inference.
