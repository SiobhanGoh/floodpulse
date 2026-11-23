"""Read and clean river water-level tables from JPS Public Infobanjir.

Everything in this file is plain Python with no AWS code, so it can be
tested on a laptop without an AWS account.
"""

import re
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

PAGE_URL = (
    "https://publicinfobanjir.water.gov.my/aras-air/data-paras-air/"
    "aras-air-data/?state={state}&district=ALL&station=ALL&lang=en"
)
USER_AGENT = "FloodPulse learning project (github.com/SiobhanGoh)"
MALAYSIA_TIME = timezone(timedelta(hours=8))

# Readings older than this are marked "stale" (the station may be offline).
STALE_AFTER = timedelta(hours=2)

# Status names, in order of seriousness.
STATUSES = ("normal", "alert", "warning", "danger")

# A reading must be at least this far outside the normal-danger range
# before it is called suspect (protects stations with a narrow range,
# such as tidal stations near the coast).
MIN_SUSPECT_GAP_M = 3.0

# Only readings with this quality may ever trigger an alert.
ALERTABLE_QUALITY = "ok"


# --------------------------------------------------------------------------
# 1. Download
# --------------------------------------------------------------------------
def fetch_state_page(state: str, timeout: int = 20) -> str:
    """Download one state's station table as HTML text."""
    request = urllib.request.Request(
        PAGE_URL.format(state=state), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# 2. Read the table
# --------------------------------------------------------------------------
class _TableReader(HTMLParser):
    """Collects every table row on the page as a list of cell texts."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.in_cell = [], [], "", False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell, self.cell = True, ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.row.append(" ".join(self.cell.split()))
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data


def parse_rows(html: str) -> list[list[str]]:
    """Return the station rows, with the page's repeated copies removed.

    Found in Phase 1: the Selangor page repeats its 66-row table about
    8 times (540 rows). dict.fromkeys keeps the first copy of each row,
    in the original order.
    """
    reader = _TableReader()
    reader.feed(html)
    station_rows = [r for r in reader.rows if len(r) >= 12 and r[0].isdigit()]
    unique = dict.fromkeys(tuple(r) for r in station_rows)
    return [list(r) for r in unique]


# --------------------------------------------------------------------------
# 3. Turn a row into a clean reading
# --------------------------------------------------------------------------
@dataclass
class Reading:
    station_key: str      # station ID + name (IDs alone are not unique)
    station_id: str
    station_name: str
    state: str
    district: str
    main_basin: str
    sub_basin: str
    observed_at: str      # ISO 8601 time with +08:00 (Malaysia time)
    level_m: float
    normal_m: float
    alert_m: float
    warning_m: float
    danger_m: float
    status: str           # normal / alert / warning / danger
    quality: str          # ok / stale / no_data / suspect_high / suspect_low / bad_thresholds

    def to_item(self) -> dict:
        return asdict(self)


def slugify(text: str) -> str:
    """'Sg. Jelok di Taman TTDI Grove' -> 'sg-jelok-di-taman-ttdi-grove'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_station_key(station_id: str, station_name: str) -> str:
    """Found in Phase 1: JPS gives two different stations the ID 3115404,
    so the key combines the ID with the station's name."""
    return f"{station_id}#{slugify(station_name)}"


def parse_time(text: str) -> datetime:
    """'14/11/2026 01:30' (Malaysia time) -> timezone-aware datetime."""
    return datetime.strptime(text, "%d/%m/%Y %H:%M").replace(tzinfo=MALAYSIA_TIME)


def level_status(level: float, alert: float, warning: float, danger: float) -> str:
    """Which band the water level is in."""
    if level >= danger:
        return "danger"
    if level >= warning:
        return "warning"
    if level >= alert:
        return "alert"
    return "normal"


def quality_check(level, normal, alert, warning, danger, observed, now) -> str:
    """Decide whether a reading can be trusted. Only "ok" can trigger alerts.

    no_data:        the level is exactly 0. JPS shows 0 when a station has
                    no reading (Sg. Kerling read 44.30 m one week, 0 the next).
    bad_thresholds: the station's thresholds are out of order.
    suspect_high /
    suspect_low:    the level is far above danger / far below normal.
                    "Far" means more than the limit:
                        limit = max(3 x (danger - normal), 3 m)
                    Example: Sg. Selangor di Ampang Pecah read 4.21 m with
                    normal 50.1 m and danger 51.9 m.
                    limit = max(3 x 1.8, 3) = 5.4 m;
                    normal - level = 45.89 m > 5.4 m  ->  suspect_low
                    The 3 m floor keeps tidal stations near the coast "ok":
                    Sg. Klang di Bandar Klang read -0.67 m at low tide
                    (normal 2.2, danger 3): limit = max(2.4, 3) = 3 m;
                    2.2 - (-0.67) = 2.87 m < 3 m  ->  ok
    stale:          the reading is older than STALE_AFTER.
    """
    if level == 0:
        return "no_data"
    band = danger - normal
    if not (normal <= alert <= warning <= danger) or band <= 0:
        return "bad_thresholds"
    limit = max(3 * band, MIN_SUSPECT_GAP_M)
    if level - danger > limit:
        return "suspect_high"
    if normal - level > limit:
        return "suspect_low"
    if now - observed > STALE_AFTER:
        return "stale"
    return "ok"


def to_reading(row: list[str], state: str, now: datetime) -> Reading | None:
    """Convert one table row into a Reading. Returns None if the row
    can't be read (for example a missing number)."""
    try:
        (_, station_id, name, district, main_basin, sub_basin,
         time_text, level, normal, alert, warning, danger) = row[:12]
        observed = parse_time(time_text)
        level, normal, alert, warning, danger = (
            float(level), float(normal), float(alert), float(warning), float(danger)
        )
    except (ValueError, IndexError):
        return None

    return Reading(
        station_key=make_station_key(station_id, name),
        station_id=station_id,
        station_name=name,
        state=state,
        district=district,
        main_basin=main_basin,
        sub_basin=sub_basin,
        observed_at=observed.isoformat(),
        level_m=level,
        normal_m=normal,
        alert_m=alert,
        warning_m=warning,
        danger_m=danger,
        status=level_status(level, alert, warning, danger),
        quality=quality_check(level, normal, alert, warning, danger, observed, now),
    )


def read_state(html: str, state: str, now: datetime) -> tuple[list[Reading], int]:
    """Parse a whole page. Returns (readings, number of rows skipped)."""
    rows = parse_rows(html)
    readings = [r for r in (to_reading(row, state, now) for row in rows) if r]
    return readings, len(rows) - len(readings)
