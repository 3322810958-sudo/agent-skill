"""Synthetic tests only. No personal save data or game code."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import worldbox_ledger as w


def sample():
    return {'saveVersion': 17, 'mapStats': {'name': 'Synthetic World', 'life_dna': 1234, 'world_time': 120.5, 'creaturesCreated': 200},
            'actors_data': [
                {'id': 1, 'asset_id': 'human', 'created_time': 1.0, 'age_overgrowth': 18, 'health': 73, 'lover': 2, 'cityID': 5, 'saved_traits': ['miracle_born']},
                {'id': 2, 'name': 'Example', 'asset_id': 'human', 'created_time': 60.0, 'age_overgrowth': 0, 'health': 0, 'happiness': -25, 'cityID': 5},
                {'id': 3, 'name': 'Example', 'asset_id': 'dog', 'created_time': 70.0}],
            'cities': [{'id': 5, 'name': 'Town', 'kingdomID': 10, 'leaderID': 2}],
            'kingdoms': [{'id': 10, 'name': 'Country', 'kingID': 1, 'past_rulers': [{'id': 1, 'timestamp_ago': 100.0}]}]}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.slot = self.root / 'saves' / 'save1'
        self.slot.mkdir(parents=True)
        self.data = sample()
        self.store()

    def tearDown(self):
        self.temp.cleanup()

    def store(self):
        (self.slot / 'map.wbox').write_bytes(zlib.compress(json.dumps(self.data).encode()))
        (self.slot / 'map.meta').write_text(json.dumps({'mapStats': {'name': 'Synthetic World'}, 'units': 3}), encoding='utf-8')

    def database(self):
        db = self.slot / 'map_stats.s3db'
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE WorldLogMessage(asset_id TEXT, timestamp INTEGER, special1 TEXT, special2 TEXT, special3 TEXT, unit_id INTEGER, kingdom_id INTEGER)')
            conn.execute('INSERT INTO WorldLogMessage VALUES(?,?,?,?,?,?,?)', ('king_killed', 100, 'Country', 'Victim', 'Killer', 7, 10))
            conn.commit()
        return db

    def test_bounded_zlib_and_invalid_streams(self):
        raw = zlib.compress(json.dumps(self.data).encode())
        self.assertEqual(w.decode_save(raw), self.data)
        for bad in (raw[:-2], raw + b'extra', zlib.compress(b'{"actors_data":[],"x":1,"x":2}')):
            with self.assertRaises((w.LedgerError, ValueError, zlib.error)):
                w.decode_save(bad)
        with self.assertRaises(w.LedgerError):
            w.decode_save(zlib.compress(b' ' * 2000), limit=100)

    def test_readonly_database_and_no_created_sidecars(self):
        self.database()
        before = w.inventory(self.slot)
        snap = w.snapshot(self.slot)
        self.assertEqual(before, w.inventory(self.slot))
        self.assertEqual(snap['database']['integrity_check'], ['ok'])
        self.assertEqual(snap['database']['events'][0]['unit_id'], 7)
        self.assertEqual(snap['summary']['retained_events'], 1)

    def test_active_wal_rejected(self):
        db = self.database()
        Path(str(db) + '-wal').touch()
        with self.assertRaisesRegex(w.LedgerError, 'sidecar'):
            w.snapshot(self.slot)

    def test_missing_not_zero_and_duplicate_names_preserved(self):
        snap = w.snapshot(self.slot)
        self.assertNotIn('name', snap['actors'][0]['raw'])
        self.assertNotIn('happiness', snap['actors'][0]['raw'])
        self.assertEqual(snap['actors'][1]['raw']['health'], 0)
        self.assertEqual(snap['actors'][1]['raw']['happiness'], -25)
        self.assertEqual(snap['summary']['human_saved_names'], 1)
        self.assertEqual(snap['actors'][0]['links']['lover']['saved_name'], 'Example')
        self.assertEqual(snap['actors'][0]['derived_age'], 19)
        self.assertIsNone(snap['actors'][2]['derived_age'])

    def test_counters_do_not_create_events(self):
        snap = w.snapshot(self.slot)
        self.assertEqual(snap['map_stats']['creaturesCreated'], 200)
        self.assertEqual(snap['summary']['retained_events'], 0)

    def test_unknown_schema_disables_derivation(self):
        self.data['saveVersion'] = 999
        self.store()
        snap = w.snapshot(self.slot)
        self.assertIsNone(snap['world']['display_year_month'])
        self.assertIsNone(snap['actors'][0]['derived_age'])

    def test_output_source_and_game_overlap_rejected(self):
        snap = w.snapshot(self.slot)
        for target in (self.slot, self.slot / 'reports', w.PLUGIN_ROOT / 'generated'):
            with self.assertRaises(w.LedgerError):
                w.export_snapshot(snap, target)
        game = self.root / 'game'; game.mkdir()
        snap['source']['game_dir'] = str(game)
        with self.assertRaises(w.LedgerError):
            w.export_snapshot(snap, game / 'reports')

    def test_export_roundtrip_and_source_unchanged(self):
        before = w.inventory(self.slot)
        snap = w.snapshot(self.slot)
        out = w.export_snapshot(snap, self.root / 'reports')
        self.assertEqual(w.load_snapshot(out / 'snapshot.json'), snap)
        self.assertEqual(w.inventory(self.slot), before)
        self.assertTrue((out / '人物完整档案.md').is_file())
        self.assertNotEqual(out, w.export_snapshot(snap, self.root / 'reports'))

    def test_world_identity_and_rewind(self):
        snap = w.snapshot(self.slot)
        for key, value in (('identity', None), ('identity', 'life_dna:5678'), ('world_time', 10)):
            other = copy.deepcopy(snap); other['world'][key] = value
            with self.assertRaises(w.LedgerError):
                w.compare(snap, other)

    def test_diff_absent_field_and_disappearance(self):
        before = w.snapshot(self.slot)
        after = copy.deepcopy(before)
        after['entities']['actors_data'][0]['name'] = 'Now Named'
        after['entities']['actors_data'][0]['happiness'] = 0
        after['entities']['actors_data'].pop()
        change = w.compare(before, after)
        self.assertEqual(len(change['disappeared']), 1)
        fields = {f['field']: f for f in change['changed'][0]['fields']}
        self.assertFalse(fields['happiness']['before_present'])
        self.assertEqual(fields['happiness']['after'], 0)
        self.assertNotIn('deaths', change)

    def test_event_multiset_not_set(self):
        self.database()
        before = w.snapshot(self.slot)
        after = copy.deepcopy(before)
        after['database']['events'].append(copy.deepcopy(after['database']['events'][0]))
        self.assertEqual(len(w.compare(before, after)['new_retained_events']), 1)

    def test_source_change_detected(self):
        real = w.inventory
        n = 0
        def change(folder):
            nonlocal n
            n += 1
            if n == 2:
                (self.slot / 'map.meta').write_text('{}')
            return real(folder)
        with mock.patch.object(w, 'inventory', side_effect=change):
            with self.assertRaisesRegex(w.LedgerError, 'changed during'):
                w.snapshot(self.slot)

    def test_duplicate_actor_id_rejected(self):
        self.data['actors_data'][1]['id'] = 1
        self.store()
        with self.assertRaisesRegex(w.LedgerError, 'Duplicate ID'):
            w.snapshot(self.slot)

    def test_legacy_identity_is_explicit(self):
        self.data['saveVersion'] = 13
        del self.data['mapStats']['life_dna']
        self.store()
        self.assertIsNone(w.snapshot(self.slot)['world']['identity'])
        self.assertEqual(w.snapshot(self.slot, world_key='example')['world']['identity_source'], 'user_assertion')

    def test_exact_name_returns_all_matches(self):
        snap = w.snapshot(self.slot)
        out = w.export_snapshot(snap, self.root / 'reports')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = w.main(['npc', '--snapshot', str(out / 'snapshot.json'), '--name', 'Example'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['matches'], 2)

    def test_note_is_user_reported_and_no_source_write(self):
        snap = w.snapshot(self.slot)
        out = w.export_snapshot(snap, self.root / 'reports')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(w.main(['note', '--snapshot', str(out / 'snapshot.json'), '--text', 'Placed units', '--output-root', str(self.root / 'notes')]), 0)
        note = w.read_json(json.loads(output.getvalue())['note_file'])
        self.assertEqual(note['evidence_type'], 'user_reported')

    def test_apply_is_disabled(self):
        before = w.inventory(self.slot)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(w.main(['apply']), 2)
        self.assertEqual(before, w.inventory(self.slot))

    def test_date_and_markdown_boundaries(self):
        self.assertEqual(w.year_month(0, 17), '第1年1月')
        self.assertEqual(w.year_month(59, 17), '第1年12月')
        self.assertEqual(w.year_month(60, 17), '第2年1月')
        self.assertEqual(w.year_month(4023, 17), '第68年1月')
        self.assertNotIn('<', w.mdcell('<script>'))
        self.assertNotIn('[', w.mdcell('[click](file)'))

    def test_discovery_is_bounded_and_handles_named_slot(self):
        game = self.root / 'game'; game.mkdir(); (game / 'worldbox.exe').touch()
        result = w.discover(game, self.slot.parent)
        self.assertEqual(len(result['saves']), 1)
        self.assertEqual(result['saves'][0]['metadata_name'], 'Synthetic World')
        self.assertEqual(len(result['executables']), 1)


if __name__ == '__main__':
    unittest.main()
