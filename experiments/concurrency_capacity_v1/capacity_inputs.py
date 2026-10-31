"""Build the capacity measurement sequence from a frozen schedule.

The base schedule is rate-independent: the controller turns ``(model, rate,
seed)`` into arrival offsets at runtime, so the same frozen 4096-request
sequence is valid at every offered rate. For high rates that sequence is too
short to observe backlog, so it is repeated ``repeats`` times with fresh unique
request IDs.

Only ``request_index``, ``bin`` and the derived bookkeeping change. ``key``,
``owner``, ``requester``, ``value_size_bytes``, ``expected_fnv1a64`` and
``schedule_sha256`` are copied verbatim, so the controller's identity check
(``Engine.complete``) still matches every produced request.
"""

IDENTITY_FIELDS = (
    'key', 'owner', 'requester', 'value_size_bytes', 'expected_fnv1a64',
    'schedule_sha256', 'phase',
)
RELABELLED_FIELDS = ('request_index', 'bin', 'round')


def _max_int(rows, field, default=0):
    values = []
    for row in rows:
        value = row.get(field)
        if value not in (None, ''):
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                pass
    return max(values, default=default)


def expand_measurement(rows, repeats):
    """Return ``repeats`` passes of ``rows`` with unique request IDs.

    ``repeats == 1`` returns a shallow copy, leaving the frozen schedule's
    original IDs untouched so a plain run matches the frozen TSV exactly.
    """
    repeats = int(repeats)
    if repeats < 1:
        raise ValueError('repeats must be >= 1')
    if repeats == 1:
        return [dict(row) for row in rows]
    if not rows:
        return []
    next_index = _max_int(rows, 'request_index')
    bins_per_pass = _max_int(rows, 'bin')
    out = []
    for rep in range(repeats):
        for row in rows:
            expanded = dict(row)
            next_index += 1
            expanded['request_index'] = str(next_index)
            if 'bin' in row and row.get('bin') not in (None, ''):
                expanded['bin'] = str(int(row['bin']) + rep * bins_per_pass)
            if 'round' in row and row.get('round') not in (None, ''):
                expanded['round'] = str(row['round'])
            out.append(expanded)
    return out


def validate_expansion(rows, expanded, repeats):
    """Assert the expanded sequence preserves identity and has unique IDs."""
    repeats = int(repeats)
    if repeats == 1:
        assert len(expanded) == len(rows)
    else:
        assert len(expanded) == len(rows) * repeats, (len(expanded), len(rows), repeats)
    indices = [r['request_index'] for r in expanded]
    if len(set(indices)) != len(indices):
        raise AssertionError('duplicate request_index after expansion')
    length = len(rows)
    for position, row in enumerate(expanded):
        base = rows[position % length]
        for field in IDENTITY_FIELDS:
            if field in base:
                if row.get(field) != base.get(field):
                    raise AssertionError(f'{field} changed at position {position}')
    return True