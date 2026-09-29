"""Watcher tests use disposable synthetic saves; never real game data."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import worldbox_ledger as w
import worldbox_sync as s


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saves = self.root / 'saves'
        self.slot = self.saves / 'save1'
        self.slot.mkdir(parents=True)
        self.output = self.root / 'records'
        self.data = {'saveVersion': 17, 'mapStats': {'world_time': 300, 'life_dna': 1, 'name': 'Synthetic'},
                     'actors_data': [{'id': 1, 'asset_id': 'human', 'health': 10}], 'kingdoms': [], 'cities': []}
        self.save()
        self.sync = s.SaveSync(self.saves, self.output)

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        (self.slot / 'map.wbox').write_bytes(zlib.compress(json.dumps(self.data).encode()))

    def test_debounce_changed_record_and_persisted_dedup(self):
        before = w.inventory(self.slot)
        self.assertEqual(self.sync.tick(0), [])
        self.assertEqual(self.sync.tick(5), [])
        self.assertEqual(len(self.sync.tick(6)), 1)
        self.assertEqual(w.inventory(self.slot), before)
        self.assertEqual(self.sync.tick(8), [])
        self.data['actors_data'][0]['health'] = 11
        self.data['mapStats']['world_time'] = 301
        self.save()
        self.sync.tick(9)
        result = self.sync.tick(15)[0]
        diff = w.read_json(result['comparison'])
        self.assertEqual(diff['changed'][0]['fields'][0]['after'], 11)
        restarted = s.SaveSync(self.saves, self.output)
        self.assertIsNone(restarted.sync_slot(self.slot))
        self.assertTrue((self.output / '最新记录.md').is_file())

    def test_incomplete_save_recovers_without_marking_success(self):
        (self.slot / 'map.wbox').write_bytes(b'bad')
        self.sync.tick(0)
        self.sync.tick(6)
        self.assertEqual(self.sync.state['slots'], {})
        self.assertTrue(self.sync.errors)
        self.save()
        self.sync.tick(8)
        self.assertEqual(len(self.sync.tick(14)), 1)
        self.assertFalse(self.sync.errors)

    def test_rewind_and_different_world_start_new_segments(self):
        self.sync.sync_slot(self.slot)
        self.data['mapStats']['world_time'] = 1
        self.save()
        result = self.sync.sync_slot(self.slot)
        self.assertIsNone(result['comparison'])
        self.assertIn('rewound', result['boundary'])
        self.data['mapStats']['life_dna'] = 2
        self.save()
        result = self.sync.sync_slot(self.slot)
        self.assertIsNone(result['comparison'])
        self.assertIn('identity', result['boundary'])

    def test_database_only_update_is_detected_and_nulls_preserved(self):
        db = self.slot / 'map_stats.s3db'
        conn = sqlite3.connect(db)
        try:
            conn.execute('CREATE TABLE WorldYearly50(id INT, timestamp INT, population_civ INT)')
            conn.execute('INSERT INTO WorldYearly50 VALUES(1,50,NULL)')
            conn.commit()
        finally:
            conn.close()
        first = self.sync.sync_slot(self.slot)
        snap = w.load_snapshot(first['snapshot'])
        self.assertIsNone(snap['database']['yearly_raw']['WorldYearly50'][0]['population_civ'])
        conn = sqlite3.connect(db)
        try:
            conn.execute('UPDATE WorldYearly50 SET population_civ=0')
            conn.commit()
        finally:
            conn.close()
        self.assertIsNotNone(self.sync.sync_slot(self.slot))

    def test_mutation_after_debounce_not_committed(self):
        original = w.snapshot
        def changed(*args, **kwargs):
            self.data['mapStats']['world_time'] += 1
            self.save()
            return original(*args, **kwargs)
        with mock.patch.object(w, 'snapshot', side_effect=changed):
            with self.assertRaisesRegex(w.LedgerError, 'after debounce'):
                self.sync.sync_slot(self.slot)
        self.assertEqual(self.sync.state['slots'], {})

    def test_output_safety_and_exclusive_lock(self):
        with self.assertRaises(w.LedgerError):
            s.SaveSync(self.saves, self.slot / 'output')
        with s.process_lock(self.sync.control / 'process.lock'):
            with self.assertRaises(w.LedgerError):
                with s.process_lock(self.sync.control / 'process.lock'):
                    self.fail('second watcher acquired lock')

    def test_removed_slot_preserves_evidence(self):
        self.sync.tick(0)
        self.sync.tick(6)
        (self.slot / 'map.wbox').unlink()
        self.sync.tick(7)
        self.assertEqual(len(self.sync.state['slots']), 1)
        self.assertTrue(self.sync.errors)

    def test_state_commit_failure_does_not_skip_retry(self):
        with mock.patch.object(s, 'atomic_json', side_effect=OSError('disk busy')):
            with self.assertRaises(OSError):
                self.sync.sync_slot(self.slot)
        self.assertEqual(self.sync.state['slots'], {})
        self.assertIsNotNone(self.sync.sync_slot(self.slot))

    def test_worker_loop_detects_save_and_stops(self):
        self.sync.debounce = 0.05
        failures = []
        def run():
            try:
                self.sync.run(interval=0.02)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not self.sync.state['slots'] and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.sync.state['slots'])
            first = self.sync.state['slots'][str(self.slot)]['snapshot']
            self.data['mapStats']['world_time'] += 10
            self.save()
            while self.sync.state['slots'][str(self.slot)]['snapshot'] == first and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertNotEqual(self.sync.state['slots'][str(self.slot)]['snapshot'], first)
        finally:
            (self.sync.control / 'stop.request').touch()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(w.read_json(self.sync.control / 'status.json')['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
