"""Equivalent instants must propagate to the same position across offsets."""
from datetime import date, datetime, timedelta, timezone

import pytest

from solar_db.positions import date_to_jd


@pytest.mark.parametrize("value", [
    "2026-10-25T01:30:00+01:00",
    "2026-10-24T20:30:00-04:00",
    datetime(2026, 10, 25, 1, 30, tzinfo=timezone(timedelta(hours=1))),
    datetime(2026, 10, 24, 20, 30, tzinfo=timezone(timedelta(hours=-4))),
])
def test_equivalent_offsets_use_utc_calendar_fields(value):
    assert date_to_jd(value) == date_to_jd("2026-10-25T00:30:00Z")


@pytest.mark.parametrize("value", ["2000-01-01T12:00:00", datetime(2000, 1, 1, 12)])
def test_naive_inputs_keep_documented_utc_semantics(value):
    assert date_to_jd(value) == 2451545.0


def test_offset_crossing_leap_day_and_date_only():
    assert date_to_jd("2024-03-01T01:00:00+01:00") == date_to_jd(date(2024, 3, 1))
    assert date_to_jd("2024-03-01T00:30:00+01:00") == date_to_jd("2024-02-29T23:30:00Z")


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:30:00-01:00"])
def test_offsets_outside_python_calendar_fail_as_invalid_dates(value):
    with pytest.raises(ValueError, match="supported UTC calendar range"):
        date_to_jd(value)
