"""Portable exact inverse-delta preservation guard for the accepted Unit A base.

No Git, private packet or installed HA is needed. Each frozen authorized hunk is
verified before inversion; the remaining whole-module AST must equal the accepted
base. Unowned component bytes, including NativeRead/protocol/parsers, are pinned.
New authorized revisions must update this finite oracle with independent review.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'custom_components' / 'casio_abl100'
IMMUTABLE = {'alarms.py': '0cce75a3ec0e31c71980a0578d77d41c1a7ab9852f9f61b7d900e82b9eb2cdc4',
 'binary_sensor.py': '5af1aaee0c38429916a9453a6e71351511aff1b2b601792934e463fde431b518',
 'button.py': '02e75b972195cb504f3220d997c3c58dd76771613ab978341a3ec93b6c300d60',
 'diagnostics.py': '35373addef9e6abb305a95db8155c0a884e360c1fbd0a7b53239ec6deb476abc',
 'manifest.json': '995c7bca7aa8699184ebfd6a1d7cc6b2da69493128c20a195715287b5beb80fc',
 'native_alarm_adapter.py': 'addc41bf585bd2c7feab52748c7b99a9cadb18df503320c03254f0474cf33054',
 'native_alarms.py': 'd1663a47bd7fd7619d512065938e8b598d0babf0e4a2410d919742450197468d',
 'native_api.py': '3af0e436ec5bb28177ccff6eb102dee1249b3ef20241f1eae28eddc1a8402908',
 'native_step_adapter.py': '977086f937906e9e6aabf6974ed9db9b5ddba9e24910456ecb489a44f74df442',
 'native_steps.py': '33e29565daddd8e6accd374c441c5f7e8dd1feda50a0899a1c685b60700a0e9a',
 'native_sync_adapter.py': '74e55d1dfece6ce68e5eec2c0cdc819f7171f73e08cb474da9a6804029d90e78',
 'native_zone_adapter.py': '87bca86ecb72ed57d5bd52a64872924240034c5f38a9598dcaca1f72df6a91a8',
 'native_zones.py': '743e50708eccb89c611faedcc2f85e8cfb958ab4311732e7ed50c7487e14cc55',
 'protocol.py': '400d50114c0371f8bc0daff77bee85f6eb2b8af32715e34d142fc11de8fe742b',
 'sensor.py': '42a1dc67f79ca9d06291cf1a68b9568c486a45a1bc796123e30e12d7fdb1c0ca',
 'settings.py': '2e223383cba446e4f6bd0d1f76f646b970bcee552f94cb0d8dacc9d1310b814a',
 'sync_transport.py': '3c4ee02eb68e5842627c1979b02434c18cd1f53a92d5090ebac12ecbb2351716',
 'transport.py': '140b8029c6bae04fa43896e06c86aaeabfdbbb9a6fcb5f523b0246595db4ee43',
 'zone_transport.py': '24fa0e2da8ab9ecf54b8f328ccd6e850c1f02ae6fb519db0265f947158fd5af6'}
INVERSE = {'__init__.py': {'baseline_ast': '73b1f5469eb67b68f775fdcff7f29ba1d11a12cf6fc6c1a196c9f5fb99449998',
                 'hunks': [[402,
                            412,
                            '068a8f07a2ae1d4da1b49f51d06fe15bb35580567ee4c4df7546a2d5f7455fa5',
                            "                self.set_zone_phase('closed' if self.closed else 'idle', "
                            'notify=False)\n'
                            '        self._schedule_alarm_delivery()\n'
                            '\n'
                            '    async def read_all(self):\n'
                            '        """Read supported groups on one HA-owned native connection."""\n'
                            '        from .native_sync import read_all\n'
                            '        await read_all(self)\n'
                            '\n'
                            '    async def shutdown(self):\n'
                            '        self.closed = True\n'],
                           [441,
                            457,
                            'bae8c2f3395d84264e8b28723da124d26d7515be53ea95e01a94b564f732a6a5',
                            '    entry.runtime_data = manager\n'
                            '    cancel_update = None\n'
                            '    try:\n'
                            '        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)\n'
                            "        if entry.options.get('auto_read_steps') is True:\n"
                            '            from .auto_read import AutoStepReader\n'
                            '\n'
                            '            manager.auto_reader = AutoStepReader(hass, entry, manager)\n'
                            '            manager.auto_reader.start()\n'
                            '        cancel_update = entry.add_update_listener(_async_options_updated)\n'
                            '        entry.async_on_unload(cancel_update)\n']]},
 'auto_read.py': {'baseline_ast': '529fd627c9da2c5faa4d512bf4aaa701521f39d36f6aa43eb077ac6a0dbe17ef',
                  'hunks': [[3,
                             15,
                             '3dea5b4bc2812c76b58d6e267e5ffbab6d8aba810fa06d2bced711be9d8715be',
                             'import logging\n'
                             'import math\n'
                             '\n'
                             'from bluetooth_data_tools import monotonic_time_coarse\n'
                             'from homeassistant.components import bluetooth\n'
                             'from homeassistant.core import callback\n'
                             'from homeassistant.helpers.event import async_track_time_interval\n'],
                            [30,
                             39,
                             '954835fa5a75fcfe945c1d9a0a41d87bab9e8ec9e6e555e42ce08005c0d89c14',
                             'class AutoStepReader:\n'
                             '    """One request per observed burst, not proof of a particular watch '
                             'gesture."""\n'
                             '\n'
                             '    def __init__(self, hass, entry, session):\n'
                             '        self.hass, self.entry, self.session = hass, entry, session\n'
                             '        self._clock = None\n'
                             '        self._last_seen = None\n'
                             '        self._claimed = False\n'],
                            [45,
                             56,
                             'cef3347fbf851d59ae98d4e1dd6cf9fc161ad2cdfcdf3873afbe6aad08e43769',
                             '        self._stop_cancelled_task = None\n'
                             '        self._cancel_callback = None\n'
                             '        self._cancel_timer = None\n'
                             '\n'
                             '    def _now(self):\n'
                             '        now = monotonic_time_coarse()\n'],
                            [72,
                             93,
                             '7ba5119d3f5a39f77d0b0fef0f686e2f0ccb2e5967b38907c18f0c48624f06c0',
                             '        if now is None or self._faulted:\n'
                             '            return\n'
                             '        try:\n'
                             '            self._cancel_callback = bluetooth.async_register_callback(\n'
                             '                self.hass, self._advertisement,\n'
                             "                {'address': self.session.address, 'connectable': True},\n"],
                            [105,
                             206,
                             '4a8305cbf10cfdd9758eb9006dbc6d64a6135e506993dfd8240d3c2b51ba27bb',
                             '                    cancel()\n'
                             '                except (Exception, asyncio.CancelledError):\n'
                             "                    _LOGGER.warning('Automatic step observer cleanup failed')\n"
                             '            raise\n'
                             '\n'
                             '    @callback\n'
                             '    def _advertisement(self, info, change):\n'],
                            [225,
                             238,
                             '503d01586360f536cd27281d644abbce64e468ee659b3ed1a9a20590094e3ac9',
                             '        if now is None or not _finite_time(seen) or seen > now:\n'
                             '            self._faulted = True\n'
                             '            return\n'
                             '        previous = self._last_seen\n'
                             '        if previous is not None:\n'
                             '            if seen < previous:\n'],
                            [249,
                             288,
                             'e742801716b5d33363eb081ac093dc4367276bb3cdc14d197c5e4f48c0c5be57',
                             '        if (not self._ready or not allow_request or self._suspended or now - '
                             'seen > MAX_AGE\n'
                             '                or self._busy() or self._task is not None):\n'
                             '            return\n'
                             '        coroutine = self._read(seen)\n'
                             '        try:\n'
                             '            self._task = self.entry.async_create_background_task(\n'
                             "                self.hass, coroutine, 'Casio automatic step read', "
                             'eager_start=False,\n'
                             '            )\n'
                             '        except (Exception, asyncio.CancelledError):\n'
                             '            coroutine.close()\n'
                             "            _LOGGER.warning('Unable to schedule automatic step read')\n"
                             '\n'
                             '    def _busy(self):\n'
                             '        return self.session.closed or self.session.lock.locked() or '
                             'bool(self.session.tasks)\n'
                             '\n'
                             '    async def _read(self, seen):\n'
                             '        try:\n'
                             '            now = self._now()\n'
                             '            if (now is None or self._faulted or self._stopped or '
                             'self._suspended\n'
                             '                    or self._busy() or now - seen > MAX_AGE):\n'
                             '                return\n'
                             '            # No yield between the final check and entering the existing '
                             'session.\n'
                             '            await self.session.read()\n'
                             '        except asyncio.CancelledError:\n'
                             '            task = asyncio.current_task()\n'
                             '            if task is not None and task.cancelling():\n'],
                            [290,
                             298,
                             '7f592c99f05d3093ffdd2b3ca956fb300347cc9a2023a1096f908f94186a438d',
                             '        except Exception:\n'
                             "            _LOGGER.warning('Automatic step read failed')\n"
                             '        finally:\n'
                             '            if self._task is asyncio.current_task():\n'
                             '                self._task = None\n'
                             '\n'],
                            [314,
                             323,
                             'c98d4668f2503fbffccede6714aba76b47576f9757ecac8c15ca14a07fc4e1cb',
                             '        does not impose a timeout or forcibly abandon the owned reader.\n'
                             '        """\n'
                             '        self._stopped = True\n'
                             "        for attribute in ('_cancel_callback', '_cancel_timer'):\n"
                             '            cancel = getattr(self, attribute)\n'
                             '            setattr(self, attribute, None)\n'
                             '            if cancel is not None:\n']]},
 'config_flow.py': {'baseline_ast': '00a3c3ef513305524057b9c169834a5fae9fa66a537d2aefc31618490920f172',
                    'hunks': [[48,
                               55,
                               '3afb1e5bf5552477de5f1ac6884a73ea6adddc6a5561e9df25483dcf9b857159',
                               '        entry = self._get_reconfigure_entry()\n'
                               '        if not entry.unique_id:\n'
                               "            return self.async_abort(reason='missing_identity')\n"
                               "        if entry.options.get('auto_read_steps') is True:\n"
                               "            return self.async_abort(reason='auto_read_enabled')\n"
                               '        if user_input is not None:\n'
                               '            address, errors = self._selected_address(user_input)\n'],
                              [115,
                               129,
                               '90e4907cb8677082570cf052b427dce4d704bbb579cfe1899d0505a9728dfbbd',
                               '    async def async_step_init(self, user_input=None):\n'
                               '        errors = {}\n'
                               '        if user_input is not None:\n'
                               "            if type(user_input.get('auto_read_steps')) is not bool:\n"
                               "                errors['auto_read_steps'] = 'invalid_boolean'\n"
                               '            else:\n'
                               "                return self.async_create_entry(title='', data={\n"
                               '                    **self.config_entry.options,\n'
                               "                    'auto_read_steps': user_input['auto_read_steps'],\n"
                               '                })\n'
                               '        return self.async_show_form(\n'
                               "            step_id='init',\n"],
                              [130,
                               139,
                               '4853b9f82a9b6f88150eef4eed8ef7067cbf398340a4e90721e65e229209883b',
                               "                vol.Required('auto_read_steps', "
                               'default=self.config_entry.options.get(\n'
                               "                    'auto_read_steps', False,\n"
                               '                )): bool,\n'
                               '            }),\n'
                               '            errors=errors,\n'
                               '        )\n']]},
 'native_sync.py': {'baseline_ast': '85da4a0ed444e7b86eb04395f44ab58ff3c90fc6fbafcad5a58539aea9b2b895',
                    'hunks': [[3,
                               13,
                               'f2acef955a1888193a0e922a3e0aec8cb8aae7089675d86b7c076dcdda4085cf',
                               'from homeassistant.util import dt as dt_util\n'
                               'from homeassistant.components import bluetooth\n'
                               '\n'
                               'from .native_alarms import NativeRead, select_proxy\n'
                               'from .native_api import NativeAPI\n'
                               'from .native_sync_adapter import NativeSyncAdapter\n'
                               'from .sync_transport import (read_alarm_group, read_step_group, '
                               'read_zone_group,\n'],
                              [54,
                               103,
                               '41c7f02744f7ffec05ea415ed101620ad6ff19313dbcd0b4b3837923e0486124',
                               '        return result\n'
                               '\n'
                               '\n'
                               'class NativeSyncRead(NativeRead):\n'
                               '    def __init__(self, session, entry):\n'
                               '        super().__init__(session, entry)\n'],
                              [106,
                               118,
                               '04311d88d0d46f720f1e778444c93d246c0cfa1864fa65b4b5e3537251c8bc96',
                               '                            self.observe_link)\n'
                               '        self.api.session = session\n'
                               '        self.execution = None\n'
                               '\n'
                               '    def observe_link(self, connected, error):\n'
                               '        confirmed = connected is True and error == 0\n'],
                              [183,
                               190,
                               '82c509d40bf4438fb8e02c905a22c881a2d5a9c90a04e9734a93dfee5d9d9bf6',
                               '            self.adapter.remove_notifications()\n'
                               '\n'
                               '\n'
                               'async def read_all(session):\n'
                               '    """Register before lock wait; a queue waiter owns no status or '
                               'freshness."""\n'
                               '    task = asyncio.current_task()\n'
                               '    if session.closed:\n'],
                              [198,
                               211,
                               '33731a10c45123023350dd6d187da6f3cdc23b8f196af891624a4df32cf34180',
                               '        async with session.lock:\n'
                               '            if session.closed or task.cancelling():\n'
                               '                raise asyncio.CancelledError\n'
                               '            active = True\n'
                               '            session.sync_group_results = dict.fromkeys(GROUPS, '
                               "'not_attempted')\n"
                               "            session.sync_cleanup_result = 'not_attempted'\n"],
                              [219,
                               226,
                               'cffa1e7507ea97f6e8fb73536905b3c873c15da4e15318ea5d22d146dfec10be',
                               '            if entry is None:\n'
                               "                raise ValueError('Native unified source required')\n"
                               '            reader = NativeSyncRead(session, entry)\n'
                               '            await reader.read()\n'
                               '            if session.closed or task.cancelling():\n'
                               '                raise asyncio.CancelledError\n']]}}
