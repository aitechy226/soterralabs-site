"""Anvil Engine Facts — per-engine extractors package.

Each engine has its own module under this package (e.g. `vllm.py`,
`tgi.py`). All extractors inherit from `base.Extractor` (ABC). The
package boundary signals "internal grouping" — modules use plain
names (no leading underscore on `base.py`).

Wave 1A landed only `base.py` + `engines.yaml`. Per-engine modules
ship in Waves 1B-1D.
"""
