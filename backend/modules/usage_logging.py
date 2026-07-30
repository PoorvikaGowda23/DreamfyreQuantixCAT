"""
Module 2: Usage Logging

Since there's no real hardware/sensors, usage data is SIMULATED for any
equipment currently checked in (status = 'Active'). Call simulate_day()
once per "day" -- either on a real schedule (cron) or via a demo
"Simulate Day" button -- to generate one new reading per active equipment.

Each reading is stored permanently in usage_log (raw history), and the
equipment table's engine_hours_day / idle_hours_day are updated to the
ROLLING AVERAGE across all logs for that equipment -- exactly mirroring
the aggregated columns in your original dataset.
"""

import sys
import os
import random
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection

# Realistic engine-hour ranges per equipment type (used for simulation only)
BASE_ENGINE_RANGE_BY_TYPE = {
    "Excavator": (2, 8),
    "Crane": (1, 6),
    "Bulldozer": (3, 9),
    "Grader": (2, 7),
}

FUEL_RATE_PER_ENGINE_HOUR = 4.5  # liters/hour, rough average across machine types


def _generate_reading(equipment_type):
    """Generate one day's (engine_hours, idle_hours, fuel_used) for a type."""
    low, high = BASE_ENGINE_RANGE_BY_TYPE.get(equipment_type, (2, 7))
    engine_hours = round(random.uniform(low, high), 1)
    # Idle hours: normally less than engine hours, occasionally an anomaly
    # (idle > engine) to give Module 4 (Anomaly Detection) real cases.
    if random.random() < 0.1:  # ~10% chance of an idle-heavy anomaly day
        idle_hours = round(engine_hours + random.uniform(1, 5), 1)
    else:
        idle_hours = round(random.uniform(0, max(engine_hours - 0.5, 0.5)), 1)

    fuel_used = round(engine_hours * FUEL_RATE_PER_ENGINE_HOUR + random.uniform(-2, 2), 1)
    fuel_used = max(fuel_used, 0)
    return engine_hours, idle_hours, fuel_used


def simulate_day(log_date=None):
    """
    Generates one new usage_log row for every equipment with status='Active',
    then recalculates that equipment's rolling-average engine_hours_day /
    idle_hours_day in the equipment table.

    Returns:
        list[dict]: the new usage log rows created.
    """
    log_date = log_date or date.today().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    active_equipment = cur.execute(
        "SELECT equipment_id, type, site_id FROM equipment WHERE status = 'Active'"
    ).fetchall()

    new_logs = []
    for eq in active_equipment:
        engine_hours, idle_hours, fuel_used = _generate_reading(eq["type"])

        cur.execute("""
            INSERT INTO usage_log (equipment_id, log_date, engine_hours, idle_hours, fuel_used, site_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (eq["equipment_id"], log_date, engine_hours, idle_hours, fuel_used, eq["site_id"]))

        new_logs.append({
            "equipment_id": eq["equipment_id"],
            "log_date": log_date,
            "engine_hours": engine_hours,
            "idle_hours": idle_hours,
            "fuel_used": fuel_used,
        })

        _update_rolling_average(cur, eq["equipment_id"])

    conn.commit()
    conn.close()
    return new_logs


def _update_rolling_average(cur, equipment_id):
    """Recompute engine_hours_day / idle_hours_day as the average across
    all usage_log rows for this equipment, and write it back to the
    equipment table (this is the field the dashboard reads directly)."""
    avg_row = cur.execute("""
        SELECT AVG(engine_hours) AS avg_engine, AVG(idle_hours) AS avg_idle
        FROM usage_log WHERE equipment_id = ?
    """, (equipment_id,)).fetchone()

    cur.execute("""
        UPDATE equipment
        SET engine_hours_day = ?, idle_hours_day = ?
        WHERE equipment_id = ?
    """, (round(avg_row["avg_engine"], 2), round(avg_row["avg_idle"], 2), equipment_id))


def get_usage_history(equipment_id):
    """Full raw usage log history for one equipment_id."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM usage_log WHERE equipment_id = ? ORDER BY log_date",
        (equipment_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_usage_summary(equipment_id):
    """
    Aggregated usage summary for ONE equipment_id -- feeds the dashboard's
    per-machine drill-down view.
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) AS days_logged,
            SUM(engine_hours) AS total_engine_hours,
            SUM(idle_hours) AS total_idle_hours,
            SUM(fuel_used) AS total_fuel_used,
            AVG(engine_hours) AS avg_engine_hours_day,
            AVG(idle_hours) AS avg_idle_hours_day
        FROM usage_log WHERE equipment_id = ?
    """, (equipment_id,)).fetchone()
    conn.close()

    summary = dict(row)
    total_hours = (summary["total_engine_hours"] or 0) + (summary["total_idle_hours"] or 0)
    summary["utilization_pct"] = (
        round((summary["total_engine_hours"] or 0) / total_hours * 100, 1)
        if total_hours > 0 else 0
    )
    return summary


def get_fleet_summary():
    """
    Total rented hours across the ENTIRE fleet (all equipment combined).
    This is the top-line number for the dashboard, e.g.
    "Total Rented Hours: 1,240 | Total Idle Hours: 310 | Fleet Utilization: 80%".
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(DISTINCT equipment_id) AS equipment_logged,
            SUM(engine_hours) AS total_engine_hours,
            SUM(idle_hours) AS total_idle_hours,
            SUM(fuel_used) AS total_fuel_used
        FROM usage_log
    """).fetchone()
    conn.close()

    summary = dict(row)
    total_hours = (summary["total_engine_hours"] or 0) + (summary["total_idle_hours"] or 0)
    summary["fleet_utilization_pct"] = (
        round((summary["total_engine_hours"] or 0) / total_hours * 100, 1)
        if total_hours > 0 else 0
    )
    return summary