JSON_BASE = {'strings.json': '612c38f0cb7928138d97742cee58b2e0b31a63372621da3f9b0cc28a0291cef9',
 'translations/en.json': '612c38f0cb7928138d97742cee58b2e0b31a63372621da3f9b0cc28a0291cef9',
 'translations/es.json': 'f9d94e65dfcfbc0b89654349ed49cb7d97c74a839a709f6322829d00025a0dfa'}


INTERACTIVE_INVERSE = {'__init__.py': {'installed_sha256': '2f4003d80d27a67231c50c9d76d077477c558ab8c88bb0fb56d9b3f03958d163', 'hunks': [[74, 75, '426335538439bcec5d0147c2f196fd0f08ddcfc9096e42b339154ff2ed3c25a3', ''], [411, 430, 'a71237999e15c1b9bbde6bf883b2f6661f69539d4a76429afcdf33bc31aa8943', ''], [445, 448, '1d60f5169045bcb3170f494f8ecfa4d9860cdf0879118553e8e8e21aedf5a6e6', '                if self.auto_reader is not None:\n                    await self.auto_reader.stop()\n'], [449, 460, 'e673c22b1a68267d75c1045c6d0ed440e00b3737826657e1f49613f09a966ee5', '                # The reader has already joined its owned read, including any\n                # cooperative disconnect. Do not cancel that cleanup twice.\n                tasks = set(self.tasks)\n                for task in tasks:\n                    task.cancel()\n                if tasks:\n                    await asyncio.gather(*tasks, return_exceptions=True)\n'], [505, 511, '00332a526e28e2a85cafbbfa82e6a602237bfae5fa45007a58df118fa31405c0', '        unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)\n']]}, 'native_sync_adapter.py': {'installed_sha256': '74e55d1dfece6ce68e5eec2c0cdc819f7171f73e08cb474da9a6804029d90e78', 'hunks': [[21, 49, '008a94c234ce0a93b47b56c5c527a3ade0d968821faaf24d6d531d300368405a', ''], [56, 62, '1440df158bf49bd75291126571adc57205bcf39f3db2c5b5c3a050f81321fc9f', ''], [65, 72, '593c9c8ba5ab215374e36d57e2a9493f3f27b2963736157a9a93043e79ccdd10', '        await super().start_notify(self.primary, receive)\n        self.guard()\n        self.primary_ready = True\n'], [86, 100, '4f3f05ec00645c0397295e548cc1f7025d03395e185973acf7c4f9bab99148e6', '']]}}


