from datetime import datetime

import jps
from conftest import FIXTURES

HTML = (FIXTURES / "selangor_sample.html").read_text()
NOW = datetime(2026, 11, 14, 2, 30, tzinfo=jps.MALAYSIA_TIME)


def readings_by_name():
    readings, _ = jps.read_state(HTML, "SEL", NOW)
    return {r.station_name: r for r in readings}


def test_repeated_table_copies_are_removed():
    # The sample repeats rows across 3 tables, like the real page.
    assert len(jps.parse_rows(HTML)) == 8


def test_header_rows_are_ignored():
    assert all(row[0].isdigit() for row in jps.parse_rows(HTML))


def test_shared_station_id_gets_two_different_keys():
    r = readings_by_name()
    a = r["Sg. Damansara di Kg. Melayu Subang"]
    b = r["Sg. Jelok di Taman TTDI Grove"]
    assert a.station_id == b.station_id == "3115404"
    assert a.station_key != b.station_key
    assert a.station_key == "3115404#sg-damansara-di-kg-melayu-subang"


def test_level_inside_a_link_is_read():
    assert readings_by_name()["Sg. Kerling di Kerling"].level_m == 44.30


def test_status_bands():
    assert jps.level_status(44.30, 47.60, 47.90, 48.30) == "normal"
    assert jps.level_status(47.60, 47.60, 47.90, 48.30) == "alert"
    assert jps.level_status(48.00, 47.60, 47.90, 48.30) == "warning"
    assert jps.level_status(48.30, 47.60, 47.90, 48.30) == "danger"
    assert readings_by_name()["Sg. Damansara di Kg. Melayu Subang"].status == "warning"


def test_far_below_normal_is_suspect_low():
    # 4.38 m against a normal level of 50.10 m: a sensor or datum problem.
    assert readings_by_name()["Sg. Selangor di Ampang Pecah"].quality == "suspect_low"


def test_zero_level_means_no_data():
    # JPS shows 0 when a station has no reading.
    assert readings_by_name()["Sg. Kuyoh di Seri Kembangan"].quality == "no_data"


def test_tidal_station_below_zero_is_ok():
    # Near the coast, levels below 0 at low tide are real.
    reading = readings_by_name()["Sg. Klang di Bandar Klang"]
    assert reading.level_m == -0.67
    assert reading.quality == "ok"


def test_impossibly_high_is_suspect_high():
    now = NOW
    # normal 1.0, danger 2.0 -> limit = max(3 x 1.0, 3) = 3 m; 9.0 - 2.0 = 7 m > 3 m
    assert jps.quality_check(9.0, 1.0, 1.5, 1.8, 2.0, now, now) == "suspect_high"
    # 4.5 - 2.0 = 2.5 m < 3 m -> still believable (a big flood)
    assert jps.quality_check(4.5, 1.0, 1.5, 1.8, 2.0, now, now) == "ok"


def test_out_of_order_thresholds():
    assert jps.quality_check(1.0, 2.0, 1.5, 3.0, 4.0, NOW, NOW) == "bad_thresholds"


def test_old_reading_is_stale():
    # Last updated 13/11 20:00, checked at 14/11 02:30: 6.5 hours old.
    assert readings_by_name()["Sg. Kundang di Kg. Kundang"].quality == "stale"


def test_fresh_normal_reading_is_ok():
    assert readings_by_name()["Sg. Kerling di Kerling"].quality == "ok"


def test_row_with_missing_level_is_skipped_not_crashed():
    readings, skipped = jps.read_state(HTML, "SEL", NOW)
    assert skipped == 1
    assert "Sg. Broken di Contoh" not in {r.station_name for r in readings}


def test_time_is_malaysia_time():
    assert readings_by_name()["Sg. Kerling di Kerling"].observed_at == "2026-11-14T01:30:00+08:00"
