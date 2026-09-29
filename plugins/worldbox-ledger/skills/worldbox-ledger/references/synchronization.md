# Save synchronization

The Python worker is `scripts/worldbox_sync.py run|status|stop --profile <local JSON profile>` from the plugin root. `run --once` reads stable changed slots once and exits. A long-running worker is a local file-monitoring feature of this plugin, without a model call or network service.

The Windows wrapper `scripts/worldbox-sync.ps1 -Action Start|Status|Stop [-Profile <path>]` launches the Python worker hidden. Paths and logs remain outside the plugin. It does not add a Windows service, scheduled task or login startup. Restart it after reboot, or use the installed skill to start it again. `Status` checks both heartbeat freshness and process existence; the underlying process lock is the final guard against duplicates.

Only immediate manual-save subdirectories of the configured saves root are monitored. A new slot is detected automatically. Autosaves elsewhere are not monitored. No hooks are installed in the game. Two saves less than the stability delay apart may produce one final snapshot. A state that is overwritten before it can be read cannot be recovered.

All tracked source files are hashed, and every read is checked before/after. Hash-identical rewrites are skipped. Incomplete saves or SQLite sidecars produce an error with a 30-second retry; a new file signature restarts the 6-second stability wait. Latest pointers advance only after the full snapshot has been exported and state committed atomically. Missing slots keep their last evidence and are shown in status errors. Old snapshots are never automatically deleted, so disk use grows with each changed save.

World identity and time direction gate comparisons. A rewind or world replacement is preserved as a new segment, not a cross-world difference. A same-identity forward branch may still be ambiguous; the comparison carries this limitation. An actor disappearing from the file is not by itself proof of death.

No player mouse events, unsaved simulation states or automatic prose generation are captured. Finished historical fiction remains bound to its specific evidence snapshot.