UI_INVERSE = {'button.py': {'accepted_core_sha256': '02e75b972195cb504f3220d997c3c58dd76771613ab978341a3ec93b6c300d60',
               'hunks': [[1, 3, '9bafe2d64788667dcd2d5332499afb885b0bcdd42469179df7e418ec76851527', ''],
                         [4, 6, '48c143ab605726f860b48a2ea081fe558f9fda6d83e87bf468c12d2ce00345e4', ''],
                         [8, 41, 'dc786eb8c9be769e84589e6a5066b13d9dce0c04155c6b7cae87ca6755a0ae54', ''],
                         [53, 57, '0b88c76f65032a53c29b5fd1e8d361f0dd2e4df726baf59273c9a3293093724a', ''],
                         [67, 68, 'e60fc4d1794b38fef46c4c08f8f050aeb06168a97369fd6983adf17196f9094a', ''],
                         [87, 91, '0b88c76f65032a53c29b5fd1e8d361f0dd2e4df726baf59273c9a3293093724a', ''],
                         [101, 102, 'e60fc4d1794b38fef46c4c08f8f050aeb06168a97369fd6983adf17196f9094a', ''],
                         [122, 123, 'e60fc4d1794b38fef46c4c08f8f050aeb06168a97369fd6983adf17196f9094a', ''],
                         [146, 147, 'e60fc4d1794b38fef46c4c08f8f050aeb06168a97369fd6983adf17196f9094a', ''],
                         [148, 214, 'c6d5f6913a6638535e3633d0808b0a1ea4c14e75b99570cce3458e30025b0715', ''],
                         [218,
                          221,
                          '99826dc867d11d756f62567b6dfb81c86b3ae1d8e07b8e89c983f5c654c4b791',
                          '                        ReadTimeZonesButton(entry.runtime_data), '
                          'ReadAllButton(entry.runtime_data)])\n']]},
 'binary_sensor.py': {'accepted_core_sha256': '5af1aaee0c38429916a9453a6e71351511aff1b2b601792934e463fde431b518',
                      'hunks': [[41,
                                 83,
                                 '720c968d900758f2b92c27bc8a1a9fe12a40bde57048dce8006b61c87dd0f9b3',
                                 ''],
                                [84,
                                 86,
                                 '98ebe2cf6dff5c3496d083bd4cf14453011b333511d2c6c9e7cc7989bc532bcf',
                                 '    async_add_entities([HourlySignalSensor(entry.runtime_data)])\n']]}}

