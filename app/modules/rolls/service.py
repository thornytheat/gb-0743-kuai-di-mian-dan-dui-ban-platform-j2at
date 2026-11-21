from collections import Counter, defaultdict

from app.clock import instant
from app.errors import DomainError
from app.modules.coordinates.box import scalar
from app.modules.coordinates.calibration import transform
from app.modules.occlusion.rules import inspect
from .sheets import read_sheets


def version_at(versions, fmt, at):
    eligible = [version for version in versions if version['format'] == fmt
                and instant(version['start']) <= at < instant(version['end'])]
    return max(eligible, key=lambda version: version['revision'], default=None)


def judge_sheet(sheet, versions):
    version = version_at(versions, sheet['format'], instant(sheet['at']))
    attempt = {'sheet': sheet['sheet'], 'roll': sheet['roll'], 'at': sheet['at'], 'format': sheet['format'],
               'line': sheet['line'], 'version_line': None, 'issues': [], 'quiet': None,
               'ok': False, 'code': None, 'boxes': None}
    if version is None:
        attempt['code'] = 'NO_TEMPLATE'
        return attempt
    attempt['version_line'] = version['line']
    try:
        width, height = map(scalar, version['paper'])
        _, boxes = transform(sheet, width, height)
        issues, quiet = inspect(boxes, version)
    except DomainError as error:
        attempt['code'] = error.code
        return attempt
    attempt.update(issues=issues, quiet=quiet,
                   boxes={field: box.output() for field, box in boxes.items()},
                   ok=not issues)
    return attempt


def evaluate(raw_rolls, versions, accepted_prints):
    intake_sheets = read_sheets(raw_rolls)
    attempts = [judge_sheet(sheet, versions) for sheet in intake_sheets['sheets']]
    by_roll = defaultdict(list)
    for attempt in attempts:
        by_roll[attempt['roll']].append(attempt)

    rolls = []
    for roll in sorted(by_roll):
        history = by_roll[roll]
        latest = history[-1]
        if latest['ok']:
            decision, code = 'CONTINUE', None
        elif latest['code']:
            decision, code = 'STOP', latest['code']
        else:
            decision, code = 'STOP', latest['issues'][0]['code']
        rolls.append({'roll': roll, 'decision': decision, 'code': code,
                      'attempts': len(history), 'passed': sum(item['ok'] for item in history),
                      'evidence_sheet': latest['sheet'], 'evidence_line': latest['line'],
                      'attempt_sheets': [item['sheet'] for item in history],
                      'latest_at': latest['at'], 'labels': []})

    verdict = {item['roll']: item for item in rolls}
    proofless_rolls = set(intake_sheets['excluded_rolls'])
    unknown, unassigned, proofless = [], [], []
    grouped = Counter()
    for entry in accepted_prints:
        data = entry['data']
        roll = data.get('roll')
        grouped[roll] += 1
        if roll is None:
            unassigned.append({'label': data['label'], 'line': entry['line'], 'code': 'MISSING_PROOF'})
        elif roll in verdict:
            verdict[roll]['labels'].append(data['label'])
        elif roll in proofless_rolls:
            proofless.append({'label': data['label'], 'roll': roll, 'line': entry['line'], 'code': 'MISSING_PROOF'})
        else:
            unknown.append({'label': data['label'], 'roll': roll, 'line': entry['line'], 'code': 'UNKNOWN_ROLL'})

    released = sum(grouped[roll] for roll in verdict if verdict[roll]['decision'] == 'CONTINUE')
    held = sum(grouped[roll] for roll in verdict if verdict[roll]['decision'] == 'STOP')
    missing = grouped[None] + len(proofless) + len(unknown)
    gate_ok = not unknown and not unassigned and not proofless and all(item['decision'] == 'CONTINUE' for item in rolls)
    return {'input_rows': intake_sheets['input_rows'], 'encoding': intake_sheets['encoding'],
            'excluded': intake_sheets['excluded'], 'attempts': attempts, 'rolls': rolls,
            'unknown_rolls': unknown, 'unassigned': unassigned, 'proofless': proofless,
            'reasons': dict(Counter(row['code'] for row in intake_sheets['excluded'])),
            'coverage': {'released': released, 'held': held, 'unassigned': grouped[None],
                         'missing_proof': missing,
                         'rolls_total': len(rolls), 'rolls_continue': sum(item['decision'] == 'CONTINUE' for item in rolls),
                         'rolls_stop': sum(item['decision'] == 'STOP' for item in rolls)},
            'gate_ok': gate_ok}
