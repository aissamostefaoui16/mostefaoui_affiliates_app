import psycopg2, os
from dotenv import load_dotenv

load_dotenv()

try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
    tables = [r[0] for r in cur.fetchall()]
    print("\n🗂️ Tables in DB:")
    if tables:
        for t in tables:
            print(" -", t)
    else:
        print("⚠️ No tables found.")
    cur.close()
    conn.close()
except Exception as e:
    print("❌ Error:", e)


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
