# Save field interpretation

Supported verified save schemas: `saveVersion` 13 and 17. Unknown versions may be decoded and inventoried, but derived date/age interpretations must be marked unverified. The installed game version and serialized save version are different numbers.

| Source | Meaning and limits |
| --- | --- |
| map.wbox | zlib stream containing JSON; inspect complete stream, limits and trailing bytes |
| map.wbax | optional autosave candidate; accept only if the same format actually decodes |
| map.meta | JSON metadata; may disagree with the main file; preserve discrepancies |
| map_stats.s3db | SQLite 3; read-only URI, query-only connection; never run arbitrary SQL from the user or save |
| actors_data | retained actor records, including animals; `asset_id=human` is the current human type; older forms include unit_human/baby_human |
| health, mana, stamina | saved current values; not denominators/maxima |
| happiness, nutrition | raw scalars; do not automatically append a percent sign or clamp negatives |
| custom_data_float | stored attribute contributions; not guaranteed final stats with all modifiers |
| saved_traits / traits | saved trait identifiers; retain identifiers if display translations are unverified |
| name | saved name only; absent/empty is not a license to invent a name |
| created_time | creation time, not necessarily birth date (placed units may start as adults) |
| lover / parent_id_1 / parent_id_2 | exact actor references; preserve unresolved IDs |
| age_overgrowth | age offset; only derive age when source world time, created_time and offset are all explicitly present |
| past_rulers | repeated office terms, not a deduplicated person list; can include current ruler |
| database.yearly_raw | raw World/Kingdom/City yearly tables; timestamp is a yearly index, not WorldLogMessage world-time units; keep interval tables separate |

For locally inspected 0.50.6 statistics, coarse yearly tables are produced by database triggers: some fields use maxima, while population, trees and vegetation use rounded averages over earlier input rows. These can be nested interval aggregates and omit unchanged values as SQL NULL. A row labeled 50 is not automatically the exact population on the last day of year 50. Do not sum cumulative maxima, combine overlapping intervals or assume NULL is zero. The reader preserves raw table rows; it does not silently reconstruct the game's chart interpolation. Confirm trigger semantics from the particular source database for quantitative historical claims.

Locally verified 0.50.6 rules (independent inspection of the installed assembly, no game code redistributed):

- `Date.getYear` uses integer truncation of `time / 60`, then adds 1. `Date.getRawDate` uses 5 units per month. Only render year/month for integer event timestamps; don't invent sub-unit timing.
- `ActorData.getAge` adds `age_overgrowth` to integer elapsed years `(world_time - created_time) / 60`.
- `Actor.getName` calls name generation when the stored name is null/empty. Name generation can depend on culture, parents, world seed and templates. This plugin does not reproduce or invoke it.
- `WorldLog.logKingMurder`: special1 is country name, special2 is victim king name, special3 is killer name; unit_id associates with the killer. A king-dead log without a cause does not establish natural death.

All these interpretations are separate from raw values. The report retains paths and original event fields for audit. World identity uses explicitly saved `life_dna`; older saves without it require an explicit external world key for cross-snapshot comparisons. That key is a user assertion and must be labeled accordingly. Same-world branches still need chronological review.

Official references (not bundled third-party source):
- [WorldBox change log](https://www.superworldbox.com/changelog)
- [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins)
