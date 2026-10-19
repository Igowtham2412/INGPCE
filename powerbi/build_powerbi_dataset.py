"""build_powerbi_dataset.py - PostgreSQL -> CSV extractor for Power BI."""
import argparse, csv, os
from collections import defaultdict
import psycopg2
import psycopg2.extras

def export_table(conn, query, out_path):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out_path}")
    return rows

def export_daily_summary(rows, out_dir):
    agg = defaultdict(int)
    for r in rows:
        d = r["detected_at"]
        date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        key = (date_str, r["camera_name"], r.get("area_group") or r.get("zone"), r["violation_type"])
        agg[key] += 1
    path = os.path.join(out_dir, "daily_summary.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "camera_name", "area_group", "violation_type", "violation_count"])
        for (date_str, cam, area, vtype), count in sorted(agg.items()):
            writer.writerow([date_str, cam, area, vtype, count])
    print(f"Wrote {len(agg)} rows -> {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("POSTGRES_PORT", "5432"))
    parser.add_argument("--db", default=os.environ.get("POSTGRES_DB", "ppe_compliance"))
    parser.add_argument("--user", default=os.environ.get("POSTGRES_USER", "ppe_user"))
    parser.add_argument("--password", default=os.environ.get("POSTGRES_PASSWORD", "ppe_password"))
    parser.add_argument("--out", default="powerbi_data")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db, user=args.user, password=args.password)
    try:
        rows = export_table(conn, "SELECT * FROM violations ORDER BY epoch_time DESC", os.path.join(args.out, "violations.csv"))
        export_daily_summary(rows, args.out)
        export_table(conn, "SELECT * FROM camera_status", os.path.join(args.out, "camera_status.csv"))
        export_table(conn, "SELECT * FROM alert_rules ORDER BY id", os.path.join(args.out, "alert_rules.csv"))
        export_table(conn, "SELECT c.*, ag.name as area_group_name FROM cameras c LEFT JOIN area_groups ag ON ag.id=c.area_group_id", os.path.join(args.out, "cameras.csv"))
        export_table(conn, "SELECT * FROM area_groups", os.path.join(args.out, "area_groups.csv"))
    finally:
        conn.close()
    print("\nDone:", os.path.abspath(args.out))
