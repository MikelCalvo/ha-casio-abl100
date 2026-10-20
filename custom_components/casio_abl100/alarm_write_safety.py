"""Entry-bound, acknowledged local alarm write barrier; never execution intent."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile

_RESULTS = {'unknown', 'verified', 'unchanged', 'mismatch', 'preservation_mismatch',
            'conflict', 'failed_before_write'}


def _read_record(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(2049)
    except FileNotFoundError:
        return None
    if len(raw) > 2048:
        raise ValueError('Invalid safety record')
    return json.loads(raw)


def _atomic_write(path, record):
    """Return only after atomic replacement and local file/directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                dir=path.parent, prefix=path.name + '.', delete=False) as stream:
            name = stream.name
            json.dump(record, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


def _remove_record(path):
    path.unlink(missing_ok=True)


class AlarmWriteSafety:
    """One local journal per entry. Original native owner serializes its IO."""
    def __init__(self, hass, entry):
        self.hass = hass
        key = hashlib.sha256(entry.entry_id.encode()).hexdigest()
        self.path = Path(hass.config.path('.storage', 'casio_abl100_alarm_safety_' + key))
        self.binding = hashlib.sha256(json.dumps(
            [entry.entry_id, entry.unique_id, entry.data['address'].upper()],
            separators=(',', ':')).encode()).hexdigest()
        self.blocked = True
        self.result = None
        self.submitted = False
        self.closed = False
        self._pending = None

    async def _io(self, function, *args):
        if self.closed:
            raise RuntimeError('Alarm safety owner closed')
        if self._pending is not None:
            raise RuntimeError('Alarm safety IO already pending')
        future = self.hass.async_add_executor_job(function, *args)
        self._pending = future
        cancelled = False
        try:
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    # A thread cannot be cancelled. Join it before owner cleanup
                    # or removal; caller cancellation can never dispatch a SET.
                    cancelled = True
            value = future.result()
            if cancelled or asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            return value
        finally:
            self._pending = None

    async def async_load(self):
        try:
            record = await self._io(_read_record, self.path)
            if record is None:
                self.blocked = False
                return
            if (type(record) is not dict or set(record) != {
                    'version', 'binding', 'blocked', 'result', 'submitted'}
                    or type(record['version']) is not int or record['version'] != 1
                    or type(record['blocked']) is not bool
                    or type(record['submitted']) is not bool
                    or record['result'] not in _RESULTS
                    or type(record['binding']) is not str
                    or len(record['binding']) != 64):
                raise ValueError('Invalid safety record')
            self.blocked = record['blocked'] or record['binding'] != self.binding
            self.result, self.submitted = record['result'], record['submitted']
        except Exception:
            # Corrupt/unreadable storage is not a never-written entry.
            self.blocked = True

    async def _save(self, blocked, result, submitted, guard=None):
        record = dict(version=1, binding=self.binding, blocked=blocked,
                      result=result, submitted=submitted)
        try:
            await self._io(_atomic_write, self.path, record)
            if guard is not None:
                guard()
        except BaseException:
            self.blocked = True
            if not blocked:
                # A cancelled/failed clear may already have replaced the file.
                # Join a conservative re-block before exposing owner completion.
                pending = dict(version=1, binding=self.binding, blocked=True,
                               result=self.result or 'unknown', submitted=self.submitted)
                try:
                    await self._io(_atomic_write, self.path, pending)
                except BaseException:
                    # Original error still fails closed in memory. Local storage
                    # failure can prevent either write; never dispatch afterward.
                    pass
            raise
        self.blocked, self.result, self.submitted = blocked, result, submitted

    async def async_pending(self):
        if self.blocked:
            raise RuntimeError('Alarm reconciliation required')
        # Fail closed in memory even if the local write fails or is cancelled.
        self.blocked = True
        await self._save(True, 'unknown', True)

    async def async_complete(self, result, submitted, guard=None):
        await self._save(False, result, submitted, guard)

    async def async_reconcile(self):
        # GET-only does not rewrite the historical Apply as verified.
        if self.blocked:
            await self._save(False, self.result or 'failed_before_write', self.submitted)

    async def async_close(self):
        self.closed = True
        future = self._pending
        cancelled = False
        while future is not None and not future.done():
            try:
                await asyncio.wait({future})
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def async_remove(self):
        await self.async_close()
        await self.hass.async_add_executor_job(_remove_record, self.path)
