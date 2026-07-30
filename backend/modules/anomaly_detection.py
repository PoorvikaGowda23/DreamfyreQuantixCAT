"""
Module 4: Anomaly Detection (Hybrid: ML model, with rule-based fallback)

Design:
--------
Two different KINDS of anomaly live in this data, and they're handled
differently on purpose:

1. BUSINESS / DATA-INTEGRITY RULES (always rule-based, never ML)
   e.g. "Site ID is NULL while status = Active", "no operator logged".
   These are compliance checks, not statistical outliers -- a missing
   operator ID is wrong 100% of the time, regardless of what "normal"
   looks like in the data. A model has nothing to add here, so these
   always run as fixed rules:
       - UNASSIGNED_SITE
       - NO_OPERATOR
       - ZERO_ENGINE_ACTIVITY (while Active)

2. NUMERIC / BEHAVIORAL OUTLIERS (ML-first, rule-based fallback)
   e.g. "is this idle/engine/rental pattern unusual for this equipment
   Type?". This is exactly what unsupervised ML (IsolationForest) is
   built for -- it can catch combinations of features that a single
   fixed threshold would miss (e.g. moderately-high idle + moderately-
   long rental together, neither alone crossing a hardcoded line).

   The model is trained PER EQUIPMENT TYPE (an Excavator's "normal" idle
   pattern differs from a Crane's), using only the existing fields:
   engine_hours_day, idle_hours_day, idle_ratio, rental_days (or
   planned_rental_days for still-active equipment).

   FALLBACK: the ML path is only used when it can be trusted. It
   automatically falls back to the same fixed-threshold rules from the
   original spec (IDLE_EXCEEDS_ACTIVE, EXCESSIVE_IDLE_RATIO,
   UNUSUALLY_LONG_RENTAL) whenever:
       - scikit-learn isn't installed, OR
       - there isn't enough data for that Type yet to train on
         (< MIN_SAMPLES_FOR_MODEL rows), OR
       - model fitting throws any exception (safety net -- an anomaly
         detector should never crash the dashboard).

Every flagged anomaly reports which method produced it
(`detection_method`: "model_pretrained" / "model_live" / "rule_fallback" /
"rule_business") so the dashboard (or you, debugging) can always tell
where a given flag came from.

MODEL SOURCE -- three tiers, in priority order:
  1. model_pretrained -- a model trained offline on synthetic_rental_data.csv
     via train_anomaly_model.py and saved to models/anomaly_models.joblib.
     This is the PRIMARY path: the synthetic dataset has 28-57 rows per
     Type, plenty to learn a real "normal" pattern from.
  2. model_live -- if no pretrained model exists for a Type, but the live
     `equipment` table happens to have enough rows (>= MIN_SAMPLES_FOR_MODEL)
     for that Type, train one on the fly instead.
  3. rule_fallback / rule_business -- if neither model path is available
     (missing dependency, no pretrained file, not enough live data, or
     the model throws an exception), fall back to fixed thresholds.
"""

import sys
import os
import statistics

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection

try:
    from sklearn.ensemble import IsolationForest
    import joblib
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

MODELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "models", "anomaly_models.joblib")

_pretrained_models_cache = None  # lazy-loaded, see _load_pretrained_models()

# --- Tunable thresholds (used by the RULE-BASED fallback only) --------
EXCESSIVE_IDLE_RATIO_THRESHOLD = 0.7   # Idle Hours/Day / 24 > this -> flag
LONG_RENTAL_MULTIPLIER = 1.5           # rental_days > (type avg * this) -> flag

# --- Model settings -----------------------------------------------------
MIN_SAMPLES_FOR_MODEL = 8              # below this, fall back to rules for that Type
MODEL_CONTAMINATION = 0.12             # expected outlier fraction (~matches synthetic data's ~10-12% anomaly rate)
MODEL_RANDOM_STATE = 42                # reproducible results across runs

# --- Anomaly codes ------------------------------------------------------
UNASSIGNED_SITE = "Unassigned Site"
NO_OPERATOR = "No Operator Logged"
ZERO_ENGINE_ACTIVITY = "Zero Engine Activity"
IDLE_EXCEEDS_ACTIVE = "Idle Exceeds Active Use"
EXCESSIVE_IDLE_RATIO = "Excessive Idle Ratio"
UNUSUALLY_LONG_RENTAL = "Unusually Long Rental"
MODEL_BEHAVIORAL_OUTLIER = "Behavioral Outlier (Model)"

