from app.canonical import canonical
from app.clock import instant, stamp
from app.errors import DomainError
from app.modules.coordinates.calibration import dpi_value
from app.modules.intake.contract import FIELDS, valid_code
from app.modules.intake.reader import lines

COLS = {'sheet', 'roll', 'at', 'format', 'dpi', 'rotation', 'origin', 'unit', 'fields'}


def normalize(row):
    data = row['data']
    if set(data) != COLS or not valid_code(data['sheet']) or not valid_code(data['roll']) or not valid_code(data['format']):
        raise DomainError('BAD_SHEET')
    try:
        dpi_value(data['dpi'])
    except DomainError as error:
        raise DomainError('BAD_SHEET') from None
    fields = data['fields']
    if not isinstance(fields, dict) or set(fields) != FIELDS:
        raise DomainError('BAD_SHEET')
    for item in fields.values():
        if (not isinstance(item, dict) or set(item) != {'value', 'box'}
                or type(item['value']) is not str or not item['value'].strip()):
            raise DomainError('BAD_SHEET')
    if type(data['rotation']) is not int or data['rotation'] not in (0, 90, 180, 270):
        raise DomainError('BAD_SHEET')
    if data['origin'] not in ('TL', 'BL') or data['unit'] not in ('px', 'mm'):
        raise DomainError('BAD_SHEET')
    return dict(data, at=stamp(instant(data['at'])), line=row['line'], raw=row['raw'])


def read_sheets(raw):
    rows, encoding = lines(raw, allow_empty=True)
    groups, excluded = {}, []
    accepted = []
    for row in rows:
        if row['code']:
            excluded.append(dict(row, roll=None))
            continue
        try:
            sheet = normalize(row)
            groups.setdefault(sheet['sheet'], []).append(sheet)
        except DomainError:
            excluded.append(dict(row, code='BAD_SHEET', roll=row['data'].get('roll')))
    conflict_ids = {sheet_id for sheet_id, group in groups.items()
                    if len({canonical({key: value for key, value in row.items()
                                       if key not in ('line', 'raw')}) for row in group}) > 1}
    for group in groups.values():
        for row in group:
            if row['sheet'] in conflict_ids:
                excluded.append(dict(line=row['line'], raw=row['raw'], code='SHEET_CONFLICT', roll=row['roll']))
            else:
                accepted.append(row)
    accepted.sort(key=lambda row: (instant(row['at']), row['line']))
    excluded.sort(key=lambda row: row['line'])
    return {'input_rows': len(rows), 'encoding': encoding, 'sheets': accepted,
            'excluded': excluded, 'excluded_rolls': sorted({row['roll'] for row in excluded if row['roll']})}
