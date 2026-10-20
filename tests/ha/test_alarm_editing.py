"""Draft semantics are local, sparse and nonpersistent."""
from datetime import time, timezone
from types import SimpleNamespace
import importlib
import pytest

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.alarms import parse_alarms
FIRST = bytes.fromhex('15 40 07 06 11')
REST = bytes.fromhex('16 00 00 08 09 40 07 0a 0b 00 07 0c 0d 40 00 0e 0f')


def test_draft_contract_exists():
    import importlib.util
    assert importlib.util.find_spec('custom_components.casio_abl100.alarm_editing') is not None


@pytest.mark.parametrize('bad',[time(1,2,3),time(1,2,0,4),time(1,2,tzinfo=timezone.utc),'01:02'])
def test_minute_semantics_reject_without_mutation(bad):
    from custom_components.casio_abl100.alarm_editing import AlarmDraft
    draft = AlarmDraft()
    with pytest.raises(ValueError):
        draft.stage_time(1,bad,None)
    assert not draft.pending and draft.revision == 0


def test_sparse_equal_intent_anchor_read_does_not_overwrite_revision():
    from custom_components.casio_abl100.alarm_editing import AlarmDraft
    from custom_components.casio_abl100.alarm_write import AlarmEdit, AlarmWriteIntent
    draft = AlarmDraft()
    assert draft.value('time',2,None) is None
    observed = parse_alarms(FIRST,REST)
    draft.stage_time(2,time(8,9),observed)
    draft.stage_enabled(4,True,observed)
    draft.stage_signal(True,observed)
    revision,intent,anchor = draft.freeze()
    assert revision == 3
    assert intent == AlarmWriteIntent((AlarmEdit(2,8,9),AlarmEdit(4,enabled=True)),True)
    newer = parse_alarms(bytes.fromhex('15 c0 07 05 10'),REST)
    assert draft.value('time',1,newer) == time(5,16)
    assert draft.value('time',2,newer) == time(8,9)
    assert anchor != draft.semantic(newer)
    draft.clear(revision-1)
    assert draft.pending
    draft.clear(revision)
    assert not draft.pending and draft.revision == 4 and draft.anchor is None


@pytest.mark.parametrize('slot',[True,0,6,1.0])
def test_slot_strict(slot):
    from custom_components.casio_abl100.alarm_editing import AlarmDraft
    with pytest.raises(ValueError):
        AlarmDraft().stage_time(slot,time(1,2),None)


@pytest.mark.parametrize('value',[0,1,None,'false'])
def test_bool_strict(value):
    from custom_components.casio_abl100.alarm_editing import AlarmDraft
    draft = AlarmDraft()
    with pytest.raises(ValueError):
        draft.stage_enabled(1,value,None)
    with pytest.raises(ValueError):
        draft.stage_signal(value,None)
