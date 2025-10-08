# app_pg.py — Mostefaoui DZShop Affiliates (complete)
# تشغيل محلي:  py app_pg.py
# تشغيل إنتاج (Render): gunicorn app_pg:app --workers 3 --timeout 120 --bind 0.0.0.0:$PORT
# .env يجب أن يحتوي: DATABASE_URL, SECRET_KEY, ADMIN_PASSWORD, (اختياري) CLOUDINARY_URL

import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional, Tuple

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import psycopg
import psycopg.rows

import cloudinary
import cloudinary.uploader

# ===================== إعداد البيئة =====================
load_dotenv()
APP_NAME = "Mostefaoui DZShop Affiliates"

DATABASE_URL       = os.getenv("DATABASE_URL", "").strip()
SECRET_KEY         = os.getenv("SECRET_KEY", "change-this-secret")
ADMIN_PASSWORD     = os.getenv("ADMIN_PASSWORD", "admin123")
CLOUDINARY_URL     = os.getenv("CLOUDINARY_URL", "").strip()
WITHDRAW_MIN       = float(os.getenv("WITHDRAW_MIN", "5000"))
WEEKLY_BONUS       = float(os.getenv("WEEKLY_BONUS_AMOUNT", "1000"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL مفقود")

app = Flask(__name__, static_folder="static")
app.config["SECRET_KEY"] = SECRET_KEY

UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {"png","jpg","jpeg","webp","gif","svg"}

USE_CLOUDINARY = False
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)
    USE_CLOUDINARY = True

# ===================== أدوات قاعدة البيانات =====================
def get_db():
    return psycopg.connect(DATABASE_URL, autocommit=False)

def q_all(sql, params=()):
    with get_db() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params); return cur.fetchall()

def q_one(sql, params=()):
    with get_db() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params); return cur.fetchone()

def exec_sql(sql, params=()):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()

# ===================== مساعدين =====================
def now_iso(): return datetime.now(timezone.utc).isoformat()

def allowed_file(filename:str)->bool:
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXT

def save_image(file_storage):
    if not file_storage or file_storage.filename=="" or not allowed_file(file_storage.filename):
        return None
    if USE_CLOUDINARY:
        res = cloudinary.uploader.upload(
            file_storage,
            folder="dzshop/products",
            resource_type="image",
            use_filename=True,
            unique_filename=True,
            overwrite=False
        )
        return res.get("secure_url")
    # محلي
    filename = secure_filename(file_storage.filename)
    base, ext = os.path.splitext(filename)
    uniq = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    filename = f"{base}_{uniq}{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    file_storage.save(path)
    return "/" + path.replace("\\","/")

def dl_url(url_or_path:str)->str:
    if not url_or_path:
        return url_for("static", filename="img/placeholder.svg")
    s=url_or_path.strip()
    if s.startswith("http://") or s.startswith("https://"):
        if "/upload/" in s and "fl_attachment" not in s:
            return s.replace("/upload/","/upload/fl_attachment/",1)
        return s
    if s.startswith("/static/"): return s
    if s.startswith("static/"): return "/"+s
    return s

@app.context_processor
def inject_globals():
    return dict(app_name=APP_NAME, dl_url=dl_url)