UI_JSON_ADDITIONS = {'strings.json': {'buttons': {'connect_interactive': {'name': 'Connect watch'},
                              'read_interactive': {'name': 'Refresh connected watch'},
                              'close_interactive': {'name': 'Close watch connection'}},
                  'binary_sensors': {'interactive_connected': {'name': 'Interactive connection'}},
                  'exceptions': {'interactive_connect_failed': {'message': 'Unable to connect the watch'},
                                 'interactive_read_failed': {'message': 'Unable to refresh the connected '
                                                                        'watch; completed groups are '
                                                                        'retained'},
                                 'interactive_close_failed': {'message': 'Unable to confirm closure of the '
                                                                         'watch connection'},
                                 'interactive_busy': {'message': 'The watch is busy; no action was queued'},
                                 'interactive_not_connected': {'message': 'No usable interactive watch '
                                                                          'connection'}}},
 'translations/en.json': {'buttons': {'connect_interactive': {'name': 'Connect watch'},
                                      'read_interactive': {'name': 'Refresh connected watch'},
                                      'close_interactive': {'name': 'Close watch connection'}},
                          'binary_sensors': {'interactive_connected': {'name': 'Interactive connection'}},
                          'exceptions': {'interactive_connect_failed': {'message': 'Unable to connect the '
                                                                                   'watch'},
                                         'interactive_read_failed': {'message': 'Unable to refresh the '
                                                                                'connected watch; completed '
                                                                                'groups are retained'},
                                         'interactive_close_failed': {'message': 'Unable to confirm closure '
                                                                                 'of the watch connection'},
                                         'interactive_busy': {'message': 'The watch is busy; no action was '
                                                                         'queued'},
                                         'interactive_not_connected': {'message': 'No usable interactive '
                                                                                  'watch connection'}}},
 'translations/es.json': {'buttons': {'connect_interactive': {'name': 'Conectar reloj'},
                                      'read_interactive': {'name': 'Actualizar reloj conectado'},
                                      'close_interactive': {'name': 'Cerrar conexión del reloj'}},
                          'binary_sensors': {'interactive_connected': {'name': 'Conexión interactiva'}},
                          'exceptions': {'interactive_connect_failed': {'message': 'No se pudo conectar el '
                                                                                   'reloj'},
                                         'interactive_read_failed': {'message': 'No se pudo actualizar el '
                                                                                'reloj conectado; se '
                                                                                'conservan los grupos '
                                                                                'completados'},
                                         'interactive_close_failed': {'message': 'No se pudo confirmar el '
                                                                                 'cierre de la conexión del '
                                                                                 'reloj'},
                                         'interactive_busy': {'message': 'El reloj está ocupado; no se ha '
                                                                         'puesto ninguna acción en cola'},
                                         'interactive_not_connected': {'message': 'No hay ninguna conexión '
                                                                                  'interactiva utilizable'}}}}


