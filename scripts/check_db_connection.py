"""check_db_connection.py - standalone DB connection diagnostic."""
import sys, os, asyncio
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import asyncpg
from app import config

async def main():
    print("=" * 70)
    print("PostgreSQL Connection Diagnostic")
    print("=" * 70)
    print(f"  Host: {config.PG_HOST}  Port: {config.PG_PORT}  DB: {config.PG_DB}  User: {config.PG_USER}")
    print("=" * 70)
    print("\n[1/2] Attempting to connect...")
    try:
        conn = await asyncpg.connect(host=config.PG_HOST, port=config.PG_PORT, database=config.PG_DB,
                                      user=config.PG_USER, password=config.PG_PASSWORD, timeout=8)
        print("      Connected successfully!")
    except asyncpg.InvalidPasswordError:
        print("      FAILED: Invalid password."); sys.exit(1)
    except asyncpg.InvalidCatalogNameError:
        print(f"      FAILED: Database '{config.PG_DB}' does not exist."); sys.exit(1)
    except (ConnectionRefusedError, OSError) as e:
        print(f"      FAILED: Connection refused - {e}")
        print("        -> Check that PostgreSQL is running and POSTGRES_HOST/PORT are correct."); sys.exit(1)
    except Exception as e:
        print(f"      FAILED: {type(e).__name__}: {e}"); sys.exit(1)

    print("\n[2/2] Checking how many cameras are currently in the database...")
    try:
        rows = await conn.fetch("SELECT camera_id FROM cameras ORDER BY camera_id;")
    except Exception:
        print("      (cameras table doesn't exist yet - it's created on first successful app startup)")
        rows = []
    print(f"      {len(rows)} camera(s) found: {[r['camera_id'] for r in rows]}")
    if len(rows) < len(config.SEED_CAMERAS):
        print(f"\n      NOTE: config.py defines {len(config.SEED_CAMERAS)} seed cameras but only")
        print(f"      {len(rows)} are in the database. Run this to add the missing ones:")
        print(f"        curl -X POST http://localhost:8080/api/cameras/sync-seed")
        print(f"      (or click 'Sync Default Cameras' in the dashboard's Cameras tab)")

    await conn.close()
    print("\n" + "=" * 70)
    print("DONE.")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