METHOD_MODEL_PRETRAINED = "model_pretrained"
METHOD_MODEL_LIVE = "model_live"
METHOD_RULE_FALLBACK = "rule_fallback"
METHOD_RULE_BUSINESS = "rule_business"


def _load_pretrained_models():
    """
    Lazily loads models/anomaly_models.joblib (produced by
    train_anomaly_model.py) once per process and caches it.

    Returns:
        dict: {equipment_type: {"model": IsolationForest, ...}}, or {}
        if the file doesn't exist / can't be loaded (safe no-op --
        callers just fall through to the next tier).
    """
    global _pretrained_models_cache
    if _pretrained_models_cache is not None:
        return _pretrained_models_cache

    if not SKLEARN_AVAILABLE or not os.path.exists(MODELS_PATH):
        _pretrained_models_cache = {}
        return _pretrained_models_cache

    try:
        _pretrained_models_cache = joblib.load(MODELS_PATH)
    except Exception:
        _pretrained_models_cache = {}
    return _pretrained_models_cache


def _is_null(value):
    """Treat both real SQL NULL and the literal string 'NULL' (used by
    the synthetic/original dataset) as unassigned."""
    return value is None or str(value).strip().upper() == "NULL"


def _rental_value(row):
    """Actual rental_days if the rental completed, else the planned
    duration for equipment still checked out."""
    return row["rental_days"] if row["rental_days"] else row["planned_rental_days"]


# -------------------------------------------------------------------
# 1. Business rules -- ALWAYS rule-based, never delegated to the model
# -------------------------------------------------------------------

def _check_business_rules(row):
    """Data-integrity checks that must always be deterministic."""
    flags = []
    is_active = row["status"] == "Active"

    if is_active and _is_null(row["site_id"]):
        flags.append(UNASSIGNED_SITE)

    if is_active and _is_null(row["last_operator_id"]):
        flags.append(NO_OPERATOR)

    if is_active and (row["engine_hours_day"] or 0) == 0:
        flags.append(ZERO_ENGINE_ACTIVITY)

    return [(f, METHOD_RULE_BUSINESS) for f in flags]


# -------------------------------------------------------------------
# 2a. Rule-based fallback for numeric/behavioral checks
# -------------------------------------------------------------------

def _rental_days_averages_by_type():
    conn = get_connection()
    rows = conn.execute("""
        SELECT type, AVG(rental_days) AS avg_days
        FROM equipment
        WHERE rental_days IS NOT NULL AND rental_days > 0
        GROUP BY type
    """).fetchall()
    conn.close()
    return {row["type"]: row["avg_days"] for row in rows}


def _check_numeric_rules(row, rental_avg_by_type):
    """Fixed-threshold checks -- used as the FALLBACK when the model
    can't be trusted (not enough data / sklearn missing / fit failed)."""
    flags = []
    engine = row["engine_hours_day"] or 0
    idle = row["idle_hours_day"] or 0

    if idle > engine:
        flags.append(IDLE_EXCEEDS_ACTIVE)

    if (idle / 24) > EXCESSIVE_IDLE_RATIO_THRESHOLD:
        flags.append(EXCESSIVE_IDLE_RATIO)

    rental_value = _rental_value(row)
    type_avg = rental_avg_by_type.get(row["type"])
    if rental_value and type_avg and rental_value > type_avg * LONG_RENTAL_MULTIPLIER:
        flags.append(UNUSUALLY_LONG_RENTAL)

    return [(f, METHOD_RULE_FALLBACK) for f in flags]


# -------------------------------------------------------------------
# 2b. Model-based detection (primary path when data allows)
# -------------------------------------------------------------------

def _build_features(row):
    """Numeric feature vector fed to the model: engine hours, idle
    hours, idle ratio, and rental duration (actual or planned)."""
    engine = row["engine_hours_day"] or 0
    idle = row["idle_hours_day"] or 0
    idle_ratio = idle / (idle + engine) if (idle + engine) > 0 else 0
    rental_value = _rental_value(row) or 0
    return [engine, idle, idle_ratio, rental_value]


