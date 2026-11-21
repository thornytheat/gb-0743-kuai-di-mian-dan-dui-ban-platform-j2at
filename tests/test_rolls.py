import copy
import json

from app.modules.rolls.service import evaluate
from app.modules.rolls.sheets import read_sheets
from app.modules.tickets.pipeline import run
from app.modules.tickets.service import sample_sources


def roll(batch, name):
    return next(row for row in batch['rolls']['rolls'] if row['roll'] == name)


def attempt(batch, sheet_id):
    return next(row for row in batch['rolls']['attempts'] if row['sheet'] == sheet_id)


# 逐卷归口：每卷只能凭本卷整张校准样张定版面

def test_each_roll_keeps_its_own_verdict_and_evidence(batch):
    rows = {row['roll']: row for row in batch['rolls']['rolls']}
    assert rows['R-01']['decision'] == 'CONTINUE' and rows['R-01']['evidence_sheet'] == 'S-0101'
    assert rows['R-02']['decision'] == 'STOP' and rows['R-02']['code'] == 'FIELD_OUTSIDE_ZONE'
    assert rows['R-03']['decision'] == 'STOP' and rows['R-03']['code'] == 'QUIET_EDGE'
    assert rows['R-05']['decision'] == 'STOP' and rows['R-05']['code'] == 'NO_TEMPLATE'


def test_labels_of_one_roll_share_the_same_sheet_evidence(batch):
    r1 = roll(batch, 'R-01')
    assert r1['attempts'] == 1 and r1['passed'] == 1 and len(r1['labels']) == 13
    assert set(r1['attempt_sheets']) == {'S-0101'}


# 同卷重试：不新增结论，失败仍保留旧版样张记录

def test_repeated_failed_attempts_keep_one_verdict_but_preserve_every_sheet(batch):
    r2 = roll(batch, 'R-02')
    assert r2['attempts'] == 2 and r2['passed'] == 0
    assert r2['attempt_sheets'] == ['S-0201', 'S-0202']
    assert [row['sheet'] for row in batch['rolls']['attempts'] if row['roll'] == 'R-02'] == ['S-0201', 'S-0202']
    assert not attempt(batch, 'S-0201')['ok'] and not attempt(batch, 'S-0202')['ok']


def test_identical_retry_sheet_is_not_a_conflict(batch):
    assert all(row['code'] != 'SHEET_CONFLICT' or row['roll'] == 'R-04' for row in batch['rolls']['excluded'])


# 缺样张不得放行

def test_roll_with_only_conflicting_sheets_holds_its_labels_as_missing_proof(batch):
    [item] = batch['rolls']['proofless']
    assert (item['label'], item['roll'], item['code']) == ('L-022', 'R-04', 'MISSING_PROOF')
    assert all(name != 'R-04' for name in (row['roll'] for row in batch['rolls']['rolls']))
    assert batch['rolls']['coverage']['missing_proof'] == 1


def test_gate_stays_closed_while_any_roll_is_stopped_or_unproven(batch):
    coverage = batch['rolls']['coverage']
    assert coverage == {'released': 13, 'held': 19, 'unassigned': 0, 'missing_proof': 1,
                        'rolls_total': 4, 'rolls_continue': 1, 'rolls_stop': 3}
    assert batch['rolls']['gate_ok'] is False


def test_print_without_roll_attribution_is_missing_proof(batch):
    sources = sample_sources()
    first = next(line for line in sources['prints'].decode('utf-8-sig').splitlines() if line.strip())
    row = json.loads(first)
    row.pop('roll', None)
    sources['prints'] = '\n'.join([json.dumps(row, ensure_ascii=False, separators=(',', ':'))] +
                                  sources['prints'].decode('utf-8-sig').splitlines()[1:]).encode()
    result = run(sources)['rolls']
    [item] = result['unassigned']
    assert item['label'] == row['label'] and item['code'] == 'MISSING_PROOF'
    assert result['gate_ok'] is False


def test_unknown_roll_reference_does_not_borrow_other_roll_evidence(batch):
    sources = sample_sources()
    lines = sources['prints'].decode('utf-8-sig').splitlines()
    row = json.loads(lines[0])
    row['roll'] = 'R-77'
    lines[0] = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
    sources['prints'] = '\n'.join(lines).encode()
    result = run(sources)['rolls']
    [item] = result['unknown_rolls']
    assert (item['label'], item['roll'], item['code']) == (row['label'], 'R-77', 'UNKNOWN_ROLL')
    assert result['gate_ok'] is False


# 同号样张矛盾隔离，坏行连原值隔离

def test_conflicting_sheet_ids_are_quarantined_and_bad_line_kept_raw(batch):
    excluded = {(row['line'], row['code']): row for row in batch['rolls']['excluded']}
    assert excluded[(6, 'SHEET_CONFLICT')]['roll'] == 'R-04'
    assert excluded[(7, 'SHEET_CONFLICT')]['roll'] == 'R-04'
    bad = excluded[(9, 'BAD_JSON_LINE')]
    assert bad['raw'] == '{"sheet":"S-BAD","roll":"R-06","at":"2026-11-20T08:45:00+08:00' and bad['roll'] is None


