# World-Box 记录助手

A local Codex agent plugin for evidence-based WorldBox save inspection. Python 3.10+ standard library only; no external service, API key or background process.

## Implemented scope

- Bounded game/save discovery and file-format inventory.
- Read-only zlib JSON and SQLite snapshots with before/after SHA-256 verification.
- All retained NPC/creature fields, names and missing-name counts, relation links, current kings, past terms, wars and retained world logs.
- Exact name/ID queries, same-world snapshot comparisons, and clearly labeled user-reported action notes.
- Separate Markdown and JSON outputs. No game/save modifications; future editing is documented but disabled.

The installed skill contains the command workflow. Run `python -B scripts/worldbox_ledger.py --help` or `capabilities` to inspect the entry points. Run `python -B -m unittest discover -s tests -v` from this directory for synthetic tests. Tests never require a real game save.

Do not publish your profile or report directory. Public plugin contents are original instructions, code and synthetic test fixtures only. This project is an unofficial tool and is not affiliated with WorldBox's developer.
