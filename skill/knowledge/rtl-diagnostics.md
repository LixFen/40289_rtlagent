# Offline RTL diagnostic notes

Original project notes applying AMD Ross diagnostic methods, not the Ross MCP
binary or complete knowledge database. Only actual logs establish diagnostics.

## Procedural output net / VRFC
An output assigned in always must be a variable (reg/logic). Fix the declaration,
not intended timing. Compile and elaborate again after repair.

## Reset and nonblocking semantics
Nonblocking assignments read old values. Check reset priority, polarity and
synchronous/asynchronous semantics from the exact prompt. Avoid TB races at
the active edge; sample after updates settle.

## RTL lint and Synth diagnostics
Read canonical CSV from Vivado 2026.1 synth_design -lint. Preserve rule IDs and
source locations. Group cascading errors under the first cause. Clean lint
does not establish specification equivalence or full synthesis success.
Sources: https://github.com/Xilinx/ross-ai-assistant/tree/main/skills/vivado-rtl-lint
https://github.com/Xilinx/ross-ai-assistant/tree/main/skills/vivado-rtl-elaboration-analysis

## XSim failure and license
Compile, elaborate and simulate are distinct. Exit zero alone is not functional
success. Require finite stimulus, actual comparisons and terminal evidence.
Separate TB mistakes, DUT counterexamples, missing licenses and timeouts;
do not repeatedly rewrite RTL for host failures.
Source: https://github.com/Xilinx/ross-ai-assistant/tree/main/skills/vivado-simulate-rtl
