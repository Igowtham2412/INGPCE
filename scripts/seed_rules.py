"""seed_rules.py - inserts starter alert rules. Run once after first boot."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, config

RULES = [
    {"name": "Any Zone - Missing Helmet", "camera_id": None, "zone": None,
     "violation_type": "no_helmet", "min_severity": 1, "channels": ["teams"],
     "cooldown_seconds": 300, "email_recipients": [], "enabled": True},
    {"name": "Any Zone - Missing Vest", "camera_id": None, "zone": None,
     "violation_type": "no_vest", "min_severity": 1, "channels": ["teams"],
     "cooldown_seconds": 300, "email_recipients": [], "enabled": True},
    {"name": "Substation - Any Violation (Escalated)", "camera_id": None, "zone": "Substation",
     "violation_type": "*", "min_severity": 1, "channels": ["teams", "email"],
     "cooldown_seconds": 180, "email_recipients": config.ALERT_EMAIL_TO_DEFAULT or ["safety.team@yourcompany.com"],
     "enabled": True},
]

async def main():
    await db.init_pool()
    existing = await db.list_rules()
    existing_names = {r["name"] for r in existing}
    created = 0
    for rule in RULES:
        if rule["name"] in existing_names:
            print(f"Skipping (already exists): {rule['name']}")
            continue
        await db.create_rule(rule["name"], rule["camera_id"], rule["zone"], rule["violation_type"],
                              rule["min_severity"], rule["channels"], rule["cooldown_seconds"],
                              rule["email_recipients"], rule["enabled"])
        print(f"Created rule: {rule['name']}")
        created += 1
    print(f"\nDone. {created} new rule(s) created, {len(existing_names)} already existed.")
    await db.close_pool()

if __name__ == "__main__":
    asyncio.run(main())
