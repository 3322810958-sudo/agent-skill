"""Opt-in local save watcher. All mutations are confined to the report directory."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import worldbox_ledger as w


def atomic_json(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def process_lock(path):
    # Lock our own report-side file, never a source game/save file.
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise w.LedgerError('A sync process is already running for this output directory') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def file_signature(slot):
    result = []
    for path in sorted(slot.iterdir()):
        if path.is_file():
            stat = path.stat()
            result.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(result)


def content_key(files):
    # mtime-only updates do not require another identical evidence export.
    return hashlib.sha256(json.dumps(
        sorted((f['file'], f['bytes'], f['sha256']) for f in files),
        ensure_ascii=False).encode()).hexdigest()


class SaveSync:
    def __init__(self, saves_root, output_root, game_dir=None, debounce=6):
        self.saves = Path(saves_root).resolve()
        self.game = game_dir
        self.output = w.safe_destination(output_root, {'save_dir': str(self.saves / 'slot'), 'game_dir': game_dir})
        self.output.mkdir(parents=True, exist_ok=True)
        self.control = self.output / '.sync'
        self.control.mkdir(exist_ok=True)
        self.state_path = self.control / 'state.json'
        self.state = w.read_json(self.state_path) if self.state_path.exists() else {'schema': 1, 'slots': {}}
        if self.state.get('schema') != 1 or not isinstance(self.state.get('slots'), dict):
            raise w.LedgerError('Invalid sync state; preserve and inspect it before restarting')
        self.pending = {}
        self.debounce = debounce
        self.errors = {}

    def slots(self):
        if not self.saves.is_dir():
            raise w.LedgerError('Configured saves directory is unavailable')
        # Manual save slots only. No recursive drive scan or autosave assumption.
        return [p for p in sorted(self.saves.iterdir()) if p.is_dir() and
                not p.is_symlink() and p.resolve().parent == self.saves and
                any((p / n).is_file() for n in ('map.wbox', 'map.wbax'))]

    def sync_slot(self, slot):
        key = str(slot.resolve())
        fingerprint = content_key(w.inventory(slot))
        previous = self.state['slots'].get(key)
        if previous and previous['fingerprint'] == fingerprint:
            return None
        snap = w.snapshot(slot, self.game)
        if content_key(snap['source']['files']) != fingerprint:
            raise w.LedgerError('Source changed after debounce; waiting for a stable save')
        dest = w.export_snapshot(snap, self.output)
        comparison = None
        boundary = 'initial_snapshot'
        if previous:
            prior_path = Path(previous['snapshot'])
            # Only read previous evidence that this output directory owns.
            if prior_path.resolve().is_relative_to(self.output) and prior_path.is_file():
                prior = w.load_snapshot(prior_path)
                try:
                    comparison = w.compare(prior, snap)
                    comparison['before_snapshot_sha256'] = w.hash_file(prior_path)
                    comparison['after_snapshot_sha256'] = w.hash_file(dest / 'snapshot.json')
                    w.write_json(dest / 'comparison.json', comparison)
                    boundary = 'same_world_forward'
                except w.LedgerError as exc:
                    boundary = 'new_history_segment: ' + str(exc)
            else:
                boundary = 'previous_evidence_unavailable'
        entry = {'fingerprint': fingerprint, 'snapshot': str(dest / 'snapshot.json'),
                 'snapshot_sha256': w.hash_file(dest / 'snapshot.json'), 'world': snap['world'],
                 'summary': snap['summary'], 'synced_at_utc': w.utc_now(), 'boundary': boundary,
                 'comparison': str(dest / 'comparison.json') if comparison is not None else None}
        # The evidence export is complete before the latest pointer is committed.
        next_state = copy.deepcopy(self.state)
        next_state['slots'][key] = entry
        atomic_json(self.state_path, next_state)
        self.state = next_state
        self.write_index()
        return entry

    def write_index(self):
        lines = ['# 最新同步记录', '此入口随成功读取的存档更新。旧快照保留；小说不自动改写。',
                 '监测手动存档槽，每2秒检查，稳定6秒后读取。快速连续保存可能合并；不能还原未保存操作。']
        for slot, entry in sorted(self.state['slots'].items()):
            folder = Path(entry['snapshot']).parent
            relative = folder.relative_to(self.output).as_posix()
            lines.extend(['## ' + w.mdcell(entry['world'].get('name')) + ' · ' + w.mdcell(Path(slot).name),
                          w.mdcell(entry['world'].get('display_year_month')),
                          '[存档报告](' + relative + '/报告.md) · [人物档案](' + relative + '/人物完整档案.md) · [历史事件](' + relative + '/世界历史.md)',
                          '同步时间UTC：' + entry['synced_at_utc'],
                          '记录边界：' + w.mdcell(entry['boundary'])])
            if entry['comparison']:
                lines.append('[与上次的变化](' + relative + '/comparison.json)')
        # Atomic text replacement; output is an explicitly managed index only.
        target = self.output / '最新记录.md'
        temp = self.control / ('index-' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text('\n\n'.join(lines) + '\n', encoding='utf-8')
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        results = []
        for slot in self.slots():
            key = str(slot)
            try:
                signature = file_signature(slot)
                pending = self.pending.get(key)
                if pending is None or pending['signature'] != signature:
                    self.pending[key] = {'signature': signature, 'since': now, 'done': False, 'retry': now}
                    continue
                if pending['done'] or now - pending['since'] < self.debounce or now < pending['retry']:
                    continue
                entry = self.sync_slot(slot)
                pending['done'] = True
                self.errors.pop(key, None)
                if entry:
                    results.append(entry)
            except (w.LedgerError, OSError, ValueError, TypeError, KeyError, w.sqlite3.Error, w.zlib.error) as exc:
                self.errors[key] = {'message': str(exc), 'at_utc': w.utc_now()}
                if key in self.pending:
                    self.pending[key]['retry'] = now + 30
        present = {str(p) for p in self.slots()}
        for missing in set(self.pending) - present:
            self.pending.pop(missing, None)
            self.errors[missing] = {'message': 'Save slot is unavailable; last evidence retained', 'at_utc': w.utc_now()}
        return results

    def status(self, status):
        atomic_json(self.control / 'status.json', {'status': status, 'pid': os.getpid(),
                    'heartbeat_utc': w.utc_now(), 'saves_root': str(self.saves),
                    'slot_count': len(self.state['slots']), 'errors': self.errors})

    def run(self, interval=2, once=False):
        with process_lock(self.control / 'process.lock'):
            stop = self.control / 'stop.request'
            stop.unlink(missing_ok=True)
            try:
                self.write_index()
                while not stop.exists():
                    try:
                        self.tick()
                        self.errors.pop('_root', None)
                    except (OSError, w.LedgerError) as exc:
                        self.errors['_root'] = {'message': str(exc), 'at_utc': w.utc_now()}
                    self.status('running')
                    if once and self.pending and all(p['done'] for p in self.pending.values()):
                        break
                    if once and self.errors:
                        raise w.LedgerError('Sync failed; see status.json errors')
                    if once and not self.pending:
                        break
                    time.sleep(interval)
            finally:
                self.status('stopped')


def main():
    parser = argparse.ArgumentParser(description='Local WorldBox save synchronization; no game writes.')
    parser.add_argument('command', choices=['run', 'status', 'stop'])
    parser.add_argument('--profile', type=Path, default=w.profile_path())
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        config = w.read_json(args.profile, 1024 * 1024)
        sync = SaveSync(config['saves_root'], config['output_root'], config.get('game_dir'))
        if args.command == 'run':
            sync.run(once=args.once)
        elif args.command == 'stop':
            (sync.control / 'stop.request').touch()
            print('Stop requested; source saves remain unchanged.')
        else:
            path = sync.control / 'status.json'
            print(path.read_text(encoding='utf-8') if path.exists() else '{"status":"not_started"}')
        return 0
    except (w.LedgerError, OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
