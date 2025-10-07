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
