#!/usr/bin/env python
# verify_env.py
# تشغيل: py verify_env.py

import os
import sys
from dotenv import load_dotenv

load_dotenv()

print("=== VERIFY ENV & SERVICES ===\n")

errors = []

# 1) Env vars
db_url = os.environ.get("DATABASE_URL")
cloud_url = os.environ.get("CLOUDINARY_URL")
secret = os.environ.get("SECRET_KEY")

print("1) Environment variables:")
print("  - DATABASE_URL:", "SET" if db_url else "MISSING")
print("  - CLOUDINARY_URL:", "SET" if cloud_url else "MISSING")
print("  - SECRET_KEY:", "SET" if secret else "MISSING")
if not db_url: errors.append("DATABASE_URL missing")
if not cloud_url: errors.append("CLOUDINARY_URL missing")
if not secret: print("  (SECRET_KEY optional but recommended)")

# 2) Required packages check
print("\n2) Checking Python packages...")
missing_pkgs = []
try:
    import psycopg2
    import psycopg2.extras
except Exception:
    missing_pkgs.append("psycopg2-binary (or psycopg2)")
try:
    import cloudinary, cloudinary.uploader
except Exception:
    missing_pkgs.append("cloudinary")
try:
    import requests
except Exception:
    # requests used only for optional network test; not strictly required
    missing_pkgs.append("requests (optional)")
if missing_pkgs:
    print("  Missing packages:", ", ".join(missing_pkgs))
    print("  Install: pip install " + " ".join([p for p in missing_pkgs if p]))
    errors.extend(missing_pkgs)
else:
    print("  All required packages appear installed.")

# 3) Test DB connection and required tables
if db_url and 'psycopg2' not in missing_pkgs:
    print("\n3) Testing PostgreSQL connection...")
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT now() as now")
        now = cur.fetchone()['now']
        print("  Connected to DB, server time:", now)
        # check tables
        required_tables = ['users','products','orders','withdrawals','product_images','categories','pages','bonuses']
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name = ANY(%s);
        """, (required_tables,))
        found = [r[0] for r in cur.fetchall()]
        missing = [t for t in required_tables if t not in found]
        if missing:
            print("  Missing tables (might be ok if first run):", missing)
        else:
            print("  All required tables present.")
        cur.close(); conn.close()
    except Exception as e:
        print("  DB connection failed:", repr(e))
        errors.append("DB connection failed: " + str(e))
else:
    print("\n3) Skipping DB test (DATABASE_URL missing or psycopg2 not installed).")

# 4) Cloudinary upload test
if cloud_url and 'cloudinary' not in missing_pkgs:
    print("\n4) Testing Cloudinary upload...")
    try:
        cloudinary.config(cloudinary_url=cloud_url)
        uploader = cloudinary.uploader
        test_path = os.path.join("static", "img", "placeholder.svg")
        if not os.path.exists(test_path):
            # create a tiny placeholder if not exist
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            with open(test_path, "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect width="100%" height="100%" fill="#ddd"/></svg>')
            created_placeholder = True
        else:
            created_placeholder = False

        res = uploader.upload(test_path, folder="dzshop/verify", resource_type="image", use_filename=True, unique_filename=True)
        secure = res.get("secure_url") or res.get("url")
        if secure:
            print("  Upload OK. URL:", secure)
            print("  Tip: that URL should be persistent (Cloudinary).")
        else:
            print("  Upload response (no secure_url):", res)
            errors.append("Cloudinary upload returned no secure_url")
        # optional: don't delete the resource (so you can inspect it), or delete by public_id if desired
    except Exception as e:
        print("  Cloudinary upload failed:", repr(e))
        errors.append("Cloudinary upload failed: " + str(e))
else:
    print("\n4) Skipping Cloudinary test (CLOUDINARY_URL missing or cloudinary lib not installed).")

# 5) Final report
print("\n=== SUMMARY ===")
if errors:
    print("Problems detected:")
    for it in errors:
        print(" -", it)
    print("\nFix the above issues, then re-run: py verify_env.py")
    sys.exit(1)
else:
    print("All checks passed ✅")
    sys.exit(0)


@app.errorhandler(404)
def _e404(e):
    try:
        return render_template("error.html", message="404 - غير موجود", status=404), 404
    except Exception:
        return "404 - غير موجود", 404

@app.errorhandler(403)
def _e403(e):
    try:
        return render_template("error.html", message="403 - ممنوع", status=403), 403
    except Exception:
        return "403 - ممنوع", 403
