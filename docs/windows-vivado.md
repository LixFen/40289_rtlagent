# Local Vivado discovery — 2026-10-11

Installed root: `D:\AMDDesignTools\2026.1\Vivado`.
Version output: Vivado v2026.1 (64-bit), SW Build 6511674.
The four Windows command wrappers exist under `bin`:
`vivado.bat`, `xvlog.bat`, `xelab.bat`, `xsim.bat`.

The installation was absent from the initial shell PATH. To enable discovery in
a PowerShell session, run:

```powershell
$env:XILINX_VIVADO = 'D:\AMDDesignTools\2026.1\Vivado'
$env:PATH = "$env:XILINX_VIVADO\bin;$env:PATH"
python tools/preflight.py
```

For the full vendor environment in a cmd session, use:

```bat
call D:\AMDDesignTools\2026.1\Vivado\settings64.bat
```

No global PATH or machine configuration was modified. Version output and updated
preflight evidence are in `out/vivado-discovery/`. The version command printed the
version successfully but returned exit code 1; this alone does not establish that
compilation, simulation, synthesis, licensing or target-device support work.
The first sandboxed invocation was denied access; the approved invocation produced
the recorded version output. Real RTL judging remains pending.
