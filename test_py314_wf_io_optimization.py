"""Compatibilitatea completarii campurilor din cache dupa reducerea reflectiei."""

from dataclasses import dataclass, field
import pickle

from loto_enterprise.core.walk_forward_adapter import (
    WalkForwardResult,
    _backfill_new_fields,
)


def test_backfill_mixed_legacy_classes_and_inherited_fields():
    @dataclass
    class BaseRecord:
        required: int
        values: list[int] = field(default_factory=list)

    @dataclass
    class ExtendedRecord(BaseRecord):
        context: dict = field(default_factory=dict)

    first = BaseRecord.__new__(BaseRecord)
    second = ExtendedRecord.__new__(ExtendedRecord)
    third = BaseRecord.__new__(BaseRecord)
    second.required = 9
    third.values = [7]
    _backfill_new_fields([first, object(), second, None, third])

    assert first.required is None
    assert first.values == []
    assert second.required == 9
    assert second.values == []
    assert second.context == {}
    assert third.required is None
    assert third.values == [7]
    # default_factory trebuie evaluata pe fiecare obiect vechi, nu pe clasa.
    assert first.values is not second.values


def test_backfill_keeps_shared_ticket_context_and_existing_fields():
    context = {"pool": [1, 2, 3, 4, 5, 6]}
    rows = [
        WalkForwardResult(
            10,
            "2026-10-30",
            [1, 2, 3, 4, 5],
            hits,
            4,
            wheel_coverage=100.0,
            ticket_context=context,
        )
        for hits in (1, 2, 3)
    ]
    before = pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL)
    _backfill_new_fields(rows)
    assert pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL) == before
    assert all(row.ticket_context is context for row in rows)


def test_backfill_reloads_schema_on_each_call():
    @dataclass
    class Record:
        required: int

    @dataclass
    class NewFields:
        context: dict = field(default_factory=dict)

    old = Record.__new__(Record)
    _backfill_new_fields([old])
    assert old.required is None

    # Modeleaza un camp aditiv pe o clasa deja intalnita in acelasi proces.
    Record.__dataclass_fields__["context"] = NewFields.__dataclass_fields__["context"]
    _backfill_new_fields([old])
    assert old.context == {}


def test_backfill_empty_dataclass_and_iterable():
    @dataclass
    class EmptyRecord:
        pass

    items = [EmptyRecord(), EmptyRecord()]
    _backfill_new_fields(iter(items))
    _backfill_new_fields(None)
    assert all(item.__dict__ == {} for item in items)
