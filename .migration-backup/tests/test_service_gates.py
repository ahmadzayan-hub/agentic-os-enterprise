"""The gate that decides whether a missing service is a skip or a failure.

This suite exists because the gate is itself a control: if it silently
degraded to "always skip", the whole integration suite would report green on
an empty run and nothing else would notice. So the gate is tested the same way
everything else here is — by exercising it, in both directions.
"""

from __future__ import annotations

import pytest

from tests import conftest

pytestmark = [pytest.mark.unit]


class _Item:
    """The two things pytest_runtest_setup asks of an item."""

    def __init__(self, *names: str) -> None:
        self._marks = [pytest.mark.requires_service(n).mark for n in names]

    def iter_markers(self, name: str):
        return [m for m in self._marks if m.name == name]


@pytest.fixture()
def unavailable(monkeypatch):
    """Present the gate with a service that is definitively not there."""
    monkeypatch.setitem(conftest.SERVICES, "phantom", (lambda: False, "phantom is absent"))
    conftest.service_available.cache_clear()
    yield "phantom"
    conftest.service_available.cache_clear()


def test_an_absent_service_skips_when_nothing_requires_it(monkeypatch, unavailable):
    monkeypatch.delenv("AGENTIC_REQUIRE_SERVICES", raising=False)
    with pytest.raises(pytest.skip.Exception) as caught:
        conftest.pytest_runtest_setup(_Item(unavailable))
    assert "phantom is absent" in str(caught.value)


def test_an_absent_service_fails_when_it_is_required(monkeypatch, unavailable):
    """The point of the whole mechanism: no green run without the evidence."""
    monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", unavailable)
    # Not skip.Exception: the whole point is that this run does not pass.
    with pytest.raises(pytest.fail.Exception) as caught:
        conftest.pytest_runtest_setup(_Item(unavailable))
    assert "phantom is absent" in str(caught.value)


def test_requiring_one_service_does_not_silently_require_another(monkeypatch, unavailable):
    monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", "db")
    with pytest.raises(pytest.skip.Exception):
        conftest.pytest_runtest_setup(_Item(unavailable))


def test_the_shorthand_requires_every_known_service(monkeypatch):
    for value in ("1", "true", "yes", "all", "ALL"):
        monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", value)
        assert conftest.required_services() == frozenset(conftest.SERVICES), value


def test_an_available_service_neither_skips_nor_fails(monkeypatch):
    monkeypatch.setitem(conftest.SERVICES, "phantom", (lambda: True, "unused"))
    monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", "phantom")
    conftest.service_available.cache_clear()
    try:
        assert conftest.pytest_runtest_setup(_Item("phantom")) is None
    finally:
        conftest.service_available.cache_clear()


def test_a_misspelt_service_name_is_rejected_rather_than_ignored(monkeypatch):
    """A typo in CI must not quietly mean "require nothing"."""
    monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", "redsi")
    with pytest.raises(pytest.UsageError) as caught:
        conftest.required_services()
    assert "redsi" in str(caught.value)


def test_no_value_requires_nothing(monkeypatch):
    monkeypatch.delenv("AGENTIC_REQUIRE_SERVICES", raising=False)
    assert conftest.required_services() == frozenset()
    monkeypatch.setenv("AGENTIC_REQUIRE_SERVICES", "  ")
    assert conftest.required_services() == frozenset()


def test_every_gate_the_suites_use_names_a_known_service():
    """A mark naming an unregistered service would raise KeyError mid-run."""
    for mark in (conftest.requires_db, conftest.requires_redis, conftest.requires_dr_identity):
        assert mark.mark.args[0] in conftest.SERVICES
