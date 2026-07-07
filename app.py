import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLES = {
    "employees": "employees",
    "departments": "departments",
    "positions": "positions",
    "attendance": "attendance",
    "leaves": "leaves",
    "payroll": "payroll",
}

ENRICHED_KEYS = {
    "department_name",
    "position_title",
    "employee_name",
    "assigned_employees",
    "full_name",
    "departments",
    "positions",
    "employees",
}


def require_db() -> Client:
    if supabase is None:
        raise RuntimeError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY.")
    return supabase


def table_rows(table_name: str) -> list[dict]:
    db = require_db()
    response = db.table(table_name).select("*").order("id").execute()
    return response.data or []


def table_row(table_name: str, item_id: int) -> dict | None:
    db = require_db()
    response = db.table(table_name).select("*").eq("id", item_id).limit(1).execute()
    rows = response.data or []
    return rows[0] if rows else None


def clean_payload(payload: dict) -> dict:
    cleaned = {}
    for key, value in (payload or {}).items():
        if key in ENRICHED_KEYS:
            continue
        if key in {"id", "created_at", "updated_at"}:
            continue
        cleaned[key] = value
    return cleaned


def employee_full_name(employee: dict) -> str:
    return f"{employee.get('first_name') or ''} {employee.get('last_name') or ''}".strip() or "Unnamed"


def enrich_rows(resource: str, rows: list[dict] | dict) -> list[dict] | dict:
    is_single = isinstance(rows, dict)
    data = [rows] if is_single else list(rows)

    departments = {d["id"]: d for d in table_rows("departments")}
    positions = {p["id"]: p for p in table_rows("positions")}
    employees = {e["id"]: e for e in table_rows("employees")}

    for item in data:
        if resource == "employees":
            dept = departments.get(item.get("department_id"))
            pos = positions.get(item.get("position_id"))
            item["department_name"] = dept.get("name") if dept else None
            item["position_title"] = pos.get("title") if pos else None
            item["full_name"] = employee_full_name(item)

        elif resource == "positions":
            dept = departments.get(item.get("department_id"))
            item["department_name"] = dept.get("name") if dept else None
            assigned = []
            for employee in employees.values():
                if employee.get("position_id") == item.get("id"):
                    assigned.append(
                        {
                            **employee,
                            "full_name": employee_full_name(employee),
                            "department_name": departments.get(employee.get("department_id"), {}).get("name"),
                            "position_title": item.get("title"),
                        }
                    )
            item["assigned_employees"] = assigned

        elif resource in {"attendance", "leaves", "payroll"}:
            emp = employees.get(item.get("employee_id"))
            item["employee_name"] = employee_full_name(emp) if emp else None

    return data[0] if is_single and data else data


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "JavaGoat HR"})


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    password = payload.get("password")

    if email == "admin@javagoat.hr" and password == "password123":
        return jsonify(
            {
                "token": "mock-javagoat-hr-admin-token",
                "user": {"email": email, "role": "admin", "name": "HR Admin"},
            }
        )

    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/api/<resource>", methods=["GET", "POST"])
def collection(resource: str):
    if resource not in TABLES:
        return jsonify({"error": "Unknown resource"}), 404

    table_name = TABLES[resource]

    try:
        db = require_db()

        if request.method == "GET":
            rows = table_rows(table_name)
            if resource in {"employees", "positions", "attendance", "leaves", "payroll"}:
                rows = enrich_rows(resource, rows)
            return jsonify(rows)

        payload = clean_payload(request.get_json(silent=True) or {})
        response = db.table(table_name).insert(payload).execute()
        created = (response.data or [{}])[0]

        if resource in {"employees", "positions", "attendance", "leaves", "payroll"}:
            created = enrich_rows(resource, created)

        return jsonify(created), 201

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/<resource>/<int:item_id>", methods=["GET", "PUT", "DELETE"])
def item(resource: str, item_id: int):
    if resource not in TABLES:
        return jsonify({"error": "Unknown resource"}), 404

    table_name = TABLES[resource]

    try:
        db = require_db()

        if request.method == "GET":
            row = table_row(table_name, item_id)
            if not row:
                return jsonify({"error": "Record not found"}), 404

            if resource in {"employees", "positions", "attendance", "leaves", "payroll"}:
                row = enrich_rows(resource, row)

            return jsonify(row)

        if request.method == "PUT":
            payload = clean_payload(request.get_json(silent=True) or {})

            if not payload:
                row = table_row(table_name, item_id)
                if not row:
                    return jsonify({"error": "Record not found"}), 404
                return jsonify(row)

            response = db.table(table_name).update(payload).eq("id", item_id).execute()
            rows = response.data or []

            if not rows:
                return jsonify({"error": "Record not found"}), 404

            updated = rows[0]
            if resource in {"employees", "positions", "attendance", "leaves", "payroll"}:
                updated = enrich_rows(resource, updated)

            return jsonify(updated)

        response = db.table(table_name).delete().eq("id", item_id).execute()
        deleted = response.data or []

        if not deleted:
            return jsonify({"error": "Record not found"}), 404

        return jsonify({"deleted": True, "id": item_id})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/dashboard/stats")