def test_reader_quarantines_conflicting_duplicate_sheet_ids_only():
    base = ('{{"sheet":"S-1","roll":"R-1","at":"2026-11-20T08:0{idx}:00+08:00","format":"CN100","dpi":300,'
            '"rotation":0,"origin":"TL","unit":"mm","fields":{{"barcode":{{"box":[20,20,60,14],"value":"{bar}"}},'
            '"route":{{"box":[12,46,30,7],"value":"B"}},"recipient":{{"box":[12,70,68,9],"value":"C"}}}}}}')
    raw = (base.format(idx=1, bar='A') + '\n' + base.format(idx=2, bar='X') + '\n').encode()
    result = read_sheets(raw)
    assert result['sheets'] == [] and [row['code'] for row in result['excluded']] == ['SHEET_CONFLICT', 'SHEET_CONFLICT']


def test_identical_retry_sheet_is_kept_not_quarantined():
    line = ('{"sheet":"S-1","roll":"R-1","at":"2026-11-20T08:01:00+08:00","format":"CN100","dpi":300,'
            '"rotation":0,"origin":"TL","unit":"mm","fields":{"barcode":{"box":[20,20,60,14],"value":"A"},'
            '"route":{"box":[12,46,30,7],"value":"B"},"recipient":{"box":[12,70,68,9],"value":"C"}}}')
    result = read_sheets((line + '\n' + line + '\n').encode())
    assert not result['excluded'] and [row['sheet'] for row in result['sheets']] == ['S-1', 'S-1']


def test_bad_dpi_and_rotation_sheets_are_quarantined_as_bad_sheet():
    raw = ('{"sheet":"S-9","roll":"R-9","at":"2026-11-20T08:01:00+08:00","format":"CN100","dpi":"203/300",'
           '"rotation":0,"origin":"TL","unit":"mm","fields":{"barcode":{"box":[20,20,60,14],"value":"A"},'
           '"route":{"box":[12,46,30,7],"value":"B"},"recipient":{"box":[12,70,68,9],"value":"C"}}}\n'
           '{"sheet":"S-8","roll":"R-8","at":"2026-11-20T08:01:00+08:00","format":"CN100","dpi":300,'
           '"rotation":45,"origin":"TL","unit":"mm","fields":{"barcode":{"box":[20,20,60,14],"value":"A"},'
           '"route":{"box":[12,46,30,7],"value":"B"},"recipient":{"box":[12,70,68,9],"value":"C"}}}\n').encode()
    result = read_sheets(raw)
    assert result['sheets'] == []
    assert [row['code'] for row in result['excluded']] == ['BAD_SHEET', 'BAD_SHEET']
    assert set(result['excluded_rolls']) == {'R-8', 'R-9'}


# 规则/模板更新后原判定失效重算：重跑即按新模板重新裁决，旧结论不残留

def test_template_change_recomputes_verdict_without_keeping_stale_decision(batch):
    sources = sample_sources()
    before = run(sources)
    assert roll(before, 'R-03')['code'] == 'QUIET_EDGE'
    templates = copy.deepcopy(before['templates']['versions'])
    for version in templates:
        if version['format'] == 'CN100':
            version['quiet_mm'] = 3
    sources['templates'] = '\n'.join(json.dumps({key: value for key, value in version.items()
                                                 if key not in ('line', 'raw')}, ensure_ascii=False)
                                     for version in templates).encode()
    after = run(sources)
    assert roll(after, 'R-03')['decision'] == 'CONTINUE'
    assert all(item['code'] != 'QUIET_EDGE' for item in after['rolls']['attempts'] if item['roll'] == 'R-03')
    assert roll(after, 'R-01')['decision'] == 'CONTINUE'
    assert after['rolls']['gate_ok'] is False


def test_roll_service_reads_template_versions_directly():
    sources = sample_sources()
    snapshot = run(sources)
    result = evaluate(sources['rolls'], snapshot['templates']['versions'], snapshot['intake']['accepted'])
    assert [row['roll'] for row in result['rolls']] == ['R-01', 'R-02', 'R-03', 'R-05']


# 入口与留档

def test_rolls_endpoint_and_workbench_entry(client, batch_id):
    response = client.get(f'/api/rolls?run={batch_id}')
    assert response.status_code == 200 and response.json['id'] == batch_id
    assert response.json['data']['rolls'] and 'gate_ok' in response.json['data']
    html = client.get('/').get_data(as_text=True)
    assert '换卷校准' in html and '纸卷换卷校准结论' in html


def test_roll_verdict_is_part_of_archived_evidence(client, batch_id):
    bundle = client.get(f'/api/runs/{batch_id}/evidence.json').json['evidence']
    assert bundle['rolls']['coverage']['released'] == 13
    assert {row['roll'] for row in bundle['rolls']['rolls']} == {'R-01', 'R-02', 'R-03', 'R-05'}
