# Logging Plan (Local-First)

Goal: keep logs structured and easy to rotate without adding heavy deps.

## Format
- JSON lines (one JSON object per line)
- Required fields: `ts`, `level`, `event`, `context`

## Where
- All logs go to `logs/` (gitignored).
- Naming: `logs/<tool>_<YYYYMMDD>.log`

## Rotation
- Rotate daily by filename.
- Keep 7 days locally; archive older if needed.

## Minimal Implementation (Python)
Use the standard library `logging` module with JSON formatting.
If a tool is small, log to stdout and redirect:
```
python3 tool.py | tee -a logs/tool_$(date -u +%Y%m%d).log
```