def get_summary_by_site():
    """
    Usage totals GROUPED BY site_id -- answers "usage per site" directly.
    Returns one row per site with total engine/idle hours and fuel used
    across every piece of equipment ever logged at that site.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            site_id,
            COUNT(DISTINCT equipment_id) AS equipment_count,
            SUM(engine_hours) AS total_engine_hours,
            SUM(idle_hours) AS total_idle_hours,
            SUM(fuel_used) AS total_fuel_used
        FROM usage_log
        WHERE site_id IS NOT NULL AND site_id != 'NULL'
        GROUP BY site_id
        ORDER BY total_engine_hours DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_idle_downtime_report():
    """
    DOWNTIME (definition 1): idle hours while equipment IS checked in/rented.
    This is "we're paying for it but it's not being used" downtime.
    Grouped per equipment_id, using the same usage_log table.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            equipment_id,
            SUM(idle_hours) AS total_idle_hours,
            SUM(engine_hours) AS total_engine_hours,
            ROUND(
                SUM(idle_hours) * 100.0 / NULLIF(SUM(idle_hours) + SUM(engine_hours), 0), 1
            ) AS idle_pct
        FROM usage_log
        GROUP BY equipment_id
        ORDER BY idle_pct DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_yard_downtime_report():
    """
    DOWNTIME (definition 2): time equipment sits UNRENTED in the yard between
    a check-out and its next check-in -- "this asset is earning nothing".
    Calculated from checkinout_log (not usage_log), by pairing each
    CHECK_OUT with the following CHECK_IN for the same equipment_id.
    """
    conn = get_connection()
    logs = conn.execute("""
        SELECT equipment_id, action, event_date
        FROM checkinout_log
        ORDER BY equipment_id, event_date
    """).fetchall()
    conn.close()

    # Walk through each equipment's event history looking for
    # CHECK_OUT -> next CHECK_IN pairs (gaps in the yard).
    from collections import defaultdict
    from datetime import datetime as dt

    history = defaultdict(list)
    for row in logs:
        history[row["equipment_id"]].append((row["action"], row["event_date"]))

    yard_gaps = []
    for equipment_id, events in history.items():
        for i in range(len(events) - 1):
            action, event_date = events[i]
            next_action, next_date = events[i + 1]
            if action == "CHECK_OUT" and next_action == "CHECK_IN":
                gap_days = (
                    dt.strptime(next_date, "%Y-%m-%d")
                    - dt.strptime(event_date, "%Y-%m-%d")
                ).days
                yard_gaps.append({
                    "equipment_id": equipment_id,
                    "checked_out_on": event_date,
                    "checked_in_again_on": next_date,
                    "yard_downtime_days": gap_days,
                })

    return yard_gaps


if __name__ == "__main__":
    # Quick manual test -- requires an equipment row already checked in.
    from database import init_db
    from checkin_checkout import check_in, check_out

    init_db()
    check_in("EQX9002", "S004", "OP888", planned_rental_days=20,
              equipment_type="Bulldozer")
    check_in("EQX9003", "S004", "OP889", planned_rental_days=10,
              equipment_type="Excavator")

    for _ in range(5):  # simulate 5 days of usage across both machines
        simulate_day()

    print("--- Per-equipment summary ---")
    print(get_usage_summary("EQX9002"))

    print("--- Fleet-wide summary ---")
    print(get_fleet_summary())

    print("--- Usage per site ---")
    print(get_summary_by_site())

    print("--- Idle downtime (while rented) ---")
    print(get_idle_downtime_report())

    check_out("EQX9002", event_date="2026-08-05")
    check_in("EQX9002", "S004", "OP888", planned_rental_days=10,
             event_date="2026-08-09")  # sat 4 days unrented in the yard

    print("--- Yard downtime (between rentals) ---")
    print(get_yard_downtime_report())