# ===================== تهيئة القاعدة =====================
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL,
              email TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('affiliate','admin')),
              approved BOOLEAN NOT NULL DEFAULT FALSE,
              phone TEXT,
              created_at TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS categories(
              id SERIAL PRIMARY KEY,
              name TEXT UNIQUE NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS products(
              id SERIAL PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT,
              price NUMERIC NOT NULL,
              commission NUMERIC NOT NULL,
              delivery_price NUMERIC NOT NULL,
              image_path TEXT,
              category_id INTEGER REFERENCES categories(id),
              delivery_mode TEXT CHECK (delivery_mode IN ('home','office')) DEFAULT 'home',
              notes TEXT,
              created_at TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS product_images(
              id SERIAL PRIMARY KEY,
              product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
              image_path TEXT NOT NULL,
              created_at TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS orders(
              id SERIAL PRIMARY KEY,
              product_id INTEGER NOT NULL REFERENCES products(id),
              affiliate_id INTEGER NOT NULL REFERENCES users(id),
              customer_name TEXT NOT NULL,
              customer_phone TEXT NOT NULL,
              customer_address TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','delivered','canceled')),
              created_at TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals(
              id SERIAL PRIMARY KEY,
              affiliate_id INTEGER NOT NULL REFERENCES users(id),
              amount NUMERIC NOT NULL,
              method TEXT NOT NULL,
              details TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('requested','approved','rejected')),
              bonus NUMERIC NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pages(
              id SERIAL PRIMARY KEY,
              slug TEXT UNIQUE NOT NULL,
              title TEXT NOT NULL,
              content TEXT NOT NULL
            );""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bonuses(
              id SERIAL PRIMARY KEY,
              affiliate_id INTEGER NOT NULL REFERENCES users(id),
              iso_year INTEGER NOT NULL,
              iso_week INTEGER NOT NULL,
              amount NUMERIC NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(affiliate_id, iso_year, iso_week)
            );""")

            # صفحات افتراضية
            for slug,title in [("privacy","سياسة الخصوصية"),("about","من نحن"),("contact","تواصل معنا")]:
                cur.execute("SELECT 1 FROM pages WHERE slug=%s",(slug,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO pages(slug,title,content) VALUES(%s,%s,%s)",
                                (slug,title,f"{title} - محتوى افتراضي."))

            # أدمن افتراضي
            cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
            if not cur.fetchone():
                cur.execute("""INSERT INTO users(name,email,password_hash,role,approved,created_at)
                               VALUES(%s,%s,%s,%s,%s,%s)""",
                            ("Admin","admin@local",generate_password_hash(ADMIN_PASSWORD),"admin",True,now_iso()))
        conn.commit()

init_db()

# ===================== الحماية =====================
def login_required(role: Optional[str]=None):
    def deco(f):
        @wraps(f)
        def wrap(*a, **kw):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role")!=role:
                abort(403)
            return f(*a, **kw)
        return wrap
    return deco

def current_user():
    if "user_id" not in session: return None
    return q_one("SELECT * FROM users WHERE id=%s",(session["user_id"],))

# ===================== العلاوة الأسبوعية =====================
def iso_year_week(dt:Optional[datetime]=None)->Tuple[int,int]:
    if not dt: dt=datetime.now(timezone.utc)
    iso=dt.isocalendar(); return iso.year, iso.week

def weekly_bonus_pending(affiliate_id:int)->float:
    since=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    row=q_one("""SELECT COUNT(*) AS n FROM orders
                 WHERE affiliate_id=%s AND status='delivered' AND created_at>=%s""",(affiliate_id,since))
    n=int(row["n"]) if row and row.get("n") is not None else 0
    candidate=(n//10)*WEEKLY_BONUS
    y,w=iso_year_week()
    exists=q_one("SELECT 1 FROM bonuses WHERE affiliate_id=%s AND iso_year=%s AND iso_week=%s",
                 (affiliate_id,y,w))
    return 0.0 if exists else float(candidate)

def mark_bonus_paid(conn, affiliate_id:int, amount:float)->float:
    if amount<=0: return 0.0
    y,w=iso_year_week()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO bonuses(affiliate_id,iso_year,iso_week,amount,created_at)
                           VALUES(%s,%s,%s,%s,%s)""",(affiliate_id,y,w,amount,now_iso()))
        return float(amount)
    except Exception:
        return 0.0

# ===================== مصادقة =====================
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        phone=request.form.get("phone","").strip()
        password=request.form.get("password","")
        if not name or not email or not phone or not password:
            flash("املأ كل الحقول","danger"); return redirect(url_for("register"))
        try:
            exec_sql("""INSERT INTO users(name,email,password_hash,role,approved,phone,created_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                     (name,email,generate_password_hash(password),"affiliate",False,phone,now_iso()))
            flash("تم التسجيل. بانتظار موافقة الإدارة.","success")
            return redirect(url_for("login"))
        except Exception:
            flash("الإيميل مستخدم أو خطأ في التسجيل","danger")
            return redirect(url_for("register"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        pwd=request.form.get("password","")
        u=q_one("SELECT * FROM users WHERE email=%s",(email,))
        if not u or not check_password_hash(u["password_hash"], pwd):
            flash("بيانات الدخول غير صحيحة","danger"); return redirect(url_for("login"))
        if u["role"]=="affiliate" and not u["approved"]:
            flash("حسابك بانتظار الموافقة","warning"); return redirect(url_for("login"))
        session["user_id"]=u["id"]; session["role"]=u["role"]
        return redirect(url_for("affiliate_products" if u["role"]=="affiliate" else "admin_dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); flash("تم تسجيل الخروج","info")
    return redirect(url_for("login"))

# ===================== صفحات عامة =====================
@app.route("/privacy")
def privacy():  return render_template("page.html", page=q_one("SELECT * FROM pages WHERE slug='privacy'"))
@app.route("/about")
def about():    return render_template("page.html", page=q_one("SELECT * FROM pages WHERE slug='about'"))
@app.route("/contact")
def contact():  return render_template("page.html", page=q_one("SELECT * FROM pages WHERE slug='contact'"))

# ===================== توجيه أولي =====================
@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("affiliate_products" if session.get("role")=="affiliate" else "admin_dashboard"))
    return redirect(url_for("login"))

# ===================== واجهة المسوّق =====================
@app.route("/affiliate/products")
@login_required(role="affiliate")
def affiliate_products():
    cat_id=request.args.get("cat", type=int)
    if cat_id:
        products=q_all("""SELECT p.*, c.name AS category_name
                          FROM products p LEFT JOIN categories c ON c.id=p.category_id
                          WHERE p.category_id=%s ORDER BY p.id DESC""",(cat_id,))
    else:
        products=q_all("""SELECT p.*, c.name AS category_name
                          FROM products p LEFT JOIN categories c ON c.id=p.category_id
                          ORDER BY p.id DESC""")
    cats=q_all("SELECT * FROM categories ORDER BY name ASC")
    return render_template("affiliate/products.html", products=products, categories=cats)

@app.route("/affiliate/categories")
@login_required(role="affiliate")
def affiliate_categories():
    return render_template("affiliate/categories.html", categories=q_all("SELECT * FROM categories ORDER BY name ASC"))

@app.route("/affiliate/product/<int:pid>")
@login_required(role="affiliate")
def affiliate_product_detail(pid):
    p=q_one("""SELECT p.*, c.name AS category_name
               FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=%s""",(pid,))
    if not p: abort(404)
    imgs=q_all("SELECT image_path FROM product_images WHERE product_id=%s ORDER BY id ASC",(pid,))
    return render_template("affiliate/product_detail.html", p=p, images=imgs)

@app.route("/affiliate/order/<int:pid>", methods=["GET","POST"])
@login_required(role="affiliate")
def affiliate_order(pid):
    p=q_one("SELECT * FROM products WHERE id=%s",(pid,))
    if not p: abort(404)
    if request.method=="POST":
        cn=request.form.get("customer_name","").strip()
        cp=request.form.get("customer_phone","").strip()
        ca=request.form.get("customer_address","").strip()
        if not cn or not cp or not ca:
            flash("املأ بيانات الزبون","danger"); return redirect(url_for("affiliate_order", pid=pid))
        exec_sql("""INSERT INTO orders(product_id,affiliate_id,customer_name,customer_phone,customer_address,status,created_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                 (pid, session["user_id"], cn, cp, ca, "pending", now_iso()))
        flash("تم إنشاء الطلبية","success"); return redirect(url_for("affiliate_orders"))
    return render_template("affiliate/order_form.html", p=p)

@app.route("/affiliate/orders")
@login_required(role="affiliate")
def affiliate_orders():
    rows=q_all("""SELECT o.*, p.name AS product_name, p.image_path, p.commission, p.price
                  FROM orders o JOIN products p ON p.id=o.product_id
                  WHERE o.affiliate_id=%s ORDER BY o.id DESC""",(session["user_id"],))
    return render_template("affiliate/orders.html", rows=rows)

def affiliate_balance(aid:int)->float:
    e=q_one("""SELECT COALESCE(SUM(p.commission),0) AS total
               FROM orders o JOIN products p ON p.id=o.product_id
               WHERE o.affiliate_id=%s AND o.status='delivered'""",(aid,))
    earned=float(e["total"]) if e else 0.0
    out=q_one("""SELECT COALESCE(SUM(amount+bonus),0) AS total
                 FROM withdrawals WHERE affiliate_id=%s AND status IN ('requested','approved')""",(aid,))
    spent=float(out["total"]) if out else 0.0
    return earned-spent

@app.route("/affiliate/commissions", methods=["GET","POST"])
@login_required(role="affiliate")
def affiliate_commissions():
    bal=affiliate_balance(session["user_id"])
    bonus_p=weekly_bonus_pending(session["user_id"])
    if request.method=="POST":
        method=request.form.get("method")
        details=request.form.get("details","").strip()
        try: amount=float(request.form.get("amount","0") or 0)
        except: amount=0
        total=bal+bonus_p
        if method not in ("ccp","rib"): flash("اختر CCP أو RIB","danger"); return redirect(url_for("affiliate_commissions"))
        if amount<=0 or amount>total:   flash("قيمة السحب غير صالحة","danger"); return redirect(url_for("affiliate_commissions"))
        if amount<WITHDRAW_MIN and total>=WITHDRAW_MIN:
            flash(f"الحد الأدنى للسحب {int(WITHDRAW_MIN)} دج","danger"); return redirect(url_for("affiliate_commissions"))
        with get_db() as conn:
            awarded=mark_bonus_paid(conn, session["user_id"], bonus_p)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO withdrawals(affiliate_id,amount,method,details,status,bonus,created_at)
                               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                            (session["user_id"],amount,method,details,'requested',awarded,now_iso()))
            conn.commit()
        flash(f"تم إرسال طلب السحب. العلاوة المضافة: {int(awarded)} دج","success")
        return redirect(url_for("affiliate_commissions"))
    return render_template("affiliate/commissions.html", balance=bal, min_withdraw=WITHDRAW_MIN, bonus_pending=bonus_p)

@app.route("/affiliate/settings", methods=["GET","POST"])
@login_required(role="affiliate")
def affiliate_settings():
    if request.method=="POST":
        curp=request.form.get("current_password","")
        new1=request.form.get("new_password","")
        new2=request.form.get("confirm_password","")
        if not new1 or len(new1)<6 or new1!=new2:
            flash("تحقق من كلمة السر الجديدة (≥6 ومطابقة)","danger"); return redirect(url_for("affiliate_settings"))
        u=q_one("SELECT * FROM users WHERE id=%s",(session["user_id"],))
        if not u or not check_password_hash(u["password_hash"], curp):
            flash("كلمة السر الحالية غير صحيحة","danger"); return redirect(url_for("affiliate_settings"))
        exec_sql("UPDATE users SET password_hash=%s WHERE id=%s",(generate_password_hash(new1), session["user_id"]))
        flash("تم تغيير كلمة السر","success"); return redirect(url_for("affiliate_settings"))
    return render_template("affiliate/settings.html")

# ===================== واجهة الأدمن =====================
def admin_required(f): return login_required(role="admin")(f)

@app.route("/admin")
@admin_required
def admin_dashboard():
    stats={
        "orders_total": q_one("SELECT COUNT(*) AS n FROM orders")["n"],
        "delivered":    q_one("SELECT COUNT(*) AS n FROM orders WHERE status='delivered'")["n"],
        "pending":      q_one("SELECT COUNT(*) AS n FROM orders WHERE status='pending'")["n"],
        "canceled":     q_one("SELECT COUNT(*) AS n FROM orders WHERE status='canceled'")["n"],
    }
    latest=q_all("""SELECT o.*, p.name AS product_name, p.image_path, p.price, p.commission, u.name AS affiliate_name
                    FROM orders o JOIN products p ON p.id=o.product_id
                    JOIN users u ON u.id=o.affiliate_id
                    ORDER BY o.id DESC LIMIT 20""")
    withdraws=q_all("""SELECT w.*, u.name AS affiliate_name, u.email
                       FROM withdrawals w JOIN users u ON u.id=w.affiliate_id
                       WHERE w.status='requested' ORDER BY w.id DESC""")
    return render_template("admin/dashboard.html", stats=stats, latest_orders=latest, pending_withdraws=withdraws)

@app.route("/admin/affiliates")
@admin_required
def admin_affiliates():
    pending=q_all("SELECT * FROM users WHERE role='affiliate' AND approved=false ORDER BY id DESC")
    approved=q_all("SELECT * FROM users WHERE role='affiliate' AND approved=true ORDER BY id DESC")
    return render_template("admin/affiliates.html", pending=pending, approved=approved)

@app.route("/admin/affiliates/<int:uid>/set", methods=["POST"])
@admin_required
def admin_affiliate_set(uid):
    action=request.form.get("action")
    if action not in ("approve","disable"): flash("إجراء غير صالح","danger"); return redirect(url_for("admin_affiliates"))
    exec_sql("UPDATE users SET approved=%s WHERE id=%s", (True if action=="approve" else False, uid))
    flash("تم تحديث حالة المسوّق","success"); return redirect(url_for("admin_affiliates"))

@app.route("/admin/affiliates/<int:uid>/reset_password", methods=["POST"])
@admin_required
def admin_affiliate_reset_password(uid):
    new_pass=request.form.get("new_password","").strip()
    if len(new_pass)<6: flash("كلمة السر قصيرة","danger"); return redirect(url_for("admin_affiliates"))
    exec_sql("UPDATE users SET password_hash=%s WHERE id=%s",(generate_password_hash(new_pass), uid))
    flash("تم إعادة تعيين كلمة السر للمسوّق","success"); return redirect(url_for("admin_affiliates"))

@app.route("/admin/products")
@admin_required
def admin_products():
    products=q_all("""SELECT p.*, c.name AS category_name
                      FROM products p LEFT JOIN categories c ON c.id=p.category_id
                      ORDER BY p.id DESC""")
    cats=q_all("SELECT * FROM categories ORDER BY name ASC")
    return render_template("admin/products.html", products=products, categories=cats)

@app.route("/admin/categories/add", methods=["POST"])
@admin_required
def admin_category_add():
    name=request.form.get("name","").strip()
    if not name: flash("أدخل اسم التصنيف","danger"); return redirect(url_for("admin_products"))
    try:
        exec_sql("INSERT INTO categories(name) VALUES(%s)", (name,))
        flash("تمت إضافة التصنيف","success")
    except Exception:
        flash("التصنيف موجود مسبقًا","warning")
    return redirect(url_for("admin_products"))

@app.route("/admin/products/new", methods=["GET","POST"])
@admin_required
def admin_product_new():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        description=request.form.get("description","").strip()
        try:
            price=float(request.form.get("price","0") or 0)
            commission=float(request.form.get("commission","0") or 0)
            delivery_price=float(request.form.get("delivery_price","0") or 0)
        except: flash("تحقق من الأرقام","danger"); return redirect(url_for("admin_product_new"))
        category_id=request.form.get("category_id", type=int)
        delivery_mode=request.form.get("delivery_mode","home")
        notes=request.form.get("notes","").strip()
        main_image=request.files.get("image")
        extra_images=request.files.getlist("images[]")
        if not name or price<=0 or commission<0 or delivery_price<0 or delivery_mode not in ("home","office"):
            flash("تحقق من الحقول","danger"); return redirect(url_for("admin_product_new"))
        main_path=save_image(main_image) or "static/img/placeholder.svg"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,category_id,delivery_mode,notes,created_at)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                           (name,description,price,commission,delivery_price,main_path,category_id,delivery_mode,notes,now_iso()))
                pid=cur.fetchone()[0]
                for f in extra_images:
                    p=save_image(f)
                    if p: cur.execute("INSERT INTO product_images(product_id,image_path,created_at) VALUES(%s,%s,%s)", (pid,p,now_iso()))
            conn.commit()
        flash("تمت إضافة المنتج","success"); return redirect(url_for("admin_products"))
    cats=q_all("SELECT * FROM categories ORDER BY name ASC")
    return render_template("admin/product_form.html", p=None, categories=cats)

@app.route("/admin/products/<int:pid>/edit", methods=["GET","POST"])
@admin_required
def admin_product_edit(pid):
    p=q_one("SELECT * FROM products WHERE id=%s",(pid,))
    if not p: abort(404)
    if request.method=="POST":
        name=request.form.get("name","").strip()
        description=request.form.get("description","").strip()
        try:
            price=float(request.form.get("price","0") or 0)
            commission=float(request.form.get("commission","0") or 0)
            delivery_price=float(request.form.get("delivery_price","0") or 0)
        except: flash("تحقق من الأرقام","danger"); return redirect(url_for("admin_product_edit", pid=pid))
        category_id=request.form.get("category_id", type=int)
        delivery_mode=request.form.get("delivery_mode","home")
        notes=request.form.get("notes","").strip()
        main_image=request.files.get("image")
        extra_images=request.files.getlist("images[]")

        main_path=p["image_path"]
        if main_image and main_image.filename:
            newp=save_image(main_image)
            if newp: main_path=newp

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE products SET name=%s, description=%s, price=%s, commission=%s, delivery_price=%s,
                               image_path=%s, category_id=%s, delivery_mode=%s, notes=%s
                               WHERE id=%s""",
                           (name,description,price,commission,delivery_price,main_path,category_id,delivery_mode,notes,pid))
                for f in extra_images:
                    pth=save_image(f)
                    if pth:
                        cur.execute("INSERT INTO product_images(product_id,image_path,created_at) VALUES(%s,%s,%s)", (pid,pth,now_iso()))
            conn.commit()
        flash("تم تعديل المنتج","success"); return redirect(url_for("admin_products"))
    cats=q_all("SELECT * FROM categories ORDER BY name ASC")
    imgs=q_all("SELECT * FROM product_images WHERE product_id=%s ORDER BY id ASC",(pid,))
    return render_template("admin/product_form.html", p=p, categories=cats, images=imgs)

@app.route("/admin/products/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_product_delete(pid):
    exec_sql("DELETE FROM products WHERE id=%s",(pid,))
    flash("تم حذف المنتج","info"); return redirect(url_for("admin_products"))

# تغيير حالة الطلب من لوحة الأدمن
@app.route("/admin/orders/<int:oid>/status", methods=["POST"])
@admin_required
def admin_order_status(oid):
    status=request.form.get("status")
    if status not in ("pending","delivered","canceled"):
        flash("حالة غير صالحة","danger"); return redirect(url_for("admin_dashboard"))
    exec_sql("UPDATE orders SET status=%s WHERE id=%s",(status,oid))
    flash("تم تحديث حالة الطلب","success"); return redirect(url_for("admin_dashboard"))

# الصفحات
@app.route("/admin/pages", methods=["GET","POST"])
@admin_required
def admin_pages():
    if request.method=="POST":
        slug=request.form.get("slug")
        title=request.form.get("title","").strip()
        content=request.form.get("content","").strip()
        if slug not in ("privacy","about","contact") or not title:
            flash("تحقق من البيانات","danger"); return redirect(url_for("admin_pages"))
        exec_sql("UPDATE pages SET title=%s, content=%s WHERE slug=%s",(title,content,slug))
        flash("تم حفظ الصفحة","success")
    pages=q_all("SELECT * FROM pages ORDER BY slug")
    return render_template("admin/pages.html", pages=pages)

# سحب الأدمن
@app.route("/admin/withdrawals/<int:wid>/set", methods=["POST"])
@admin_required
def admin_withdraw_set(wid):
    status=request.form.get("status")
    if status not in ("approved","rejected"):
        flash("إجراء غير صالح","danger"); return redirect(url_for("admin_dashboard"))
    exec_sql("UPDATE withdrawals SET status=%s WHERE id=%s",(status,wid))
    flash("تم تحديث طلب السحب","success"); return redirect(url_for("admin_dashboard"))

# إعدادات الأدمن
@app.route("/admin/settings", methods=["GET","POST"])
@admin_required
def admin_settings():
    if request.method=="POST":
        new_email=request.form.get("email","").strip().lower()
        new_pass =request.form.get("password","").strip()
        if not new_email:
            flash("الإيميل مطلوب","danger"); return redirect(url_for("admin_settings"))
        admin_user=q_one("SELECT * FROM users WHERE role='admin' LIMIT 1")
        if admin_user:
            if new_pass:
                exec_sql("UPDATE users SET email=%s, password_hash=%s WHERE id=%s",
                         (new_email, generate_password_hash(new_pass), admin_user["id"]))
            else:
                exec_sql("UPDATE users SET email=%s WHERE id=%s",(new_email, admin_user["id"]))
            flash("تم حفظ الإعدادات","success")
        else:
            flash("لا يوجد مستخدم أدمن","danger")
    affiliates=q_all("SELECT id,name,email,phone,approved,created_at FROM users WHERE role='affiliate' ORDER BY id DESC")
    admin_user=q_one("SELECT id,name,email FROM users WHERE role='admin' LIMIT 1")
    return render_template("admin/settings.html", admin_user=admin_user, affiliates=affiliates)

# ===================== API مساعدة للصور =====================
@app.route("/api/product/<int:pid>/images")
def api_product_images(pid):
    rows=q_all("SELECT image_path FROM product_images WHERE product_id=%s ORDER BY id ASC",(pid,))
    return jsonify([r["image_path"] for r in rows])

# ===================== أخطاء =====================
@app.errorhandler(403)
def e403(_): return render_template("error.html", message="403 - ممنوع"), 403
@app.errorhandler(404)
def e404(_): return render_template("error.html", message="404 - غير موجود"), 404

# ===================== تشغيل =====================
if __name__=="__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