FIRST_FAULT_INVERSE = {'native_interactive.py': {'baseline_sha256': 'b2b7e97faaed56c3a17963406bd10ab2c158b1c82a0c3ab43ec207a8101f626e',
                           'hunks': [[31,
                                      39,
                                      'e0912bc2ace8faa1207d7ed75869d744efc3ad638cffadb93823a4ec09949941',
                                      ''],
                                     [81,
                                      83,
                                      '78511730242efb09f870706e1242fc1d040d5373bf572eea75b4785b96474db3',
                                      ''],
                                     [172,
                                      173,
                                      '68df2f33d30dfa36152028038e6ac6c3258ef86f0d149108b137dc97e92f77cb',
                                      ''],
                                     [286,
                                      287,
                                      '68df2f33d30dfa36152028038e6ac6c3258ef86f0d149108b137dc97e92f77cb',
                                      ''],
                                     [318,
                                      350,
                                      '67ff5fd5ffb2de3fd159a02025b5fb47e074f5e5b91b1c234d1c7a304e40da9f',
                                      ''],
                                     [393,
                                      396,
                                      'e899271b8ab78587a6a20244224f5f6fad13cebf77b952739aedf08c3c773875',
                                      ''],
                                     [468,
                                      469,
                                      'b99e2f6bfc0465c2733215db57ca9b3dc8baa09c0c448527bc61bb986c4bb9e1',
                                      '']]},
 'binary_sensor.py': {'baseline_sha256': '732740ca52beb152669040f5ba441974792fd537479d8f275f0b8a5fdf42903c',
                      'hunks': [[72,
                                 74,
                                 '9ff9c0f6045486ae95da3e763dd568b018362dd121c47ddc32eeb533adfeeb58',
                                 "                        cleanup_result='not_attempted', "
                                 'close_reason=None)\n'],
                                [76,
                                 80,
                                 '479603176c2835c22076f52113938a54a7ccef2f503682d475a0d5a73c60a780',
                                 '']]}}


OBSERVATIONS_INVERSE = {'__init__.py': {'baseline_sha256': 'f5c6ef8271204b30720f9540f44fbd552e43ed5744def4bc5460b458b80d6053',
                 'hunks': [[8,
                            10,
                            'ed5b199f5fd918e1d082706de8b4e41a866365aa0acf814d705a082ba90d8cd6',
                            'from homeassistant.const import Platform\n'],
                           [93,
                            104,
                            '0fef19fc031b6e430565888f39190e7fb18a7228f35b6b0829d1b172ebdcfc03',
                            ''],
                           [218,
                            219,
                            '7d381eedb1a970b9d222b1265293955ae17813f062aa233bb1560275a74cc8c4',
                            ''],
                           [333,
                            334,
                            'c220d7ea2cd1793b9e53b08caccac0bf21b157692d42d5a74d8fd2f23d235f44',
                            ''],
                           [402,
                            403,
                            'f662d220f92149af93bb7b4c4d2c5786dadbfed29f01fe667555c5f01a8759d2',
                            ''],
                           [477,
                            479,
                            'ef4ea4bcebbb2ee266c23b407dec9d11ddce5bb5453cc69fe6a15cd6bd41b42d',
                            ''],
                           [490,
                            497,
                            'ab5d4eb48d40b953acf7094732485213909235ad1656661e5a8e6de5a14fc996',
                            ''],
                           [517,
                            522,
                            '645912c49535e7f6d3058dda3c6039055a9657f8b884e673bff0003b1f423205',
                            '']]},
 'sensor.py': {'baseline_sha256': '42a1dc67f79ca9d06291cf1a68b9568c486a45a1bc796123e30e12d7fdb1c0ca',
               'hunks': [[169,
                          170,
                          '1346da673404703bcbc30d1f2b6172c89374615728b1721febe6e0614d22e504',
                          '    """Independent last-good configuration, intentionally not '
                          'persisted."""\n'],
                         [217,
                          221,
                          'eeb8bf5d3688caa93ecb1f5fca74f9c8dc6fba46b5dc90edd4c6c687ee20fa27',
                          '']]},
 'native_sync.py': {'baseline_sha256': 'e25e6203b4780abb3552a4829e08ec4374b53c2974ad5b424c661502f09d0847',
                    'hunks': [[179,
                               180,
                               'd2fbb9713658ccc0a299c1bb8a11f6143f0c20807e48a89ce3831a6949911135',
                               '']]},
 'native_interactive.py': {'baseline_sha256': '221a7b31fc8368fcae9dfbd5bddff38028d7913b712004bf1892106cb15d141c',
                           'hunks': [[438,
                                      439,
                                      '76b4ad4c721c82507151db869969c5af6d8a1687b0d9bdafe3a0d8ecaab1d927',
                                      '']]}}
