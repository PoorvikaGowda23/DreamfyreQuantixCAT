"""
Module 3: Overdue Alerts & Notifications (Dashboard-Only)

Core idea: this module is READ-ONLY / derived -- it never writes to the
database. It simply compares each ACTIVE equipment's `expected_due_date`
(set at check-in time, see Module 1) against TODAY's date, and classifies
it into one of three buckets:

    OVERDUE    -> today is PAST expected_due_date
    DUE_SOON   -> expected_due_date is within DUE_SOON_THRESHOLD_DAYS
    ON_TRACK   -> everything else (still comfortably within the rental window)

The output of this module is meant to feed a live badge on the Asset Dashboard 
(e.g. a red "Overdue" pill or an amber "Due Soon" pill next to each machine, plus a top-line
counter like "3 Overdue | 2 Due Soon").A badge recalculates instantly every time the dashboard 
is refreshed, so there's nothing that depends on flaky delivery timing.

Only equipment with status = 'Active' (i.e. currently checked out to a
site) is considered -- equipment sitting in the yard ('Available') has
no expected_due_date to violate.
"""

import sys
import os
from datetime import date, datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection

# How many days before the expected due date counts as "Due Soon".
# e.g. 2 -> equipment due today, tomorrow, or the day after shows as Due Soon.
DUE_SOON_THRESHOLD_DAYS = 3

STATUS_OVERDUE = "Overdue"
STATUS_DUE_SOON = "Due Soon"
STATUS_ON_TRACK = "On Track"


def _classify(expected_due_date_str, today=None):
    """
    Given an expected_due_date ('YYYY-MM-DD') and today's date, return
    (alert_status, days_over_or_left).

    days_over_or_left:
        positive  -> days OVERDUE (only when alert_status == Overdue)
        0 or pos. -> days remaining until due (Due Soon / On Track)
    """
    today = today or date.today()
    due = datetime.strptime(expected_due_date_str, "%Y-%m-%d").date()
    delta_days = (due - today).days  # negative if already past due

    if delta_days < 0:
        return STATUS_OVERDUE, abs(delta_days)
    elif delta_days <= DUE_SOON_THRESHOLD_DAYS:
        return STATUS_DUE_SOON, delta_days
    else:
        return STATUS_ON_TRACK, delta_days


def get_overdue_report(today=None):
    """
    Main function behind the dashboard's alert list/badges.

    Scans every ACTIVE piece of equipment and returns one row per
    equipment with its computed alert_status, so the dashboard can just
    render a colored badge per row without doing any date math itself.

    Returns:
        list[dict]: sorted so the most urgent items (Overdue first, then
        Due Soon, then On Track) appear at the top -- ready to render
        top-down on the dashboard as-is.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT equipment_id, type, site_id, check_in_date,
               planned_rental_days, expected_due_date, last_operator_id
        FROM equipment
        WHERE status = 'Active' AND expected_due_date IS NOT NULL
    """).fetchall()
    conn.close()

    report = []
    for row in rows:
        alert_status, days_value = _classify(row["expected_due_date"], today)
        item = dict(row)
        item["alert_status"] = alert_status
        item["days_overdue"] = days_value if alert_status == STATUS_OVERDUE else 0
        item["days_until_due"] = days_value if alert_status != STATUS_OVERDUE else 0
        report.append(item)

    # Sort: Overdue first (most overdue on top), then Due Soon (soonest
    # first), then On Track. This is exactly the order the dashboard
    # should list rows in.
    severity_rank = {STATUS_OVERDUE: 0, STATUS_DUE_SOON: 1, STATUS_ON_TRACK: 2}
    report.sort(key=lambda r: (
        severity_rank[r["alert_status"]],
        -r["days_overdue"] if r["alert_status"] == STATUS_OVERDUE else r["days_until_due"]
    ))
    return report


def get_equipment_alert_status(equipment_id, today=None):
    """
    Single-equipment lookup -- used for a per-machine badge, e.g. on the
    equipment detail/drill-down view.

    Returns:
        dict: {equipment_id, alert_status, days_overdue, days_until_due,
               expected_due_date} or an error dict if not found / not
               currently checked out.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"{equipment_id} not found."}
    if row["status"] != "Active" or not row["expected_due_date"]:
        return {"error": f"{equipment_id} is not currently checked out."}

    alert_status, days_value = _classify(row["expected_due_date"], today)
    return {
        "equipment_id": equipment_id,
        "expected_due_date": row["expected_due_date"],
        "alert_status": alert_status,
        "days_overdue": days_value if alert_status == STATUS_OVERDUE else 0,
        "days_until_due": days_value if alert_status != STATUS_OVERDUE else 0,
    }


def get_alert_summary(today=None):
    """
    Top-line counts for the dashboard header badge, e.g.:
        "3 Overdue | 2 Due Soon | 15 On Track"

    Returns:
        dict: {overdue_count, due_soon_count, on_track_count, total_active}
    """
    report = get_overdue_report(today)
    summary = {
        "overdue_count": sum(1 for r in report if r["alert_status"] == STATUS_OVERDUE),
        "due_soon_count": sum(1 for r in report if r["alert_status"] == STATUS_DUE_SOON),
        "on_track_count": sum(1 for r in report if r["alert_status"] == STATUS_ON_TRACK),
        "total_active": len(report),
    }
    return summary


def get_dashboard_badge_color(alert_status):
    """
    Convenience helper for the frontend: maps an alert_status string to
    the badge color it should render as. Keeps color logic centralized
    here instead of duplicated in dashboard code.
    """
    return {
        STATUS_OVERDUE: "red",
        STATUS_DUE_SOON: "amber",
        STATUS_ON_TRACK: "green",
    }.get(alert_status, "grey")


if __name__ == "__main__":
    # Quick manual test
    from database import init_db
    from checkin_checkout import check_in

    init_db()

    # Overdue example: checked in 20 days ago with a 10-day plan
    check_in("EQXA001", "S001", "OP101", planned_rental_days=10,
              equipment_type="Excavator",
              event_date="2026-07-10")

    # Due soon example: checked in with a plan that ends in 1 day (relative
    # to "today" being 2026-07-30 in this test)
    check_in("EQXA002", "S002", "OP102", planned_rental_days=1,
              equipment_type="Crane",
              event_date="2026-07-29")

    # On track example: plenty of time left
    check_in("EQXA003", "S003", "OP103", planned_rental_days=30,
              equipment_type="Bulldozer",
              event_date="2026-07-28")

    test_today = date(2026, 7, 30)

    print("--- Full overdue report ---")
    for row in get_overdue_report(today=test_today):
        print(row["equipment_id"], row["alert_status"],
              "days_overdue=", row["days_overdue"],
              "days_until_due=", row["days_until_due"])

    print("--- Single equipment lookup ---")
    print(get_equipment_alert_status("EQXA001", today=test_today))

    print("--- Dashboard summary badge ---")
    print(get_alert_summary(today=test_today))