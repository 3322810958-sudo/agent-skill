# Future editing boundary

Version 0.1.0 reads game files and writes only independent reports, profiles and user-reported notes. `apply` is intentionally unavailable and returns a nonzero error. No hidden flag enables game writes.

A later explicitly requested editor can add a separate module without changing the evidence reader. Before enabling it:

1. Specify the exact world, actor IDs, allowed fields and requested values; retain whether a field was absent versus zero.
2. Work from a verified snapshot while the game is not saving. Preserve an original backup outside the source directory and validate its digest.
3. Produce a reviewable old/new diff before applying any change. Validate ranges, references, schema version and derived-stat dependencies.
4. Implement writing to a new copy first, using atomic replacement only for a specifically authorized destination. Preserve unknown fields.
5. Reparse the copy, verify unchanged fields, and test it in the appropriate game environment before claiming compatibility. Stop if format or compatibility cannot be established.

Do not bundle game assemblies, decompiled game code, runtime captures, saves or local report data with this plugin. Later runtime name capture or action logging is a separate in-game integration, not an existing capability.