def _detect_with_model(rows_for_type, eq_type, pretrained_models):
    """
    Scores this Type's rows for behavioral outliers, preferring a
    PRE-TRAINED model (trained offline on the synthetic dataset) over
    training one on the fly from live data.

    Returns:
        (flagged_dict, method) where flagged_dict is
        {equipment_id: anomaly_score} for rows flagged as outliers, and
        method is METHOD_MODEL_PRETRAINED or METHOD_MODEL_LIVE.
        Returns (None, None) if no model path is usable, signaling the
        caller to use the rule fallback instead.
    """
    if not SKLEARN_AVAILABLE:
        return None, None

    # Tier 1: pretrained model from train_anomaly_model.py, if available
    # for this Type. Only SCORES live rows -- never retrains here, so
    # results stay consistent with what was validated at training time.
    if eq_type in pretrained_models:
        try:
            model = pretrained_models[eq_type]["model"]
            X = [_build_features(row) for row in rows_for_type]
            predictions = model.predict(X)
            scores = model.decision_function(X)
            flagged = {
                row["equipment_id"]: round(float(score), 4)
                for row, pred, score in zip(rows_for_type, predictions, scores)
                if pred == -1
            }
            return flagged, METHOD_MODEL_PRETRAINED
        except Exception:
            pass  # fall through to tier 2

    # Tier 2: train on the fly from live data, if there's enough of it.
    if len(rows_for_type) < MIN_SAMPLES_FOR_MODEL:
        return None, None

    try:
        X = [_build_features(row) for row in rows_for_type]
        model = IsolationForest(
            contamination=MODEL_CONTAMINATION,
            random_state=MODEL_RANDOM_STATE,
        )
        predictions = model.fit_predict(X)          # -1 = outlier, 1 = normal
        scores = model.decision_function(X)          # lower = more anomalous

        flagged = {
            row["equipment_id"]: round(float(score), 4)
            for row, pred, score in zip(rows_for_type, predictions, scores)
            if pred == -1
        }
        return flagged, METHOD_MODEL_LIVE
    except Exception:
        # Safety net: never let a model failure break the dashboard --
        # signal the caller to fall back to fixed rules instead.
        return None, None


# -------------------------------------------------------------------
# Main report
# -------------------------------------------------------------------

def get_anomaly_report():
    """
    Main function behind the dashboard's red anomaly badges.

    For each equipment Type:
      - Always applies the business rules (unassigned site / no
        operator / zero engine activity).
      - Attempts model-based detection for numeric/behavioral outliers;
        if the model can't run (too little data / sklearn missing /
        fit error), transparently falls back to the fixed-threshold
        rules instead.

    Returns:
        list[dict]: [{equipment_id, type, status, anomalies: [...],
                       anomaly_count, detection_methods_used: [...]},
                      ...] sorted by anomaly_count desc.
    """
    conn = get_connection()
    all_rows = conn.execute("SELECT * FROM equipment").fetchall()
    conn.close()

    rental_avg_by_type = _rental_days_averages_by_type()
    pretrained_models = _load_pretrained_models()

    # Group rows by Type so the model can be trained/scored per-Type
    rows_by_type = {}
    for row in all_rows:
        rows_by_type.setdefault(row["type"], []).append(row)

    results = {}  # equipment_id -> {anomalies: [...], methods: set()}

    for eq_type, rows_for_type in rows_by_type.items():
        model_flags, method_used = _detect_with_model(rows_for_type, eq_type, pretrained_models)
        using_model = model_flags is not None

        for row in rows_for_type:
            eq_id = row["equipment_id"]
            results.setdefault(eq_id, {"anomalies": [], "methods": set(),
                                        "type": row["type"], "status": row["status"]})

            # 1) Business rules -- always on
            for flag, method in _check_business_rules(row):
                results[eq_id]["anomalies"].append(flag)
                results[eq_id]["methods"].add(method)

            # 2) Numeric/behavioral -- model if possible, else fallback
            if using_model:
                if eq_id in model_flags:
                    results[eq_id]["anomalies"].append(MODEL_BEHAVIORAL_OUTLIER)
                    results[eq_id]["methods"].add(method_used)
            else:
                for flag, method in _check_numeric_rules(row, rental_avg_by_type):
                    results[eq_id]["anomalies"].append(flag)
                    results[eq_id]["methods"].add(method)

    report = [
        {
            "equipment_id": eq_id,
            "type": data["type"],
            "status": data["status"],
            "anomalies": data["anomalies"],
            "anomaly_count": len(data["anomalies"]),
            "detection_methods_used": sorted(data["methods"]),
        }
        for eq_id, data in results.items()
        if data["anomalies"]
    ]
    report.sort(key=lambda r: r["anomaly_count"], reverse=True)
    return report


