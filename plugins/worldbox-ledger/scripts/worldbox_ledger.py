"""Original offline WorldBox evidence reader. No game write implementation."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import zlib

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 64 * 1024 * 1024
MAX_JSON = 256 * 1024 * 1024
MAX_DB = 512 * 1024 * 1024
KNOWN_VERSIONS = {13, 17}
ENTITY_KEYS = ('actors_data', 'kingdoms', 'cities', 'clans', 'alliances', 'wars',
               'plots', 'relations', 'cultures', 'books', 'subspecies', 'languages',
               'religions', 'families', 'armies', 'items')
HUMANS = {'human', 'unit_human', 'baby_human'}
EVENT_LABELS = {
    'king_new': '国王就任', 'king_dead': '国王死亡（未指明原因）',
    'king_killed': '国王被击杀', 'king_left': '国王离任',
    'kingdom_new': '国家建立', 'kingdom_destroyed': '国家灭亡',
    'city_new': '城市建立', 'city_destroyed': '城市毁灭',
    'diplomacy_war_started': '战争开始', 'diplomacy_war_ended': '战争结束',
}


class LedgerError(Exception):
    pass


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def data_root():
    return Path.home() / 'AppData' / 'LocalLow' / 'mkarpenko' / 'WorldBox'


def profile_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'WorldBoxLedger' / 'config.json'


def reject_constant(value):
    raise LedgerError('Non-finite JSON value: ' + value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def read_json(path, limit=MAX_JSON):
    path = Path(path)
    if path.stat().st_size > limit:
        raise LedgerError('JSON exceeds size limit')
    return parse_json(path.read_text(encoding='utf-8-sig'))


def hash_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def decode_save(raw, limit=MAX_JSON):
    if len(raw) > MAX_INPUT:
        raise LedgerError('Compressed save exceeds size limit')
    stream = zlib.decompressobj()
    decoded = stream.decompress(raw, limit + 1)
    if len(decoded) > limit or stream.unconsumed_tail:
        raise LedgerError('Decompressed save exceeds size limit')
    if not stream.eof or stream.unused_data:
        raise LedgerError('Incomplete zlib stream or unexpected trailing bytes')
    result = parse_json(decoded.decode('utf-8-sig'))
    if not isinstance(result, dict) or not isinstance(result.get('actors_data'), list):
        raise LedgerError('Not a supported WorldBox JSON structure')
    return result


def year_month(value, version):
    if version not in KNOWN_VERSIONS or not numeric(value) or value < 0:
        return None
    return f'第{int(value / 60) + 1}年{int(value % 60 / 5) + 1}月'


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def inventory(folder):
    result = []
    for p in sorted(Path(folder).iterdir()):
        if not p.is_file():
            continue
        if p.is_symlink() or p.resolve().parent != Path(folder).resolve():
            raise LedgerError('Linked source file is not accepted: ' + p.name)
        if p.stat().st_size > MAX_DB:
            raise LedgerError('Source file exceeds size limit: ' + p.name)
        st = p.stat()
        with p.open('rb') as f:
            header = f.read(16)
        fmt = ('SQLite 3' if header.startswith(b'SQLite format 3') else
               'PNG' if header.startswith(b'\x89PNG\r\n\x1a\n') else
               'zlib candidate' if len(header) >= 2 and header[0] == 0x78 and int.from_bytes(header[:2], 'big') % 31 == 0 else
               'JSON candidate' if header.lstrip().startswith((b'{', b'[')) else 'unknown')
        result.append({'file': p.name, 'path': str(p.resolve()), 'bytes': st.st_size,
                       'mtime_ns': st.st_mtime_ns, 'mtime_utc': dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(),
                       'sha256': hash_file(p), 'format': fmt})
    return result


def bounded_walk(root, depth=5, max_entries=20000):
    count = 0
    root = Path(root).resolve()
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        relative_depth = len(Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in {'.git', '.venv', '__pycache__', 'node_modules'} and not Path(current, d).is_symlink())
        if relative_depth >= depth:
            dirs[:] = []
        for name in files:
            count += 1
            if count > max_entries:
                raise LedgerError('Discovery entry limit reached; choose a narrower game directory')
            yield Path(current, name)


def discover(game_dir=None, saves_root=None, include_autosaves=False):
    slots, executables = {}, []
    if game_dir:
        if not Path(game_dir).is_dir():
            raise LedgerError('Game directory does not exist')
        for p in bounded_walk(game_dir):
            if p.name.lower() == 'worldbox.exe':
                executables.append(str(p.resolve()))
            if p.name.lower() in {'map.wbox', 'map.wbax'}:
                if include_autosaves or 'autosaves' not in (x.lower() for x in p.parts):
                    slots[str(p.parent.resolve())] = 'game_directory'
    roots = [Path(saves_root) if saves_root else data_root() / 'saves']
    if include_autosaves:
        roots.append(roots[0].parent / 'autosaves')
    for root in roots:
        if not root.is_dir():
            continue
        for p in bounded_walk(root, depth=2):
            if p.name.lower() in {'map.wbox', 'map.wbax'}:
                slots.setdefault(str(p.parent.resolve()), 'user_data')
    found = []
    for path, source in sorted(slots.items()):
        meta = Path(path) / 'map.meta'
        item = {'save_dir': path, 'discovered_in': source, 'files': inventory(Path(path))}
        if meta.is_file():
            try:
                item['metadata_name'] = read_json(meta, MAX_INPUT).get('mapStats', {}).get('name')
            except (LedgerError, ValueError, OSError) as exc:
                item['metadata_error'] = str(exc)
        found.append(item)
    return {'game_directory': str(Path(game_dir).resolve()) if game_dir else None,
            'executables': executables, 'saves': found, 'autosaves_included': include_autosaves}


def read_database(path):
    path = Path(path)
    if not path.exists():
        return {'present': False, 'events': [], 'archived_kingdoms': [], 'tables': []}
    for suffix in ('-wal', '-journal'):
        if Path(str(path) + suffix).exists():
            raise LedgerError('Active SQLite sidecar detected; finish saving before reading')
    with path.open('rb') as f:
        if f.read(16) != b'SQLite format 3\x00':
            raise LedgerError('Invalid SQLite header')
    # immutable avoids creating shared-memory sidecars; WAL saves were explicitly rejected.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            raise LedgerError('SQLite integrity check failed')
        tables = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        result = {'present': True, 'integrity_check': integrity, 'tables': [], 'events': [], 'archived_kingdoms': []}
        for name, sql in tables:
            if sql and 'VIRTUAL TABLE' in sql.upper():
                raise LedgerError('Unexpected virtual table in save database')
            quoted = '"' + name.replace('"', '""') + '"'
            n = db.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
            result['tables'].append({'name': name, 'rows': n, 'schema': sql})
            if name not in {'WorldLogMessage', 'KingdomData'}:
                continue
            if n > 200000:
                raise LedgerError('Event/archive row limit reached')
            cur = db.execute('SELECT * FROM ' + quoted)
            cols = [x[0] for x in cur.description]
            result['events' if name == 'WorldLogMessage' else 'archived_kingdoms'] = [dict(zip(cols, row)) for row in cur]
        return result
    finally:
        db.close()


def keyed(records, label):
    out = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('id'), (str, int)):
            raise LedgerError('Invalid ID record in ' + label)
        key = record['id']
        if key in out:
            raise LedgerError('Duplicate ID in ' + label)
        out[key] = record
    return out


def project_actors(data):
    actors = data['actors_data']
    amap = keyed(actors, 'actors_data')
    countries = keyed(data.get('kingdoms', []), 'kingdoms')
    cities = keyed(data.get('cities', []), 'cities')
    species = keyed(data.get('subspecies', []), 'subspecies')
    roles = collections.defaultdict(list)
    for objects, id_field, role in [(countries.values(), 'kingID', '国王'), (cities.values(), 'leaderID', '城市领导'), (data.get('clans', []), 'chief_id', '氏族首领')]:
        for obj in objects:
            if obj.get(id_field) in amap:
                roles[obj[id_field]].append({'role': role, 'object_id': obj.get('id'), 'object_name': obj.get('name')})
    stats = data.get('mapStats', {})
    world_time = stats.get('world_time', stats.get('worldTime'))
    version = data.get('saveVersion')
    result = []
    for i, a in enumerate(actors):
        city = cities.get(a.get('cityID'), {})
        country_id = a.get('civ_kingdom_id', city.get('kingdomID'))
        links = {'current_kingdom': {'id': country_id, 'name': countries.get(country_id, {}).get('name')},
                 'current_city': {'id': a.get('cityID'), 'name': city.get('name')},
                 'subspecies': {'id': a.get('subspecies'), 'name': species.get(a.get('subspecies'), {}).get('name')}}
        for field in ('lover', 'parent_id_1', 'parent_id_2', 'best_friend_id'):
            if field in a:
                ref = amap.get(a[field])
                links[field] = {'id': a[field], 'resolved': ref is not None, 'saved_name': ref.get('name') if ref else None}
        age = None
        if version in KNOWN_VERSIONS and all(numeric(v) for v in (world_time, a.get('created_time'), a.get('age_overgrowth'))) and world_time >= a['created_time']:
            age = int((world_time - a['created_time']) / 60) + a['age_overgrowth']
        result.append({'source_path': f'$.actors_data[{i}]', 'raw': a,
                       'saved_name_present': bool(a.get('name')), 'roles': roles.get(a['id'], []),
                       'links': links, 'derived_age': age,
                       'age_rule': '0.50.6 elapsed whole years + explicit age_overgrowth' if age is not None else None})
    return result


def snapshot(save_dir, game_dir=None, world_key=None):
    folder = Path(save_dir).resolve()
    if not folder.is_dir():
        raise LedgerError('Save directory does not exist')
    mains = [folder / n for n in ('map.wbox', 'map.wbax') if (folder / n).is_file()]
    if len(mains) != 1:
        raise LedgerError('Expected exactly one map.wbox or map.wbax')
    before = inventory(folder)
    main = mains[0]
    if main.stat().st_size > MAX_INPUT:
        raise LedgerError('Compressed save exceeds size limit')
    data = decode_save(main.read_bytes())
    meta = read_json(folder / 'map.meta', MAX_INPUT) if (folder / 'map.meta').exists() else None
    database = read_database(folder / 'map_stats.s3db')
    people = project_actors(data)
    after = inventory(folder)
    if before != after:
        raise LedgerError('Source changed during reading; finish saving and retry')
    stats = data.get('mapStats', {})
    version = data.get('saveVersion')
    world_time = stats.get('world_time', stats.get('worldTime'))
    life_dna = stats.get('life_dna')
    identity = 'life_dna:' + str(life_dna) if life_dna is not None else ('user:' + world_key if world_key else None)
    actors = data['actors_data']
    warnings = []
    if version not in KNOWN_VERSIONS:
        warnings.append('Unverified save version; derived dates and ages disabled')
    if identity is None:
        warnings.append('No saved life_dna; comparisons require an explicit external world key')
    if not database['present']:
        warnings.append('No map_stats.s3db; missing logs do not prove no historical events')
    if any(not p['saved_name_present'] for p in people):
        warnings.append('Some actor names are not serialized; this reader does not trigger lazy name generation')
    if meta:
        for meta_key, raw_key in [('units', 'actors_data'), ('kingdoms', 'kingdoms'), ('cities', 'cities')]:
            if meta_key in meta and meta[meta_key] != len(data.get(raw_key, [])):
                warnings.append(f'Metadata mismatch: {meta_key}={meta[meta_key]}, actual array={len(data.get(raw_key, []))}')
    coverage = collections.Counter(k for a in actors for k in a)
    terms = [{'source_path': f'$.{typ}[{i}].past_rulers[{j}]', 'object_type': typ,
              'object_id': obj.get('id'), 'object_name': obj.get('name'), 'raw': term}
             for typ in ('kingdoms', 'cities') for i, obj in enumerate(data.get(typ, []))
             for j, term in enumerate(obj.get('past_rulers', []))]
    return {'ledger_schema': 1, 'captured_at_utc': utc_now(),
            'source': {'save_dir': str(folder), 'main_file': str(main), 'game_dir': str(Path(game_dir).resolve()) if game_dir else None,
                       'files': before, 'all_source_files_unchanged': True},
            'world': {'name': stats.get('name'), 'save_version': version, 'world_time': world_time,
                      'display_year_month': year_month(world_time, version), 'identity': identity,
                      'identity_source': 'saved_life_dna' if life_dna is not None else ('user_assertion' if world_key else 'missing')},
            'summary': {'actors': len(actors), 'humans': sum(a.get('asset_id') in HUMANS for a in actors),
                        'saved_names': sum(bool(a.get('name')) for a in actors),
                        'human_saved_names': sum(a.get('asset_id') in HUMANS and bool(a.get('name')) for a in actors),
                        'kingdoms': len(data.get('kingdoms', [])), 'cities': len(data.get('cities', [])),
                        'retained_events': len(database['events']), 'ruler_terms': len(terms)},
            'warnings': warnings, 'map_stats': stats, 'metadata': meta,
            'root_structure': {k: {'type': type(v).__name__, 'count': len(v) if isinstance(v, (list, dict)) else None} for k, v in data.items()},
            'entities': {k: data[k] for k in ENTITY_KEYS if k in data},
            'actors': people, 'actor_field_coverage': dict(sorted(coverage.items())),
            'ruler_terms': terms, 'database': database}


def protected_roots(source=None):
    roots = [PLUGIN_ROOT.resolve(), data_root().resolve()]
    if source:
        if source.get('save_dir'):
            roots.append(Path(source['save_dir']).resolve().parent)
        if source.get('game_dir'):
            roots.append(Path(source['game_dir']).resolve())
    return roots


def safe_destination(path, source=None):
    path = Path(path).expanduser().resolve()
    for root in protected_roots(source):
        if path == root or path.is_relative_to(root):
            raise LedgerError('Output must be outside game, saves and plugin: ' + str(root))
    return path


def new_output(root, source=None):
    root = safe_destination(root, source)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / (dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    folder.mkdir()
    return folder


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def mdcell(value):
    if value is None:
        return '未保存/未验证'
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    value = str(value)
    for old, new in [('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('|', '&#124;'), ('[', '&#91;'), (']', '&#93;'), ('`', '&#96;'), ('\r', ' '), ('\n', ' ')]:
        value = value.replace(old, new)
    return value


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] + ['| ' + ' | '.join(mdcell(v) for v in row) + ' |' for row in rows])


def event_text(e):
    typ = e.get('asset_id')
    a, b, c = (e.get('special' + str(i)) for i in (1, 2, 3))
    if typ == 'king_killed':
        return f'{a} 的国王 {b} 被 {c if c is not None else "未记录对象"} 击杀'
    if typ == 'king_new':
        return f'{b} 成为 {a} 的国王'
    return str(EVENT_LABELS.get(typ, typ)) + '；' + '；'.join(f'special{i}={v}' for i, v in enumerate((a, b, c), 1) if v is not None)


def export_snapshot(snap, root):
    dest = new_output(root, snap['source'])
    write_json(dest / 'snapshot.json', snap)
    version = snap['world']['save_version']
    people = snap['actors']
    rows = [[p['raw']['id'], p['raw'].get('name'), p['raw'].get('asset_id'), p['derived_age'],
             p['raw'].get('health'), p['raw'].get('mana'), p['raw'].get('stamina'),
             p['raw'].get('nutrition'), p['raw'].get('happiness'),
             p['links']['current_kingdom']['name'], p['links'].get('lover'),
             p['raw'].get('saved_traits', p['raw'].get('traits')), p['source_path']] for p in people]
    (dest / '人物索引.md').write_text('# 人物与生物索引\n\n来源：' + mdcell(snap['source']['main_file']) + '\n\n所有值按存档字段保留。未保存值不补为零；当前值不等于上限；年龄仅在输入字段明确时换算。\n\n' + table(['ID', '已保存姓名', '类型', '换算年龄', '当前生命', '当前魔力', '当前体力', '营养原值', '幸福度原值', '当前国家', '恋人关联', '特质', '原始路径'], rows) + '\n', encoding='utf-8')
    details = ['# 逐个人物完整档案', '来源：' + mdcell(snap['source']['main_file'])]
    for p in people:
        details += ['## ' + mdcell(p['raw'].get('name', '姓名未保存')) + ' · ID ' + mdcell(p['raw']['id']),
                    '原始路径：' + mdcell(p['source_path']), table(['原始字段', '原始值'], p['raw'].items()),
                    '关联与派生：' + mdcell({'links': p['links'], 'roles': p['roles'], 'derived_age': p['derived_age']})]
    (dest / '人物完整档案.md').write_text('\n\n'.join(details) + '\n', encoding='utf-8')
    events = snap['database']['events']
    erows = [[i + 1, year_month(e.get('timestamp'), version), e.get('timestamp'), e.get('asset_id'), event_text(e), e.get('kingdom_id'), e.get('unit_id'), e] for i, e in enumerate(events)]
    (dest / '世界历史.md').write_text('# 实际留存的世界历史\n\n来源：' + mdcell(snap['source']['save_dir']) + '/map_stats.s3db → WorldLogMessage。没有记录不等于没有发生。king_killed 的关联单位ID是击杀者；不能一律解释为国王。序号为导出顺序，非数据库主键。\n\n' + table(['序号', '游戏年月', '原始时间', '类型', '解释或原始参数', '国家ID', '关联单位ID', '完整原始记录'], erows) + '\n', encoding='utf-8')
    terms = [[r['object_type'], r['object_name'], r['raw'].get('id'), r['raw'].get('name'), r['raw'].get('timestamp_ago'), r['raw'].get('timestamp_end'), r['source_path']] for r in snap['ruler_terms']]
    (dest / '统治任期.md').write_text('# 统治任期\n\n包含现任记录；任期结束不是死亡证明。来源：' + mdcell(snap['source']['main_file']) + '\n\n' + table(['类别', '国家/城市', '人物ID', '姓名', '开始原值', '结束原值', '原始路径'], terms) + '\n', encoding='utf-8')
    sr = snap['summary']
    body = ['# World-Box 存档记录', '来源：' + mdcell(snap['source']['save_dir']),
            table(['世界字段', '值'], snap['world'].items()), table(['实际数量', '值'], sr.items()),
            '## 现任国王', table(['国家', '国王ID', '已保存姓名', '原始路径'],
            [[role['object_name'], p['raw']['id'], p['raw'].get('name'), p['source_path']] for p in people for role in p['roles'] if role['role'] == '国王']),
            '## 读取结果', '- [人物索引](人物索引.md)\n- [逐个人物完整档案](人物完整档案.md)\n- [世界历史](世界历史.md)\n- [统治任期](统治任期.md)\n- [完整证据快照](snapshot.json)',
            '## 字段覆盖', table(['字段', '出现数', '缺失数'], [[k, n, sr['actors'] - n] for k, n in snap['actor_field_coverage'].items()]),
            '## 限制', '\n'.join('- ' + mdcell(w) for w in snap['warnings']) or '未发现格式警告。',
            '造物主操作未被自动观测。出现人物、累计创建数或神迹特质都不能单独证明一次具体点击；用户说明需另存 user_reported 备注。界面最终属性可能依赖运行时计算。',
            '## 只读校验', '所有源文件读取前后指纹及修改时间一致。',
            table(['文件', '字节', '保存时间UTC', 'SHA-256'], [[r['file'], r['bytes'], r['mtime_utc'], r['sha256']] for r in snap['source']['files']])]
    (dest / '报告.md').write_text('\n\n'.join(body) + '\n', encoding='utf-8')
    return dest


def load_snapshot(path):
    data = read_json(path)
    if not isinstance(data, dict) or data.get('ledger_schema') != 1:
        raise LedgerError('Not a WorldBox Ledger snapshot')
    return data


def compare(before, after):
    left, right = before['world'], after['world']
    if not left['identity'] or left['identity'] != right['identity']:
        raise LedgerError('Missing or different world identity; comparison refused')
    if not numeric(left['world_time']) or not numeric(right['world_time']) or right['world_time'] < left['world_time']:
        raise LedgerError('World time missing or rewound; comparison refused')
    aa = keyed(before['entities']['actors_data'], 'before actors')
    bb = keyed(after['entities']['actors_data'], 'after actors')
    changes = []
    for aid in sorted(aa.keys() & bb.keys(), key=str):
        fields = []
        for field in sorted(aa[aid].keys() | bb[aid].keys()):
            if (field in aa[aid]) != (field in bb[aid]) or aa[aid].get(field) != bb[aid].get(field):
                fields.append({'field': field, 'before_present': field in aa[aid], 'after_present': field in bb[aid],
                               'before': aa[aid].get(field), 'after': bb[aid].get(field)})
        if fields:
            changes.append({'id': aid, 'fields': fields})
    canonical = lambda e: json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    prior = collections.Counter(canonical(e) for e in before['database']['events'])
    new_events = []
    for event in after['database']['events']:
        key = canonical(event)
        if prior[key]:
            prior[key] -= 1
        else:
            new_events.append(event)
    return {'evidence_type': 'snapshot_difference', 'world_identity': left['identity'],
            'before_world_time': left['world_time'], 'after_world_time': right['world_time'],
            'appeared': [bb[k] for k in sorted(bb.keys() - aa.keys(), key=str)],
            'disappeared': [aa[k] for k in sorted(aa.keys() - bb.keys(), key=str)],
            'changed': changes, 'new_retained_events': new_events,
            'limits': ['Appeared is not proof of player placement or birth.', 'Disappeared is not proof of death.',
                       'Same world identity may have branched histories; review chronology.', 'Intermediate unsaved states are not reconstructed.']}


def capabilities():
    return {'plugin': 'worldbox-ledger', 'version': '0.1.0', 'read_only_game': True,
            'implemented': ['discover', 'snapshot', 'npc', 'compare', 'note', 'configure'],
            'game_write_enabled': False, 'runtime_memory_access': False, 'continuous_monitor': False,
            'generates_missing_names': False, 'network_required': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='WorldBox local evidence ledger; game writes disabled.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('capabilities')
    sub.add_parser('apply', help='Reserved and disabled; always fails')
    for command in ('discover', 'snapshot', 'configure'):
        p = sub.add_parser(command)
        p.add_argument('--profile', type=Path, default=profile_path())
        p.add_argument('--game-dir', type=Path)
        p.add_argument('--saves-root', type=Path)
        if command == 'discover':
            p.add_argument('--include-autosaves', action='store_true')
        else:
            p.add_argument('--output-root', type=Path)
        if command == 'snapshot':
            p.add_argument('--save-dir', type=Path, required=True)
            p.add_argument('--world-key')
    p = sub.add_parser('npc')
    p.add_argument('--snapshot', type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--id'); group.add_argument('--name'); group.add_argument('--species'); group.add_argument('--all', action='store_true')
    p = sub.add_parser('compare')
    p.add_argument('--before', type=Path, required=True); p.add_argument('--after', type=Path, required=True)
    p.add_argument('--output-root', type=Path)
    p = sub.add_parser('note')
    p.add_argument('--snapshot', type=Path, required=True); p.add_argument('--text', required=True)
    p.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        command = args.command
        if command == 'apply':
            raise LedgerError('Game/save editing is not implemented or enabled in version 0.1.0')
        config = {}
        if hasattr(args, 'profile') and args.profile.exists():
            config = read_json(args.profile, 1024 * 1024)
            if not isinstance(config, dict):
                raise LedgerError('Invalid local profile')
        game = getattr(args, 'game_dir', None) or config.get('game_dir')
        saves = getattr(args, 'saves_root', None) or config.get('saves_root')
        output = getattr(args, 'output_root', None) or config.get('output_root') or (Path.home() / 'Documents' / 'WorldBoxLedger')
        if command == 'capabilities':
            result = capabilities()
        elif command == 'discover':
            result = discover(game, saves, args.include_autosaves)
        elif command == 'configure':
            if not game or not Path(game).is_dir():
                raise LedgerError('An existing game directory is required')
            config_source = {'game_dir': str(game), 'save_dir': str(Path(saves or data_root() / 'saves') / 'slot')}
            path = safe_destination(args.profile, config_source)
            if path.exists():
                raise LedgerError('Profile exists; use a new profile path to preserve existing configuration')
            safe_destination(output, config_source)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, {'game_dir': str(Path(game).resolve()), 'saves_root': str(Path(saves or data_root() / 'saves').resolve()), 'output_root': str(Path(output).resolve())})
            result = {'profile': str(path)}
        elif command == 'snapshot':
            snap = snapshot(args.save_dir, game, args.world_key)
            dest = export_snapshot(snap, output)
            result = {'report_directory': str(dest), 'snapshot': str(dest / 'snapshot.json'), 'summary': snap['summary'], 'warnings': snap['warnings'], 'all_source_files_unchanged': True}
        elif command == 'npc':
            snap = load_snapshot(args.snapshot)
            records = [p for p in snap['actors'] if args.all or
                       (args.id is not None and str(p['raw']['id']) == args.id) or
                       (args.name is not None and p['raw'].get('name') == args.name) or
                       (args.species is not None and (p['raw'].get('asset_id') in HUMANS if args.species == 'human' else p['raw'].get('asset_id') == args.species))]
            result = {'world': snap['world'], 'source': snap['source']['main_file'], 'matches': len(records), 'records': records}
        elif command == 'compare':
            before, after = load_snapshot(args.before), load_snapshot(args.after)
            result = compare(before, after)
            result['before_snapshot_sha256'] = hash_file(args.before)
            result['after_snapshot_sha256'] = hash_file(args.after)
            if args.output_root:
                safe_destination(args.output_root, before['source'])
                dest = new_output(args.output_root, after['source'])
                write_json(dest / 'comparison.json', result)
                result = {'comparison_file': str(dest / 'comparison.json'), 'appeared': len(result['appeared']), 'disappeared': len(result['disappeared']), 'changed': len(result['changed']), 'new_retained_events': len(result['new_retained_events'])}
        else:
            snap = load_snapshot(args.snapshot)
            if not args.text.strip() or len(args.text) > 20000:
                raise LedgerError('Note must contain 1 to 20000 characters')
            result = {'evidence_type': 'user_reported', 'text': args.text, 'recorded_at_utc': utc_now(),
                      'world': snap['world'], 'snapshot_sha256': hash_file(args.snapshot),
                      'limits': 'User statement, not independently observed game input or a saved world event.'}
            dest = new_output(output, snap['source'])
            write_json(dest / 'user-note.json', result)
            result = {'note_file': str(dest / 'user-note.json'), 'evidence_type': 'user_reported'}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (LedgerError, OSError, ValueError, KeyError, TypeError, sqlite3.Error, zlib.error, RecursionError) as exc:
        print(json.dumps({'error': str(exc), 'game_writes_performed': False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
