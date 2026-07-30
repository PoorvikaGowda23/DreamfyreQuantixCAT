"""
Synthetic data generator for Smart Rental Tracking System.
Generates a single CSV containing exactly the 9 existing fields:
Equipment ID, Type, Site ID, Check-In Date, Check-Out Date,
Engine Hours/Day, Idle Hours/Day, Rental Days, Last Operator ID

Design notes:
- Spreads rentals across 12 months (2025) so Demand Forecasting has
  enough history to show a real trend.
- Builds in a gentle upward trend for Excavators and Graders (so the
  forecast chart shows something meaningful), and a flat/declining
  trend for Cranes.
- Deliberately injects a controlled % of anomalies (NULL site/operator,
  idle > engine hours, zero engine activity, unusually long rentals) so
  Anomaly Detection has real cases to catch.
- Includes your original 7 rows unchanged, then appends new synthetic rows.
"""

import csv
import random
from datetime import date, timedelta

random.seed(42)

EQUIPMENT_TYPES = ["Excavator", "Crane", "Bulldozer", "Grader"]
SITE_IDS = ["S001", "S002", "S003", "S004", "S005", "S006"]

# Monthly demand weight per type -> controls how many rentals get
# generated for that type in that month (index 0 = Jan 2025 ... 11 = Dec 2025)
# Excavator & Grader trend upward, Bulldozer stays flat-ish, Crane trends down.
MONTHLY_WEIGHTS = {
    "Excavator": [2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7],
    "Grader":    [1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5],
    "Bulldozer": [3, 3, 3, 2, 3, 3, 2, 3, 3, 2, 3, 3],
    "Crane":     [4, 4, 3, 3, 3, 2, 2, 2, 1, 1, 1, 1],
}


def month_date_range(month_index):
    """Return (first_day, last_day) for the given 0-indexed month of 2025."""
    year = 2025 + (month_index // 12)
    month = (month_index % 12) + 1
    first_day = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return first_day, last_day


def random_date_in_range(start, end):
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta_days, 0)))


def generate_engine_idle_hours(equipment_type, is_anomalous_idle, is_zero_activity):
    """Generate realistic Engine Hours/Day and Idle Hours/Day."""
    if is_zero_activity:
        engine_hours = 0
        idle_hours = round(random.uniform(8, 12), 1)
        return engine_hours, idle_hours

    base_engine_by_type = {
        "Excavator": (2, 8),
        "Crane": (1, 6),
        "Bulldozer": (3, 9),
        "Grader": (2, 7),
    }
    low, high = base_engine_by_type[equipment_type]
    engine_hours = round(random.uniform(low, high), 1)

    if is_anomalous_idle:
        # Force idle hours to exceed engine hours (anomaly case)
        idle_hours = round(engine_hours + random.uniform(1, 6), 1)
    else:
        # Normal case: idle hours generally less than engine hours
        idle_hours = round(random.uniform(0, max(engine_hours - 0.5, 0.5)), 1)

    return engine_hours, idle_hours


def generate_rental_days(equipment_type, is_long_rental):
    base_range_by_type = {
        "Excavator": (8, 20),
        "Crane": (5, 15),
        "Bulldozer": (10, 25),
        "Grader": (7, 18),
    }
    low, high = base_range_by_type[equipment_type]
    if is_long_rental:
        return random.randint(high + 10, high + 25)
    return random.randint(low, high)


