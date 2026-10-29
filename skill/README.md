# RTL design skill

`design.md` supplies reusable interface, timing, arithmetic and FSM guidance.
The controller loads it only in agent mode; the unchanged official baseline never
loads this directory. No task-specific answers or reference testbenches are included.
`agent/tooling/checks.py` diagnoses explicit English bullet port lists and missing modules.
It is deliberately a partial checker, logs checked port count, and does not claim
functional correctness. Symbolic widths require Vivado validation.