def dashboard_stats():
    try:
        departments = table_rows("departments")
        positions = table_rows("positions")
        employees = table_rows("employees")
        attendance = table_rows("attendance")
        leaves = table_rows("leaves")
        payroll = table_rows("payroll")

        department_map = {d["id"]: d for d in departments}
        position_map = {p["id"]: p for p in positions}

        active_count = sum(1 for e in employees if e.get("status") == "Active")
        pending_leaves = sum(1 for l in leaves if l.get("status") == "Pending")

        today = date.today()
        current_period = today.strftime("%Y-%m")
        monthly_payroll = sum(
            float(p.get("net_pay") or 0)
            for p in payroll
            if str(p.get("period") or "").startswith(current_period)
        )

        hiring_counter = Counter()
        for employee in employees:
            hire_date = employee.get("hire_date")
            if hire_date:
                try:
                    hiring_counter[datetime.fromisoformat(str(hire_date)).strftime("%b %Y")] += 1
                except ValueError:
                    pass

        sorted_hiring = sorted(
            hiring_counter.items(),
            key=lambda x: datetime.strptime(x[0], "%b %Y"),
        )

        department_counter = Counter()
        for employee in employees:
            dept = department_map.get(employee.get("department_id"))
            department_counter[dept.get("name") if dept else "Unassigned"] += 1

        status_counter = Counter(employee.get("status") or "Unknown" for employee in employees)

        start = today - timedelta(days=13)
        attendance_counter = defaultdict(int)
        for i in range(14):
            attendance_counter[(start + timedelta(days=i)).isoformat()] = 0

        for record in attendance:
            record_date = record.get("date")
            if not record_date:
                continue
            try:
                parsed = datetime.fromisoformat(str(record_date)).date()
            except ValueError:
                continue
            if start <= parsed <= today and record.get("status") in {"Present", "Late", "Remote"}:
                attendance_counter[parsed.isoformat()] += 1

        employees_by_position = []
        for employee in employees:
            dept = department_map.get(employee.get("department_id"))
            pos = position_map.get(employee.get("position_id"))
            employees_by_position.append(
                {
                    "id": employee.get("id"),
                    "name": employee_full_name(employee),
                    "department": dept.get("name") if dept else "Unassigned",
                    "position": pos.get("title") if pos else "Unassigned",
                    "profile_pic": employee.get("profile_pic"),
                }
            )

        return jsonify(
            {
                "cards": {
                    "employees": len(employees),
                    "active": active_count,
                    "departments": len(departments),
                    "pending_leaves": pending_leaves,
                    "monthly_payroll": monthly_payroll,
                },
                "hiring": {
                    "labels": [item[0] for item in sorted_hiring],
                    "data": [item[1] for item in sorted_hiring],
                },
                "department_mix": {
                    "labels": list(department_counter.keys()),
                    "data": list(department_counter.values()),
                },
                "employees_by_position": employees_by_position,
                "attendance": {
                    "labels": [datetime.fromisoformat(k).strftime("%b %d") for k in attendance_counter.keys()],
                    "data": list(attendance_counter.values()),
                },
                "status": {
                    "labels": list(status_counter.keys()),
                    "data": list(status_counter.values()),
                },
            }
        )

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(exc):
    return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