def build_rows():
    rows = []
    equipment_counter = 1008  # continue after EQX1007

    for month_index in range(12):
        month_start, month_end = month_date_range(month_index)

        for eq_type in EQUIPMENT_TYPES:
            count = MONTHLY_WEIGHTS[eq_type][month_index]

            for _ in range(count):
                equipment_id = f"EQX{equipment_counter}"
                equipment_counter += 1

                check_in = random_date_in_range(month_start, month_end)

                # Decide anomaly flags for this row (controlled %)
                roll = random.random()
                is_unassigned = roll < 0.08                # ~8% missing site/operator
                is_anomalous_idle = 0.08 <= roll < 0.18    # ~10% idle > engine
                is_zero_activity = 0.18 <= roll < 0.24     # ~6% zero engine activity
                is_long_rental = 0.24 <= roll < 0.30       # ~6% unusually long rental

                rental_days = generate_rental_days(eq_type, is_long_rental)
                check_out = check_in + timedelta(days=rental_days)

                engine_hours, idle_hours = generate_engine_idle_hours(
                    eq_type, is_anomalous_idle, is_zero_activity
                )

                if is_unassigned:
                    site_id = "NULL"
                    operator_id = "NULL"
                else:
                    site_id = random.choice(SITE_IDS)
                    operator_id = f"OP{random.randint(100, 350)}"

                rows.append({
                    "Equipment ID": equipment_id,
                    "Type": eq_type,
                    "Site ID": site_id,
                    "Check-In Date": check_in.isoformat(),
                    "Check-Out Date": check_out.isoformat(),
                    "Engine Hours/Day": engine_hours,
                    "Idle Hours/Day": idle_hours,
                    "Rental Days": rental_days,
                    "Last Operator ID": operator_id,
                })

    return rows


# Original 7 rows from the problem statement (kept exactly as given)
ORIGINAL_ROWS = [
    {"Equipment ID": "EQX1001", "Type": "Excavator", "Site ID": "S003",
     "Check-In Date": "2025-04-01", "Check-Out Date": "2025-04-16",
     "Engine Hours/Day": 1.5, "Idle Hours/Day": 10, "Rental Days": 15,
     "Last Operator ID": "OP101"},
    {"Equipment ID": "EQX1002", "Type": "Crane", "Site ID": "NULL",
     "Check-In Date": "2025-03-10", "Check-Out Date": "2025-03-30",
     "Engine Hours/Day": 0, "Idle Hours/Day": 11, "Rental Days": 20,
     "Last Operator ID": "NULL"},
    {"Equipment ID": "EQX1003", "Type": "Bulldozer", "Site ID": "S002",
     "Check-In Date": "2025-02-15", "Check-Out Date": "2025-03-11",
     "Engine Hours/Day": 7.5, "Idle Hours/Day": 0.5, "Rental Days": 25,
     "Last Operator ID": "OP203"},
    {"Equipment ID": "EQX1004", "Type": "Excavator", "Site ID": "S004",
     "Check-In Date": "2025-05-05", "Check-Out Date": "2025-05-15",
     "Engine Hours/Day": 2, "Idle Hours/Day": 9, "Rental Days": 10,
     "Last Operator ID": "OP106"},
    {"Equipment ID": "EQX1005", "Type": "Bulldozer", "Site ID": "S006",
     "Check-In Date": "2025-01-01", "Check-Out Date": "2025-01-31",
     "Engine Hours/Day": 8, "Idle Hours/Day": 0, "Rental Days": 30,
     "Last Operator ID": "OP301"},
    {"Equipment ID": "EQX1006", "Type": "Grader", "Site ID": "S001",
     "Check-In Date": "2025-04-05", "Check-Out Date": "2025-04-23",
     "Engine Hours/Day": 3, "Idle Hours/Day": 6, "Rental Days": 18,
     "Last Operator ID": "OP114"},
    {"Equipment ID": "EQX1007", "Type": "Excavator", "Site ID": "NULL",
     "Check-In Date": "2025-03-20", "Check-Out Date": "2025-04-01",
     "Engine Hours/Day": 0, "Idle Hours/Day": 12, "Rental Days": 12,
     "Last Operator ID": "NULL"},
]


def main():
    synthetic_rows = build_rows()
    all_rows = ORIGINAL_ROWS + synthetic_rows

    fieldnames = [
        "Equipment ID", "Type", "Site ID", "Check-In Date", "Check-Out Date",
        "Engine Hours/Day", "Idle Hours/Day", "Rental Days", "Last Operator ID",
    ]

    output_path = "synthetic_rental_data.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Generated {len(all_rows)} total rows ({len(ORIGINAL_ROWS)} original + "
          f"{len(synthetic_rows)} synthetic) -> {output_path}")


if __name__ == "__main__":
    main()