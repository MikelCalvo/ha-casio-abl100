"""Private local values only; never native resources or Apply authority."""
import asyncio
from datetime import time

from .alarm_write_safety import AlarmWriteSafety, _read_record, _atomic_write
from .alarm_write_safety import _atomic_write as _write_metadata


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
        # Separate bounded metadata, never desired values or execution authority.
        self.retirement = AlarmWriteSafety(hass, entry)
        self.retirement.path = self.path.with_name(self.path.name.replace('_alarm_draft_', '_alarm_retirement_'))
        self.fence = None

    def _fence_record(self, revision, status):
        return dict(version=1, binding=self.binding, revision=revision, status=status)

    async def _save_fence(self, revision, status):
        record = self._fence_record(revision, status)
        await self.retirement._io(_write_metadata, self.retirement.path, record)
        self.fence = record

    async def async_prepare(self, revision):
        # Acknowledged before native Apply; a crash leaves GET-only recovery,
        # never a claim of submission and never permission to execute the draft.
        async with self.lock:
            await self._save_fence(revision, 'pending')

    async def async_retire(self, revision):
        async with self.lock:
            await self._save_fence(revision, 'retired')

    async def async_reconcile_retirement(self, draft):
        # Only an explicit complete GET or definite pre-SET baseline conflict
        # reaches this method; never a restore, discard or uncertain submission.
        if self.fence is not None and self.fence['status'] == 'pending':
            async with self.lock:
                await self._save_fence(self.fence['revision'], 'reconciled')
        draft.retirement_blocked = False

    async def async_close(self):
        try:
            await super().async_close()
        finally:
            await self.retirement.async_close()

    async def async_remove(self):
        try:
            await super().async_remove()
        finally:
            await self.retirement.async_remove()

    async def async_load_into(self, draft):
        try:
            fence = await self.retirement._io(_read_record, self.retirement.path)
            if fence is not None:
                if (type(fence) is not dict or set(fence) != {'version', 'binding', 'revision', 'status'}
                        or type(fence['version']) is not int or fence['version'] != 1
                        or fence['binding'] != self.binding
                        or type(fence['revision']) is not int or not 0 <= fence['revision'] <= 2**63-1
                        or fence['status'] not in ('pending', 'retired', 'reconciled')):
                    raise ValueError('Invalid retirement metadata')
                self.fence = fence
                draft.retirement_blocked = fence['status'] == 'pending'
        except Exception:
            draft.retirement_blocked = True
            self.fence = self._fence_record(0, 'pending')
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
        if (self.fence is not None and self.fence['status'] == 'retired'
                and draft.revision == self.fence['revision']):
            draft.clear(draft.revision)
            draft.restored = False
            # Old value file remains inspectable but cannot be an ordinary edit.
            draft.saved_revision = None

    async def async_save(self, draft):
        async with self.lock:
            while True:
                record = encode(draft, self.binding)
                await self._io(_atomic_write, self.path, record)
                if draft.revision == record['revision']:
                    draft.saved_revision = draft.revision
                    return