OBSERVATIONS_SHA256 = '3281ac06f65f6e7e0c9b5672de2f5cf4c0619c71b6d29701fc6fa0650aa59126'


VALUE_EDITING_INVERSE = {'__init__.py': {'installed16227ef_sha256': 'c36c6a7e76ca654d2822d79d86dc5010cd08e4fe340dfdf872b38a3af0b83e30',
                 'hunks': [[16,
                            17,
                            '8e1e7071dbba1880c192e8b111cd519385c8031b9aa732bfe06aa56fc05e6aa6',
                            'PLATFORMS = (Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR)\n'],
                           [440,
                            496,
                            'b4f09fd7739c9aa34a7fbbda61c08f8c3d7607724676309933b73974721f61eb',
                            '']]},
 'button.py': {'installed16227ef_sha256': '94b572eb8b4d0e9ad85ae10dad9198dd305bcac10e61a4f97e852dee07406174',
               'hunks': [[202,
                          203,
                          'da6c9fb24058d892093f6eb60351cce74c7f542ef19aafbd048aa8cc31c3444e',
                          "                if controller.phase == 'reading':\n"],
                         [217, 250, 'b692daa3623bfda01754b7e5ee20ccc34c8f7ffba29c4e8833174ca06b30b2d0', ''],
                         [253,
                          255,
                          'b3716e8ab65b2f2780a08f6f9e24f79a8460b2d6850e85995d21b87a9a3a51eb',
                          "                          for action in ('connect', 'read', 'close'))])\n"]]},
 'native_interactive.py': {'installed16227ef_sha256': '80d77245700955ca6c8aec8844ccf2c6a0e835ab2459eeca2cfe450a68cc0296',
                           'hunks': [[31,
                                      38,
                                      '4b3c9f5ad8e5e5791aba4a1391454654208ec8215ef6c5d02060a7df1ea68262',
                                      ''],
                                     [83,
                                      84,
                                      'd8c7edeea682aabaec216b186c1315c5f3c61ac46751adadff4ae4577c7729a2',
                                      ''],
                                     [216,
                                      218,
                                      'aa09a550ce52e0397d272fb0c1647ab0bb4adf75753d4f3e568104e104d7aa5d',
                                      "                    reason = 'read_failed' if self.operation_caller "
                                      "is not None else 'connect_failed'\n"],
                                     [236,
                                      246,
                                      '759f677eafde43f219111d3d6366fa4f91012eff381c620c28abe75da177c712',
                                      ''],
                                     [320,
                                      342,
                                      '8ee377a726787863c56239c2a374336191d2695ae1e7450c8351cdd8fb799498',
                                      ''],
                                     [384,
                                      401,
                                      '26bd33ecc6c247e9c4b040cf50eaf5e6a80cb544c61fa5400ffb5dba2e453496',
                                      ''],
                                     [404,
                                      406,
                                      'e44d6553c958704c515171a658ae3c71852d3ab805eeedcf0b590e9f3bc6f14f',
                                      ''],
                                     [500,
                                      502,
                                      '349dc92d9174841978c5eaeda47ad0ffb64ec67cc715a997dba1c455718cdf84',
                                      "            reason = 'read_failed' if c.operation_caller is not None "
                                      "else 'connect_failed'\n"],
                                     [524,
                                      537,
                                      '0ae7a12b0a880fbe7663b551c6905d2f2e0ffe94305ec1c03c23c3633fdcb5be',
                                      ''],
                                     [556,
                                      558,
                                      '9a30b258ec6f8b88cf870b482038402b4a8498beba30796edc1faa4f00bab5ed',
                                      '']]},
 'native_sync_adapter.py': {'installed16227ef_sha256': '334b4e353f5585f9047dd06f825af07e308b773d8aeb3d0dfc6d4fde56b98350',
                            'hunks': [[60,
                                       65,
                                       'b77ea9f57a22db9ce753869332713578bef01b87258ba65a38c6de58fbc816ed',
                                       '']]},
 'strings.json': {'installed16227ef_sha256': 'bf0256e137f31f9c1e9e94be7c84c23f2b98db2bf704adaa700c5b6ff23d452a',
                  'hunks': [[32, 34, '9249351c62cbdfea00fb58705d0dc774fbeae0995c5d218db5460e8c374fde24', ''],
                            [94, 96, '741c1c022fc988519acb8ed7f8a16b064717f3ed5e51ef92a2b2d203e0894995', ''],
                            [114,
                             115,
                             'ad9cdd690dedabc5cc596a30c5fbbaadbc652417f1d8231072acdd800d1b8b0a',
                             '']]},
 'translations/en.json': {'installed16227ef_sha256': 'bf0256e137f31f9c1e9e94be7c84c23f2b98db2bf704adaa700c5b6ff23d452a',
                          'hunks': [[32,
                                     34,
                                     '9249351c62cbdfea00fb58705d0dc774fbeae0995c5d218db5460e8c374fde24',
                                     ''],
                                    [94,
                                     96,
                                     '741c1c022fc988519acb8ed7f8a16b064717f3ed5e51ef92a2b2d203e0894995',
                                     ''],
                                    [114,
                                     115,
                                     'ad9cdd690dedabc5cc596a30c5fbbaadbc652417f1d8231072acdd800d1b8b0a',
                                     '']]},
 'translations/es.json': {'installed16227ef_sha256': '7f422c60dbbb5b7abfe6ee856668749e96d932427761d4aeed4b08b2e4f62878',
                          'hunks': [[32,
                                     34,
                                     'c69ad294e8320b57fdb21a7d377c56fcbb85ddf46a7c5b5adaead23b31357441',
                                     ''],
                                    [94,
                                     96,
                                     'bf0b10eab37b7aa2131fed1597980e7032eaa6131d3a4ae77b8ac46dfce64a25',
                                     ''],
                                    [114,
                                     115,
                                     'cd274605f0586e38550fd2b1dfd162ad572a34ce6d4e441a49395df9339f1682',
                                     '']]}}
