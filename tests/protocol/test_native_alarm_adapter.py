"""Existing alarm transport through the native adapter, with synthetic API IO."""

import asyncio
from types import SimpleNamespace
import unittest

from custom_components.casio_abl100.native_alarm_adapter import NativeAlarmAdapter
from custom_components.casio_abl100.transport import (
    ALARM_QUERY,
    ALL_FEATURES,
    FEATURES_SERVICE,
    ReadError,
    read_alarms,
)


def metadata():
    descriptor = SimpleNamespace(uuid='00002902-0000-1000-8000-00805f9b34fb', handle=77)
    primary = SimpleNamespace(
        uuid=ALL_FEATURES, handle=61, properties=0x10, descriptors=[descriptor]
    )
    query = SimpleNamespace(
        uuid=ALARM_QUERY, handle=52, properties=0x04, descriptors=[]
    )
    return SimpleNamespace(
        services=[
            SimpleNamespace(
                uuid=FEATURES_SERVICE, handle=42, characteristics=[primary, query]
            )
        ]
    )


class API:
    def __init__(self):
        self.calls = []
        self.cb = None
        self.removed = False
        self.bad = False
        self.fail_cccd = False
        self.hold = asyncio.Event()
        self.cccd_entered = asyncio.Event()
        self.hold_cccd = False

    async def bluetooth_gatt_start_notify(self, address, handle, callback, timeout):
        self.calls.append(('notify', address, handle))
        self.cb = callback

        async def stop():
            self.calls.append(('stop',))

        def remove():
            self.removed = True
            self.cb = None

        return stop, remove

    async def bluetooth_gatt_write_descriptor(self, address, handle, data, timeout):
        self.calls.append(('cccd', address, handle, data))
        self.cccd_entered.set()
        if self.hold_cccd:
            await self.hold.wait()
        if self.fail_cccd:
            raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')

    async def bluetooth_gatt_write(self, address, handle, data, response, timeout):
        self.calls.append(('query', address, handle, data, response))
        if data == b'\x15':
            raw = b'\x15\xc0\x00\x07\x23' if not self.bad else b'\x15\x00'
        else:
            raw = b'\x16' + b'\x00\x07\x08\x09' * 4
        self.cb(61, bytearray(raw))  # Immediate response before write completes.

    async def bluetooth_device_disconnect(self, address, timeout):
        self.calls.append(('disconnect', address))


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_existing_transport_and_only_allowlisted_queries(self):
        api = API()
        adapter = NativeAlarmAdapter(api, 1, metadata(), lambda: True)
        result = await read_alarms(adapter)
        self.assertEqual(len(result.alarms), 5)
        self.assertTrue(result.hourly_signal)
        self.assertEqual((result.alarms[0].hour, result.alarms[0].minute), (7, 35))
        self.assertEqual(
            api.calls,
            [
                ('notify', 1, 61),
                ('cccd', 1, 77, b'\x01\x00'),
                ('query', 1, 52, b'\x15', False),
                ('query', 1, 52, b'\x16', False),
                ('disconnect', 1),
            ],
        )
        self.assertTrue(api.removed)
        self.assertTrue(adapter.disconnected)

    async def test_metadata_rejected_before_IO(self):
        for mode in [
            'no_service',
            'no_cccd',
            'no_notify',
            'no_query_property',
            'duplicate',
        ]:
            api = API()
            raw = metadata()
            if mode == 'no_service':
                raw.services = []
            elif mode == 'no_cccd':
                raw.services[0].characteristics[0].descriptors = []
            elif mode == 'no_notify':
                raw.services[0].characteristics[0].properties = 0
            elif mode == 'no_query_property':
                raw.services[0].characteristics[1].properties = 0
            else:
                raw.services.append(raw.services[0])
            with self.assertRaises(Exception):
                NativeAlarmAdapter(api, 1, raw, lambda: True)
            self.assertFalse(api.calls)

    async def test_malformed_first_reply_prevents_second_query(self):
        api = API()
        api.bad = True
        adapter = NativeAlarmAdapter(api, 1, metadata(), lambda: True)
        with self.assertRaises(ReadError):
            await read_alarms(adapter)
        self.assertEqual(
            [call[3] for call in api.calls if call[0] == 'query'], [b'\x15']
        )
        self.assertTrue(api.removed)
        self.assertTrue(adapter.disconnected)

    async def test_notify_failure_and_cancel_cleanup(self):
        for mode in ['error', 'cancel']:
            api = API()
            adapter = NativeAlarmAdapter(api, 1, metadata(), lambda: True)
            if mode == 'error':
                api.fail_cccd = True
            else:
                api.hold_cccd = True
            task = asyncio.create_task(read_alarms(adapter))
            await asyncio.wait_for(api.cccd_entered.wait(), 1)
            if mode == 'cancel':
                task.cancel()
            with self.assertRaises(BaseException):
                await task
            self.assertTrue(api.removed)
            self.assertTrue(adapter.disconnected)
            self.assertFalse(any(call[0] == 'query' for call in api.calls))

    async def test_lost_link_and_arbitrary_write_rejected(self):
        api = API()
        alive = [True]
        adapter = NativeAlarmAdapter(api, 1, metadata(), lambda: alive[0])
        query = adapter.services.get_service(FEATURES_SERVICE).characteristics[1]
        with self.assertRaises(Exception):
            await adapter.write_gatt_char(query, b'\x99', response=False)
        alive[0] = False
        with self.assertRaises(Exception):
            await adapter.write_gatt_char(query, b'\x15', response=False)
        self.assertFalse(api.calls)
