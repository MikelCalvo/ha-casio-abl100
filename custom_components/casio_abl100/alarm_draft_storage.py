"""Private local values only; never native resources or Apply authority."""
import asyncio
from datetime import time

from .alarm_write_safety import AlarmWriteSafety, _read_record, _atomic_write


def encode(draft, binding):
    return dict(version=1, binding=binding, revision=draft.revision,
                times={str(k): [v.hour, v.minute] for k, v in draft.times.items()},
                enabled={str(k): v for k, v in draft.enabled.items()},
                signal=draft.signal, anchor=draft.anchor)


def decode(record, binding):
    if (type(record) is not dict or set(record) != {
            'version', 'binding', 'revision', 'times', 'enabled', 'signal', 'anchor'}
            or type(record['version']) is not int or record['version'] != 1
            or type(record['binding']) is not str or record['binding'] != binding
            or type(record['revision']) is not int or not 0 <= record['revision'] <= 2**63-1
            or type(record['times']) is not dict or type(record['enabled']) is not dict
            or record['signal'] is not None and type(record['signal']) is not bool):
        raise ValueError('Invalid local draft')
    times, enabled = {}, {}
    for key, value in record['times'].items():
        if (key not in {'1', '2', '3', '4', '5'} or type(value) is not list or len(value) != 2
                or any(type(v) is not int for v in value)
                or not 0 <= value[0] <= 23 or not 0 <= value[1] <= 59):
            raise ValueError('Invalid local time')
        times[int(key)] = time(*value)
    for key, value in record['enabled'].items():
        if key not in {'1', '2', '3', '4', '5'} or type(value) is not bool:
            raise ValueError('Invalid local enable')
        enabled[int(key)] = value
    anchor = record['anchor']
    if anchor is not None:
        if (type(anchor) is not list or len(anchor) != 2 or type(anchor[0]) is not list
                or len(anchor[0]) != 5 or type(anchor[1]) is not bool):
            raise ValueError('Invalid local anchor')
        for slot, row in enumerate(anchor[0], 1):
            if (type(row) is not list or len(row) != 6 or type(row[0]) is not int or row[0] != slot
                    or type(row[1]) is not int or not 0 <= row[1] <= 23
                    or type(row[2]) is not int or not 0 <= row[2] <= 59 or type(row[3]) is not bool
                    or any(v is not None and (type(v) is not int or not 0 <= v <= 255) for v in row[4:])):
                raise ValueError('Invalid local anchor')
        anchor = (tuple(tuple(row) for row in anchor[0]), anchor[1])
    if not (times or enabled or record['signal'] is not None) and anchor is not None:
        raise ValueError('Unexpected local anchor')
    return times, enabled, record['signal'], anchor, record['revision']


class AlarmDraftStorage(AlarmWriteSafety):
    """Independent checked atomic file, serialized revisions and joined executor."""
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self.path = self.path.with_name(self.path.name.replace('_alarm_safety_', '_alarm_draft_'))
        self.lock = asyncio.Lock()

    async def async_load_into(self, draft):
        try:
            record = await self._io(_read_record, self.path)
            if record is None:
                return
            values = decode(record, self.binding)
        except Exception:
            # Foreign/corrupt data is not a draft and never affects safety.
            return
        draft.times, draft.enabled, draft.signal, draft.anchor, draft.revision = values
        draft.saved_revision = draft.revision
        draft.restored = draft.pending

    async def async_save(self, draft):
        async with self.lock:
            while True:
                record = encode(draft, self.binding)
                await self._io(_atomic_write, self.path, record)
                if draft.revision == record['revision']:
                    draft.saved_revision = draft.revision
                    return
