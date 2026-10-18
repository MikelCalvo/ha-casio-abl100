"""Synthetic native steps contract; existing parser/transport remain real."""
import asyncio
from importlib import import_module, util
from types import SimpleNamespace

import pytest

from custom_components.casio_abl100.transport import (
    ALL_FEATURES, CONVOY, DRSP, FEATURES_SERVICE, START, read_record,
)
from tests.protocol.test_daily_history import history_record

CCCD = '00002902-0000-1000-8000-00805f9b34fb'
HANDLES = {ALL_FEATURES: (61, 62), DRSP: (71, 72), CONVOY: (81, 82)}


def adapter_type():
    name = 'custom_components.casio_abl100.native_step_adapter'
    assert util.find_spec(name) is not None, 'native step adapter API is absent'
    return import_module(name).NativeStepAdapter


def metadata():
    return SimpleNamespace(services=[SimpleNamespace(
        uuid=FEATURES_SERVICE, handle=42,
        characteristics=[SimpleNamespace(
            uuid=uuid, handle=handle, properties=0x18 if uuid == DRSP else 0x10,
            descriptors=[SimpleNamespace(uuid=CCCD, handle=desc)],
        ) for uuid, (handle, desc) in HANDLES.items()],
    )])


class API:
    def __init__(self):
        self.calls = []
        self.callbacks = {}
        self.hold_at = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.failure = None
        self.when = None

    async def boundary(self, stage):
        if self.hold_at == stage:
            self.entered.set()
            await self.release.wait()
        if self.when == stage:
            raise self.failure

    async def bluetooth_gatt_start_notify(self, address, handle, callback, timeout):
        self.calls.append(('notify', handle))
        self.callbacks[handle] = callback
        await self.boundary(f'notify{handle}')
        return (lambda: None), lambda: self.callbacks.pop(handle, None)

    async def bluetooth_gatt_write_descriptor(self, address, handle, data, timeout):
        self.calls.append(('cccd', handle, bytes(data)))
        await self.boundary(f'cccd{handle}')

    def deliver(self, mode='valid'):
        self.callbacks[71](71, bytearray(b'\x00\x11\x90\x01\x00'))
        raw = history_record()
        self.callbacks[81](81, bytearray(raw[:200]))
        self.callbacks[81](81, bytearray(raw[200:]))
        if mode == 'extra':
            self.callbacks[81](81, bytearray(b'\x00'))

    async def bluetooth_gatt_write(self, address, handle, data, response, timeout):
        self.calls.append(('write', handle, bytes(data), response))
        self.deliver()
        await self.boundary('write')

    async def bluetooth_device_disconnect(self, address, timeout):
        self.calls.append(('disconnect', address))
        await self.boundary('disconnect')
        # Installed connect callback removes address notifications on a
        # confirmed disconnect; real NativeAPI backstop is covered in HA.
        self.callbacks.clear()


@pytest.mark.asyncio
async def test_complete_real_transport_uses_only_three_notify_cccd_and_start():
    api = API()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    result = await read_record(adapter)
    assert result.steps == 1485 and len(result.daily_history) == 7
    assert [x.steps for x in result.daily_history] == [1234, 0, None, 7654, 42, 99999999, 1]
    assert api.calls == [
        ('notify', 61), ('cccd', 62, b'\x01\x00'),
        ('notify', 71), ('cccd', 72, b'\x01\x00'),
        ('notify', 81), ('cccd', 82, b'\x01\x00'),
        ('write', 71, START, True), ('disconnect', 1),
    ]
    assert not api.callbacks and adapter.disconnected


@pytest.mark.parametrize('role', range(3))
@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'cccd', 'duplicate_cccd', 'notify', 'handle', 'collision', 'wrong_service'])
def test_metadata_rejected_before_io(role, mode):
    api = API()
    raw = metadata()
    service = raw.services[0]
    char = service.characteristics[role]
    if mode == 'missing':
        service.characteristics.remove(char)
    elif mode == 'duplicate':
        service.characteristics.append(char)
    elif mode == 'cccd':
        char.descriptors = []
    elif mode == 'duplicate_cccd':
        char.descriptors.append(char.descriptors[0])
    elif mode == 'notify':
        char.properties &= ~0x10
    elif mode == 'handle':
        char.handle = True
    elif mode == 'collision':
        char.descriptors[0].handle = 61
    else:
        service.characteristics.remove(char)
        raw.services.append(SimpleNamespace(uuid='00001800-0000-1000-8000-00805f9b34fb', handle=2, characteristics=[char]))
    with pytest.raises(ValueError):
        adapter_type()(api, 1, raw, lambda: True)
    assert not api.calls


@pytest.mark.parametrize('mode', ['no_service', 'duplicate_service', 'no_write', 'zero_service', 'service_collision'])
def test_service_and_drsp_write_properties_are_validated(mode):
    api = API()
    raw = metadata()
    if mode == 'no_service':
        raw.services = []
    elif mode == 'duplicate_service':
        raw.services.append(raw.services[0])
    elif mode == 'no_write':
        raw.services[0].characteristics[1].properties = 0x14
    elif mode == 'zero_service':
        raw.services[0].handle = 0
    else:
        raw.services[0].handle = 61
    with pytest.raises(ValueError):
        adapter_type()(api, 1, raw, lambda: True)
    assert not api.calls


@pytest.mark.asyncio
async def test_arbitrary_writes_wrong_response_and_unsubscribed_start_never_submit():
    api = API()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    for char, data, response in [(DRSP, START, True), (DRSP, START, False), (CONVOY, START, True), (DRSP, b'\x15', True), (DRSP, b'\x01\x11\x00\x00\x00', True)]:
        with pytest.raises(ValueError):
            await adapter.write_gatt_char(char, data, response)
    assert not api.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('handle', [61, 71, 81])
@pytest.mark.parametrize('outcome', ['success', 'error', 'backend_cancel'])
async def test_each_finite_notify_acquisition_is_joined_under_repeated_cancel(handle, outcome):
    api = API()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    api.hold_at = f'notify{handle}'
    if outcome != 'success':
        api.when = api.hold_at
        api.failure = RuntimeError('PRIVATE_NOTIFY_CANARY') if outcome == 'error' else asyncio.CancelledError()
    task = asyncio.create_task(read_record(adapter))
    await asyncio.wait_for(api.entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert not task.done()
    api.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not api.callbacks and adapter.disconnected
    assert not any(x[0] == 'write' for x in api.calls)