def get_equipment_anomalies(equipment_id):
    """Single-equipment lookup -- used for a per-machine red badge on
    the equipment detail/drill-down view."""
    report = get_anomaly_report()
    for row in report:
        if row["equipment_id"] == equipment_id:
            row["has_anomaly"] = True
            return row
    return {"equipment_id": equipment_id, "anomalies": [], "anomaly_count": 0,
            "detection_methods_used": [], "has_anomaly": False}


def get_anomaly_summary():
    """Top-line count for the dashboard header, e.g. '5 assets flagged'.
    Also reports which detection tier ran per Type (model_pretrained /
    model_live / rule_fallback), so you can see at a glance whether a
    Type is being covered by the trained model yet."""
    report = get_anomaly_report()
    pretrained_models = _load_pretrained_models()

    conn = get_connection()
    rows = conn.execute("SELECT type, COUNT(*) as n FROM equipment GROUP BY type").fetchall()
    conn.close()

    method_by_type = {}
    for row in rows:
        if row["type"] in pretrained_models:
            method_by_type[row["type"]] = METHOD_MODEL_PRETRAINED
        elif SKLEARN_AVAILABLE and row["n"] >= MIN_SAMPLES_FOR_MODEL:
            method_by_type[row["type"]] = METHOD_MODEL_LIVE
        else:
            method_by_type[row["type"]] = METHOD_RULE_FALLBACK

    return {
        "flagged_equipment_count": len(report),
        "total_anomaly_count": sum(r["anomaly_count"] for r in report),
        "sklearn_available": SKLEARN_AVAILABLE,
        "pretrained_models_loaded": sorted(pretrained_models.keys()),
        "detection_method_by_type": method_by_type,
    }


if __name__ == "__main__":
    from database import init_db
    from checkin_checkout import check_in
    import random

    init_db()
    random.seed(1)

    # NOTE: this demo block inserts a few clearly-marked test rows
    # (EQXC### / EQXD001) into whatever database.py points at. If you've
    # run this before, those rows already exist -- clean them up first so
    # re-running this file is always safe/idempotent.
    conn = get_connection()
    conn.execute("DELETE FROM checkinout_log WHERE equipment_id LIKE 'EQXC%' OR equipment_id LIKE 'EQXD%'")
    conn.execute("DELETE FROM usage_log WHERE equipment_id LIKE 'EQXC%' OR equipment_id LIKE 'EQXD%'")
    conn.execute("DELETE FROM equipment WHERE equipment_id LIKE 'EQXC%' OR equipment_id LIKE 'EQXD%'")
    conn.commit()
    conn.close()

    # Build up enough Excavator history to make the model kick in
    # (needs >= MIN_SAMPLES_FOR_MODEL rows for that Type).
    conn = get_connection()
    for i in range(12):
        eng = round(random.uniform(3, 7), 1)
        idl = round(random.uniform(0, 3), 1)
        conn.execute("""
            INSERT INTO equipment (equipment_id, type, site_id, check_in_date,
                check_out_date, planned_rental_days, expected_due_date,
                engine_hours_day, idle_hours_day, rental_days,
                last_operator_id, status)
            VALUES (?, 'Excavator', 'S001', '2026-01-01', '2026-01-15',
                    14, '2026-01-15', ?, ?, 14, 'OP100', 'Available')
        """, (f"EQXC{i:03d}", eng, idl))
    # One genuine outlier: near-zero engine, very high idle
    conn.execute("""
        UPDATE equipment SET engine_hours_day = 0.1, idle_hours_day = 9.5
        WHERE equipment_id = 'EQXC000'
    """)
    conn.commit()
    conn.close()

    # A second Type (Crane) deliberately left with too little data ->
    # should automatically fall back to fixed rules.
    check_in("EQXD001", "S002", "OP200", planned_rental_days=10,
              equipment_type="Crane", event_date="2026-07-01")
    conn = get_connection()
    conn.execute("UPDATE equipment SET engine_hours_day = 2, idle_hours_day = 20 WHERE equipment_id = 'EQXD001'")
    conn.commit()
    conn.close()

    print("--- Full anomaly report ---")
    for row in get_anomaly_report():
        print(row["equipment_id"], row["type"], row["anomalies"], row["detection_methods_used"])

    print("--- Dashboard summary ---")
    print(get_anomaly_summary())

'''
    print("\n(Demo rows EQXC### / EQXD001 were written into your database. "
          "Re-run this file anytime -- it cleans up its own rows first. "
          "To remove them without re-running, delete WHERE equipment_id "
          "LIKE 'EQXC%' OR equipment_id LIKE 'EQXD%'.)")
'''          