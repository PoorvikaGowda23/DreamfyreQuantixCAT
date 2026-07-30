"""
Trains and persists per-Equipment-Type IsolationForest anomaly models
using synthetic_rental_data.csv (or any CSV with the same 9 original
fields: Equipment ID, Type, Site ID, Check-In Date, Check-Out Date,
Engine Hours/Day, Idle Hours/Day, Rental Days, Last Operator ID).

Run this once (and again whenever you regenerate synthetic data, or
later swap in real historical data) to (re)build the saved models that
anomaly_detection.py automatically picks up and uses as its PRIMARY
detection method:

    python train_anomaly_model.py                        # uses ./synthetic_rental_data.csv
    python train_anomaly_model.py path/to/other_data.csv  # or a custom path

Saves one file: models/anomaly_models.joblib
    { equipment_type: { "model": IsolationForest,
                         "n_samples": int,
                         "trained_at": iso_timestamp } }

Why train on the CSV instead of the live `equipment` table:
the live table only ever holds a handful of rows per Type at any given
moment (whatever's currently checked in/out) -- nowhere near enough for
an IsolationForest to learn a reliable "normal" pattern. The synthetic
dataset has 28-57 rows per Type, which is enough to train on.

anomaly_detection.py loads this file if present. If a Type isn't in it
(or the file doesn't exist), it transparently falls back to training
on-the-fly from live data, and finally to fixed rule thresholds -- see
anomaly_detection.py's module docstring for the full fallback chain.
"""

import sys
import os
from datetime import datetime
import csv

try:
    from sklearn.ensemble import IsolationForest
    import joblib
except ImportError:
    print("scikit-learn (and joblib, installed alongside it) are required.")
    print("Install with:  pip install scikit-learn")
    sys.exit(1)

MIN_SAMPLES_FOR_MODEL = 8       # must match anomaly_detection.py
MODEL_CONTAMINATION = 0.12      # must match anomaly_detection.py
MODEL_RANDOM_STATE = 42         # must match anomaly_detection.py

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODELS_PATH = os.path.join(MODELS_DIR, "anomaly_models.joblib")

# This file lives in backend/modules/. The dataset lives in data/, two
# levels up: backend/modules/ -> backend/ -> project root -> data/.
DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "data", "synthetic_rental_data.csv"
)


def _to_float(value, default=0.0):
    """Parses a CSV cell as float; treats blank/NULL as `default`."""
    if value is None:
        return default
    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _build_features(engine, idle, rental_days):
    """Same feature engineering used at inference time in
    anomaly_detection.py -- keep these in sync."""
    idle_ratio = idle / (idle + engine) if (idle + engine) > 0 else 0
    return [engine, idle, idle_ratio, rental_days]


def load_training_rows(csv_path):
    """Reads the CSV and groups feature vectors + equipment IDs by Type."""
    rows_by_type = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            eq_type = row["Type"]
            engine = _to_float(row.get("Engine Hours/Day"))
            idle = _to_float(row.get("Idle Hours/Day"))
            rental_days = _to_float(row.get("Rental Days"))
            features = _build_features(engine, idle, rental_days)
            rows_by_type.setdefault(eq_type, []).append({
                "equipment_id": row["Equipment ID"],
                "features": features,
            })
    return rows_by_type


def train_models(csv_path=None):
    csv_path = csv_path or DEFAULT_CSV
    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        sys.exit(1)

    rows_by_type = load_training_rows(csv_path)

    os.makedirs(MODELS_DIR, exist_ok=True)
    saved_models = {}
    summary = []

    for eq_type, rows in sorted(rows_by_type.items()):
        if len(rows) < MIN_SAMPLES_FOR_MODEL:
            summary.append((eq_type, len(rows), "SKIPPED (fewer than "
                             f"{MIN_SAMPLES_FOR_MODEL} samples -- will use "
                             "rule-based fallback for this Type)"))
            continue

        X = [r["features"] for r in rows]
        model = IsolationForest(
            contamination=MODEL_CONTAMINATION,
            random_state=MODEL_RANDOM_STATE,
        )
        model.fit(X)

        predictions = model.predict(X)
        n_flagged = int((predictions == -1).sum())
        flagged_ids = [rows[i]["equipment_id"] for i, p in enumerate(predictions) if p == -1]

        saved_models[eq_type] = {
            "model": model,
            "n_samples": len(rows),
            "trained_at": datetime.now().isoformat(timespec="seconds"),
        }
        summary.append((eq_type, len(rows),
                         f"trained -- {n_flagged} flagged in training set "
                         f"({', '.join(flagged_ids[:5])}{'...' if n_flagged > 5 else ''})"))

    joblib.dump(saved_models, MODELS_PATH)

    print(f"Training data : {csv_path}")
    print(f"Saved models  : {MODELS_PATH}\n")
    print(f"{'Type':<12}{'Samples':<10}Result")
    print("-" * 70)
    for eq_type, n, result in summary:
        print(f"{eq_type:<12}{n:<10}{result}")

    return saved_models


if __name__ == "__main__":
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    train_models(csv_arg)