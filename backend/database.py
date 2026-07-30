"""
Shared SQLite database setup for the Smart Rental Tracking System.
Creates two tables:

1. equipment       -> current/live state of each piece of equipment
                      (mirrors the 9 original fields + a derived 'status')
2. checkinout_log  -> append-only history of every check-in/check-out event

3. usage_log       -> append-only daily usage readings (feeds Module 2)

Run this file directly once to create the DB:
    python database.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "rental_tracking.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us access columns by name
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Current/live state per Equipment ID (one row per equipment)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS equipment (
            equipment_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            site_id TEXT,                      -- NULL if unassigned
            check_in_date TEXT,                -- date equipment entered current site
            check_out_date TEXT,               -- filled only once ACTUAL check-out happens
            planned_rental_days INTEGER,       -- agreed duration, entered AT check-in
            expected_due_date TEXT,            -- = check_in_date + planned_rental_days
            engine_hours_day REAL DEFAULT 0,   -- rolling average, updated by usage_logging
            idle_hours_day REAL DEFAULT 0,     -- rolling average, updated by usage_logging
            rental_days INTEGER DEFAULT 0,     -- ACTUAL duration, auto-computed on check-out
            last_operator_id TEXT,
            status TEXT DEFAULT 'Available'    -- 'Active' | 'Available'
        )
    """)

    # Append-only event history (every check-in / check-out action ever made)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS checkinout_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id TEXT NOT NULL,
            action TEXT NOT NULL,              -- 'CHECK_IN' | 'CHECK_OUT'
            event_date TEXT NOT NULL,
            site_id TEXT,
            operator_id TEXT,
            FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)
        )
    """)

    # Append-only daily usage readings (raw logs behind engine/idle hours)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id TEXT NOT NULL,
            log_date TEXT NOT NULL,
            engine_hours REAL NOT NULL,
            idle_hours REAL NOT NULL,
            fuel_used REAL NOT NULL,
            site_id TEXT,
            FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")