NEW_ALARM_MODULES = {'alarm_write.py': '2cff26befd829fd9a5b6340cb060cec0af4ce4ffb0c32ef5ec4711d689dc826f',
 'alarm_editing.py': '12dece7464445864840291712924f74e2590c05f7633b265573869c932c459bd',
 'native_alarm_edit.py': '86d2f7da45330740627dc594b20394e54354bac5b71f14fa1b600a11561915bf',
 'time.py': 'a14b92c7c86c38bd8a2875bec781a4fb759de2b3885d5054a5c0ead288868fe4',
 'switch.py': '6133f26f512e7ec9c08515c95efd9821a130c37a0aeb85bcfe10404d4b36e292'}


# R1 safety correction inverse: reconstruct rejected45e5307 exactly first.
R1_INVERSE = {'__init__.py': {'baseline_sha256': '90ab59501f49da8ae4464f709b87a541f940fe82b52ef938fe00556f03246442', 'hunks': [[94, 95, '11bd01adfe49b93904cf8b8b98b91bfa800dfb02ed5e41c10dceadc012a4bab3', ''], [446, 447, 'f27d5888d3870109b164bad1400fbd1d4cfb59781800c9e3131ee27efb15b0a8', '            self._alarm_draft = AlarmDraft()\n'], [534, 540, 'e751fc1a20cd55a1ce461f21ef6e6d6a823549e73e644058a4ad0c11da224a25', '            if self.observations is not None:\n                await self.observations.async_close()\n'], [546, 547, '94196040c95102720245c535e6a5dcba7f57eb06d9b9f9bbb2815ad5abc259c4', ''], [552, 555, 'f0594e455e144cdf5bb37b7bd2b5797c2571de6f14846e10c69094496b290abf', ''], [561, 567, '1dc9b3f462e482853aa179a11c23254aa718f7e3aca24c5299af23f242470069', '        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_observations))\n'], [592, 599, '1120006dcb63f0c093ba2d051a267a461383f06ece940c81537d6a0d194e77b2', '']]}, 'alarm_editing.py': {'baseline_sha256': '12dece7464445864840291712924f74e2590c05f7633b265573869c932c459bd', 'hunks': [[15, 17, '16039b9a106f00c31c1926218437f6a39f859e51867228ac524d28ff83b2e94d', '    def __init__(self):\n'], [25, 28, '552007b839416d821f736ba2230cb4979fee5fae1133e0050b88976d76f6e387', '        self.outcome = None\n        self.write_submitted = False\n'], [31, 41, '95d3b9256c2341cbfb6113efb5c39d8a45ef4c71a90da3573fa31a2defd25594', '']]}, 'native_alarm_edit.py': {'baseline_sha256': '86d2f7da45330740627dc594b20394e54354bac5b71f14fa1b600a11561915bf', 'hunks': [[82, 85, '409e42b0c8ef7d9ace2255c81c52baf9b5905f26bf85c5655c1fc2d7d4d88f46', ''], [94, 98, '15bae1df852b14dceadd6618e3abbf10b536c360540a1740564f8175e91a7fb6', '            return AlarmApplyOutcome(compare_alarm_write_readback(baseline,request.intent,observed),True,observed_at)\n']]}, 'native_interactive.py': {'baseline_sha256': 'ba1993f46c6d108862f4bfab1f2012b87588780a3dcd30f1be8fd087c89c01a6', 'hunks': [[326, 327, 'ec44d3ccf86e723180eaa1b3dc04e6ea4c6191211a0c49f26083ff6db0be15eb', ''], [531, 536, 'ce5a3fa42c3353cfbbc1f9a45383f798c8fb6bdebca535cab5ec0963ba7ce586', ''], [562, 566, 'a3fb2d7514d5ab567096b6001b917bcd711fd958c2e63492ef89da1824c13c1a', "                    if group == 'alarms' and hasattr(self.session, '_alarm_draft'):\n                        self.session.alarm_draft.needs_reconciliation = False\n"]]}}
R1_NEW = {'alarm_write_safety.py': '9067a8460b87db8b3cc969a6e6f638aeb786b2ef1b79d671ed674f063690106f'}


