# Model declaration

Served model ID: `manifest.json.model.name` in the included [manifest.json](manifest.json).
Weights: `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`, revision `fc62412a020e0c1ee3a8f9a12f10aea2102478f0`.
SHA-256: `64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810`.
Generation settings for both agent and baseline are declared in `manifest.json.generation`.
Deployment context and Agent output ceiling: `manifest.json.context.max_model_len`
and `manifest.json.context.max_output_tokens` in the included [manifest.json](manifest.json).
Near-window backend stress completed with 262080 input tokens and 16 output tokens,
no truncation, peak 28.223GiB on W7900D. This does not validate long-output stability
or independent network isolation; see docs/offline-context-validation-20261028.md.
Model provenance, measurements and target validation status: [model/MODEL.md](model/MODEL.md).
