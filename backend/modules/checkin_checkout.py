"""
Module 1: Check-In / Check-Out System

Core idea: this is an EVENT-DRIVEN system, not a static table.
- check_in()  -> equipment ARRIVES at a site (status becomes 'Active').
                 Also takes a PLANNED rental duration (agreed up front),
                 used to compute Expected Due Date = check_in + planned_days.
                 This is what Module 3 (Overdue Alerts) compares against
                 today's date WHILE the equipment is still checked out.
- check_out() -> equipment LEAVES the site (status becomes 'Available').
                 Rental Days here is the ACTUAL duration, calculated as
                 (real check-out date - check-in date) -- this is only
                 known once the equipment truly returns, and is what
                 becomes historical data (matches your original dataset,
                 which only contains closed/completed rentals).

So "Rental Days" has two versions in this system:
  planned_rental_days -> input at check-in, drives Expected Due Date / alerts
  rental_days          -> actual, computed at check-out, used for history/reports

Every action is written to:
  1. equipment table   -> updated to reflect current/live state
  2. checkinout_log     -> permanent history of the event (never overwritten)

QR/RFID scanning is simulated here by simply passing in an equipment_id
string (as if it had just been scanned) -- swap this for real scanner
input later without changing the logic below.
"""

import sys
import os
from datetime import date, datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection


def check_in(equipment_id, site_id, operator_id, planned_rental_days,
             equipment_type=None, event_date=None):
    """
    Registers equipment arriving at a site.

    Args:
        equipment_id (str): e.g. 'EQX1001'
        site_id (str): e.g. 'S003'
        operator_id (str): e.g. 'OP101'
        planned_rental_days (int): agreed rental duration, entered by the
                                    site manager AT check-in time (e.g. 15).
                                    Used to compute Expected Due Date for
                                    Module 3 (Overdue Alerts).
        equipment_type (str): only needed the FIRST time a new equipment_id
                               is seen (e.g. 'Excavator'); ignored if the
                               equipment already exists.
        event_date (str): 'YYYY-MM-DD'; defaults to today (real-time stamp).

    Returns:
        dict: the updated equipment record, or an error dict.
    """
    event_date = event_date or date.today().isoformat()
    check_in_dt = datetime.strptime(event_date, "%Y-%m-%d").date()
    expected_due_date = (check_in_dt + timedelta(days=planned_rental_days)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()

    if existing and existing["status"] == "Active":
        conn.close()
        return {"error": f"{equipment_id} is already checked in at a site."}

    if existing:
        # Equipment known -> just update its live state
        cur.execute("""
            UPDATE equipment
            SET site_id = ?, check_in_date = ?, check_out_date = NULL,
                planned_rental_days = ?, expected_due_date = ?,
                last_operator_id = ?, status = 'Active'
            WHERE equipment_id = ?
        """, (site_id, event_date, planned_rental_days, expected_due_date,
              operator_id, equipment_id))
    else:
        # Brand-new equipment_id -> insert a fresh row
        if not equipment_type:
            conn.close()
            return {"error": "equipment_type is required for a new Equipment ID."}
        cur.execute("""
            INSERT INTO equipment
                (equipment_id, type, site_id, check_in_date, check_out_date,
                 planned_rental_days, expected_due_date,
                 engine_hours_day, idle_hours_day, rental_days,
                 last_operator_id, status)
            VALUES (?, ?, ?, ?, NULL, ?, ?, 0, 0, 0, ?, 'Active')
        """, (equipment_id, equipment_type, site_id, event_date,
              planned_rental_days, expected_due_date, operator_id))

    # Always log the event in the permanent history table
    cur.execute("""
        INSERT INTO checkinout_log (equipment_id, action, event_date, site_id, operator_id)
        VALUES (?, 'CHECK_IN', ?, ?, ?)
    """, (equipment_id, event_date, site_id, operator_id))

    conn.commit()
    updated = cur.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()
    return dict(updated)


def check_out(equipment_id, event_date=None):
    """
    Registers equipment leaving its current site (the REAL/actual event --
    may happen earlier or later than planned_rental_days predicted).
    Auto-calculates ACTUAL Rental Days = Check-Out Date - Check-In Date.
    (planned_rental_days / expected_due_date are left untouched here, so
    you can compare planned vs. actual afterwards if useful.)

    Returns:
        dict: the updated equipment record, or an error dict.
    """
    event_date = event_date or date.today().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()

    if not existing:
        conn.close()
        return {"error": f"{equipment_id} not found."}
    if existing["status"] != "Active":
        conn.close()
        return {"error": f"{equipment_id} is not currently checked in."}

    check_in_date = datetime.strptime(existing["check_in_date"], "%Y-%m-%d").date()
    check_out_date = datetime.strptime(event_date, "%Y-%m-%d").date()
    rental_days = (check_out_date - check_in_date).days

    cur.execute("""
        UPDATE equipment
        SET check_out_date = ?, rental_days = ?, status = 'Available'
        WHERE equipment_id = ?
    """, (event_date, rental_days, equipment_id))

    cur.execute("""
        INSERT INTO checkinout_log (equipment_id, action, event_date, site_id, operator_id)
        VALUES (?, 'CHECK_OUT', ?, ?, ?)
    """, (equipment_id, event_date, existing["site_id"], existing["last_operator_id"]))

    conn.commit()
    updated = cur.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()
    return dict(updated)


def get_equipment_status(equipment_id):
    """Fetch the current live record for a single equipment_id."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM equipment WHERE equipment_id = ?", (equipment_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {"error": f"{equipment_id} not found."}


def get_all_equipment():
    """Fetch live state for every equipment_id -- this is what the
    Asset Dashboard's 'live status' table pulls directly from."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM equipment").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_history(equipment_id):
    """Full check-in/check-out event history for one equipment_id."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM checkinout_log WHERE equipment_id = ? ORDER BY event_date",
        (equipment_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    # Quick manual test
    from database import init_db
    init_db()

    print(check_in("EQX9001", "S003", "OP999", planned_rental_days=15,
                    equipment_type="Excavator"))
    print(get_equipment_status("EQX9001"))
    print(check_out("EQX9001", event_date="2026-08-10"))  # actual: returned early/late vs plan
    print(get_history("EQX9001"))