def r1_source(name):
    source = (ROOT / name).read_text()
    if name in R1_INVERSE:
        oracle = R1_INVERSE[name]
        lines = source.splitlines(keepends=True)
        for start,end,expected_hash,original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name,start)
            lines[start:end] = original.splitlines(keepends=True)
        source = ''.join(lines)
        assert digest(source.encode()) == oracle['baseline_sha256'], name
    return source


def test_r1_correction_inverse_restores_rejected45e5307():
    assert set(R1_INVERSE) == {'__init__.py','alarm_editing.py','native_alarm_edit.py','native_interactive.py'}
    assert set(R1_NEW) == {'alarm_write_safety.py'}
    for name in R1_INVERSE:
        r1_source(name)
    for name,expected in R1_NEW.items():
        assert digest((ROOT / name).read_bytes()) == expected


def value_editing_source(name):
    """Reconstruct exact installed16227ef before any historical inversion."""
    source = r1_source(name)
    if name in VALUE_EDITING_INVERSE:
        oracle = VALUE_EDITING_INVERSE[name]
        lines = source.splitlines(keepends=True)
        for start,end,expected_hash,original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name,start)
            lines[start:end] = original.splitlines(keepends=True)
        source = ''.join(lines)
        assert digest(source.encode()) == oracle['installed16227ef_sha256'], name
    return source


def test_value_editing_inverse_restores_installed16227ef_and_exact_new_paths():
    assert set(VALUE_EDITING_INVERSE) == {'__init__.py','button.py','native_interactive.py',
        'native_sync_adapter.py','strings.json','translations/en.json','translations/es.json'}
    assert set(NEW_ALARM_MODULES) == {'alarm_write.py','alarm_editing.py','native_alarm_edit.py','time.py','switch.py'}
    for name in VALUE_EDITING_INVERSE:
        value_editing_source(name)
    for name,expected_hash in NEW_ALARM_MODULES.items():
        assert digest(r1_source(name).encode()) == expected_hash, name


def installed_source(name):
    """Invert exact approved hunks, then verify the entire installed module."""
    source = value_editing_source(name)
    if name in FIRST_FAULT_INVERSE:
        oracle = FIRST_FAULT_INVERSE[name]
        lines = source.splitlines(keepends=True)
        for start, end, expected_hash, original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name, start)
            lines[start:end] = original.splitlines(keepends=True)
        source = ''.join(lines)
        assert digest(source.encode()) == oracle['baseline_sha256'], name
    if name in OBSERVATIONS_INVERSE:
        oracle = OBSERVATIONS_INVERSE[name]
        lines = source.splitlines(keepends=True)
        for start, end, expected_hash, original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name, start)
            lines[start:end] = original.splitlines(keepends=True)
        source = ''.join(lines)
        assert digest(source.encode()) == oracle['baseline_sha256'], name
    if name in UI_INVERSE:
        oracle = UI_INVERSE[name]
        lines = source.splitlines(keepends=True)
        for start, end, expected_hash, original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name, start)
            lines[start:end] = original.splitlines(keepends=True)
        source = ''.join(lines)
        assert digest(source.encode()) == oracle['accepted_core_sha256'], name
    if name not in INTERACTIVE_INVERSE:
        return source
    oracle = INTERACTIVE_INVERSE[name]
    lines = source.splitlines(keepends=True)
    for start, end, expected_hash, original in reversed(oracle['hunks']):
        assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name, start)
        lines[start:end] = original.splitlines(keepends=True)
    source = ''.join(lines)
    assert digest(source.encode()) == oracle['installed_sha256'], name
    return source


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_exact_component_inventory_and_unowned_bytes():
    expected = set(IMMUTABLE) | set(INVERSE) | set(JSON_BASE) | {'native_interactive.py', 'observations.py'} | set(NEW_ALARM_MODULES) | set(R1_NEW)
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    assert actual == expected and len(actual) == 34
    assert digest((ROOT / 'observations.py').read_bytes()) == OBSERVATIONS_SHA256
    installed_source('native_interactive.py')
    for name, expected_hash in IMMUTABLE.items():
        assert digest(installed_source(name).encode()) == expected_hash, name


def test_only_exact_authorized_python_seams_change():
    for name, oracle in INVERSE.items():
        lines = installed_source(name).splitlines(keepends=True)
        for start, end, expected_hash, original in reversed(oracle['hunks']):
            assert digest(''.join(lines[start:end]).encode()) == expected_hash, (name, start)
            lines[start:end] = original.splitlines(keepends=True)
        assert digest(ast.dump(ast.parse(''.join(lines)), include_attributes=False).encode()) == oracle['baseline_ast'], name


def test_existing_json_names_keys_and_translations_are_preserved():
    for name, expected_hash in JSON_BASE.items():
        obj = json.loads(value_editing_source(name))
        additions = UI_JSON_ADDITIONS[name]
        for key, expected in additions['buttons'].items():
            assert obj['entity']['button'].pop(key) == expected
        for key, expected in additions['binary_sensors'].items():
            assert obj['entity']['binary_sensor'].pop(key) == expected
        for key, expected in additions['exceptions'].items():
            assert obj['exceptions'].pop(key) == expected
        obj['config']['abort'].pop('auto_read_enabled')
        obj['options']['step']['init'].pop('description')
        assert obj['options']['step']['init']['data'].pop('auto_read_all')
        assert obj['options']['error'].pop('automatic_modes_conflict')
        assert digest(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()) == expected_hash, name
