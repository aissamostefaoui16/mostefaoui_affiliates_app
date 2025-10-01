import sqlite3

DB = "app.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def table_cols(table):
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]

def ensure_column(table, col_def):
    # col_def example: "image_path TEXT"
    col = col_def.split()[0]
    if col not in table_cols(table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

# 1) products: إضافة image_path ونسخ من image_url إن موجودة
ensure_column('products', 'image_path TEXT')
cols_products = table_cols('products')
if 'image_url' in cols_products:
    c.execute("UPDATE products SET image_path=image_url WHERE (image_path IS NULL OR image_path='') AND image_url IS NOT NULL")

# 2) users: إضافة approved إن ناقص
ensure_column('users', 'approved INTEGER NOT NULL DEFAULT 0')

# 3) product_images: تحويل image_url -> image_path أو إنشاء الجدول إن ناقص
try:
    cols_pi = table_cols('product_images')
except sqlite3.OperationalError:
    cols_pi = []
    c.execute("""
        CREATE TABLE IF NOT EXISTS product_images(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

if cols_pi:
    if 'image_path' not in cols_pi and 'image_url' in cols_pi:
        # نعيد تسمية الجدول وننشئه من جديد بالأعمدة الصحيحة ثم ننسخ البيانات
        c.execute("ALTER TABLE product_images RENAME TO product_images_old")
        c.execute("""
            CREATE TABLE product_images(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)
        c.execute("""
            INSERT INTO product_images(product_id,image_path,created_at)
            SELECT product_id,image_url,created_at FROM product_images_old
        """)
        c.execute("DROP TABLE product_images_old")
    elif 'image_path' not in cols_pi and 'image_url' not in cols_pi:
        # ماكان لا image_path لا image_url -> ننشئه صحيح
        c.execute("DROP TABLE IF EXISTS product_images")
        c.execute("""
            CREATE TABLE product_images(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)

conn.commit()
conn.close()
print("Migration done.")
