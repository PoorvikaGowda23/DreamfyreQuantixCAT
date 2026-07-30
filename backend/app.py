"""
backend/app.py -- Main Flask entry point.

Serves:
  GET  /                          -> the Asset Dashboard page (Module 6)
  GET  /api/equipment             -> live status of every asset (Module 1)
  GET  /api/equipment/<id>        -> single asset detail + history + usage
  GET  /api/alerts                -> overdue / due-soon report (Module 3)
  GET  /api/anomalies             -> flagged anomalies report (Module 4)
  GET  /api/fleet-summary         -> fleet-wide usage totals (Module 2)
  GET  /api/site-summary          -> usage grouped by site (Module 2)
  POST /api/simulate-day          -> advance one simulated day of usage
  POST /api/checkin               -> check equipment into a site
  POST /api/checkout               -> check equipment out of a site

Run with:
    python app.py
then open http://127.0.0.1:5000
"""

import sys
import os
import threading
import webbrowser

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules"))

from flask import Flask, render_template, jsonify, request

from database import init_db
from checkin_checkout import (
    check_in, check_out, get_all_equipment, get_equipment_status, get_history,
)
from usage_logging import (
    simulate_day, get_usage_summary, get_usage_history,
    get_fleet_summary, get_summary_by_site, get_idle_downtime_report,
)
from overdue_alerts import get_overdue_report, get_alert_summary
from anomaly_detection import get_anomaly_report, get_anomaly_summary, get_equipment_anomalies

# ---------------------------------------------------------------------
# Locate templates/ and static/.
#
# This project keeps the frontend as a sibling of backend/ instead of
# inside it:
#     project_root/
#       backend/app.py   <- this file
#       frontend/templates/dashboard.html
#       frontend/static/css/... , frontend/static/js/...
#
# Flask's default loader only checks for templates/ and static/ next to
# app.py, so it won't find frontend/ on its own -- these paths are built
# from app.py's own location (not the current working directory), so
# this works no matter where you run `python app.py` from or what the
# project folder is named.
# ---------------------------------------------------------------------
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
TEMPLATE_DIR = os.path.join(FRONTEND_DIR, "templates")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

if not os.path.isdir(TEMPLATE_DIR):
    print(f"[app.py] WARNING: template folder not found at {TEMPLATE_DIR}")
if not os.path.isdir(STATIC_DIR):
    print(f"[app.py] WARNING: static folder not found at {STATIC_DIR}")


# ---------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


# ---------------------------------------------------------------------
# Equipment / live status (Module 1)
# ---------------------------------------------------------------------

@app.route("/api/equipment")
def api_equipment():
    """Live status table -- joined with alert + anomaly flags per asset
    so the dashboard can render everything from one call."""
    equipment = get_all_equipment()

    alerts_by_id = {a["equipment_id"]: a for a in get_overdue_report()}
    anomalies_by_id = {a["equipment_id"]: a for a in get_anomaly_report()}

    for eq in equipment:
        alert = alerts_by_id.get(eq["equipment_id"])
        eq["alert_status"] = alert["alert_status"] if alert else None
        eq["days_overdue"] = alert["days_overdue"] if alert else 0
        eq["days_until_due"] = alert["days_until_due"] if alert else 0

        anomaly = anomalies_by_id.get(eq["equipment_id"])
        eq["anomalies"] = anomaly["anomalies"] if anomaly else []
        eq["anomaly_count"] = anomaly["anomaly_count"] if anomaly else 0

    return jsonify(equipment)


@app.route("/api/equipment/<equipment_id>")
def api_equipment_detail(equipment_id):
    status = get_equipment_status(equipment_id)
    if "error" in status:
        return jsonify(status), 404

    return jsonify({
        "equipment": status,
        "checkinout_history": get_history(equipment_id),
        "usage_history": get_usage_history(equipment_id),
        "usage_summary": get_usage_summary(equipment_id),
        "anomalies": get_equipment_anomalies(equipment_id),
    })


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    body = request.get_json(force=True) or {}
    required = ["equipment_id", "site_id", "operator_id", "planned_rental_days"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    result = check_in(
        equipment_id=body["equipment_id"],
        site_id=body["site_id"],
        operator_id=body["operator_id"],
        planned_rental_days=int(body["planned_rental_days"]),
        equipment_type=body.get("equipment_type"),
        event_date=body.get("event_date"),
    )
    status_code = 400 if "error" in result else 200
    return jsonify(result), status_code


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    body = request.get_json(force=True) or {}
    if not body.get("equipment_id"):
        return jsonify({"error": "Missing field: equipment_id"}), 400

    result = check_out(
        equipment_id=body["equipment_id"],
        event_date=body.get("event_date"),
    )
    status_code = 400 if "error" in result else 200
    return jsonify(result), status_code


# ---------------------------------------------------------------------
# Usage / simulation (Module 2)
# ---------------------------------------------------------------------

@app.route("/api/simulate-day", methods=["POST"])
def api_simulate_day():
    new_logs = simulate_day()
    return jsonify({"new_logs": new_logs, "count": len(new_logs)})


@app.route("/api/fleet-summary")
def api_fleet_summary():
    return jsonify(get_fleet_summary())


@app.route("/api/site-summary")
def api_site_summary():
    return jsonify(get_summary_by_site())


@app.route("/api/idle-downtime")
def api_idle_downtime():
    return jsonify(get_idle_downtime_report())


# ---------------------------------------------------------------------
# Alerts (Module 3)
# ---------------------------------------------------------------------

@app.route("/api/alerts")
def api_alerts():
    return jsonify({
        "report": get_overdue_report(),
        "summary": get_alert_summary(),
    })


# ---------------------------------------------------------------------
# Anomalies (Module 4)
# ---------------------------------------------------------------------

@app.route("/api/anomalies")
def api_anomalies():
    return jsonify({
        "report": get_anomaly_report(),
        "summary": get_anomaly_summary(),
    })


def _open_browser():
    """Launch the dashboard in the OS's default browser.

    Flask's debug reloader actually starts this file twice under the
    hood (once as a watcher process, once as the real server) -- without
    the WERKZEUG_RUN_MAIN check below, that would pop open two browser
    tabs every time you save a file. WERKZEUG_RUN_MAIN is only set on
    the real run, so this only fires once.
    """
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            webbrowser.open_new("http://127.0.0.1:5000")
        except webbrowser.Error:
            print("[app.py] Couldn't auto-open a browser -- "
                  "open http://127.0.0.1:5000 manually.")


if __name__ == "__main__":
    init_db()
    # Open the dashboard in your actual OS browser (Chrome/Edge/etc.)
    # half a second after the server starts, instead of needing to
    # click a link that VS Code might intercept into Simple Browser.
    threading.Timer(0.5, _open_browser).start()
    app.run(debug=True, port=5000)