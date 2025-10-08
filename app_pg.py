import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import psycopg2
import psycopg2.extras

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

# ---------------- Env & App Config ----------------
load_dotenv()

APP_NAME = "Mostefaoui DZShop Affiliates"
WITHDRAW_MIN = 5000.0         # حد السحب الأدنى
WEEKLY_BONUS_AMOUNT = 1000.0  # علاوة كل 10 طلبيات مؤكدة خلال أسبوع

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'}

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")

# Cloudinary (من .env)
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()
USE_CLOUDINARY = bool(CLOUDINARY_URL)
if USE_CLOUDINARY:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- Helpers ----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_file(fs):
    """رفع الصورة إلى Cloudinary إن وُجد، وإلا حفظ محلي."""
    if not fs or not allowed_file(fs.filename):
        return None

    if USE_CLOUDINARY:
        result = cloudinary.uploader.upload(
            fs,
            folder="dzshop/products",
            resource_type="image",
            use_filename=True,
            unique_filename=True
        )
        return result.get('secure_url')

    # Local fallback (للاستخدام المحلي فقط)
    filename = secure_filename(fs.filename)
    base, ext = os.path.splitext(filename)
    uniq = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    filename = f"{base}_{uniq}{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fs.save(path)
    return path.replace("\\", "/")

def make_dl_url(url_or_path):
    """رابط تنزيل مباشر (Cloudinary يدعم fl_attachment)."""
    if not url_or_path:
        return url_for('static', filename='img/placeholder.svg')
    s = url_or_path.strip()
    if s.startswith('http://') or s.startswith('https://'):
        # Cloudinary: فعّل fl_attachment للتنزيل
        if '/upload/' in s and 'fl_attachment' not in s:
            return s.replace('/upload/', '/upload/fl_attachment/', 1)
        return s
    if s.startswith('static/'):
        return url_for('static', filename=s.split('static/', 1)[1])
    return s

@app.context_processor
def inject_helpers():
    def static_url(path):
        if not path or path.strip() == "":
            return url_for('static', filename='img/placeholder.svg')
        if path.startswith('static/'):
            return url_for('static', filename=path.split('static/', 1)[1])
        if path.startswith('http://') or path.startswith('https://'):
            return path
        return path
    return dict(static_url=static_url, dl_url=make_dl_url, app_name=APP_NAME)

# ---------------- DB ----------------
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def pg_exec(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('affiliate','admin')),
        approved INTEGER NOT NULL DEFAULT 0,
        phone TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS approved INTEGER NOT NULL DEFAULT 0;")

    # categories
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL
    );
    """)

    # products
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
    );
    """)
    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES categories(id);")
    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS delivery_mode TEXT;")
    cur.execute("ALTER TABLE products ALTER COLUMN delivery_mode SET DEFAULT 'home';")
    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS notes TEXT;")

    # product_images
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_images(
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        image_path TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    # orders
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
    );
    """)

    # withdrawals (+ bonus)
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
    );
    """)
    cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS bonus NUMERIC NOT NULL DEFAULT 0;")

    # pages (privacy/about/contact)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pages(
        id SERIAL PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL
    );
    """)
    for slug, title in [('privacy','سياسة الخصوصية'), ('about','من نحن'), ('contact','تواصل معنا')]:
        cur.execute("SELECT 1 FROM pages WHERE slug=%s", (slug,))
        if not cur.fetchone():
            cur.execute("INSERT INTO pages(slug,title,content) VALUES(%s,%s,%s)",
                        (slug, title, f"{title} - محتوى افتراضي."))

    # bonuses (لمنع صرف العلاوة مرتين في نفس الأسبوع)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bonuses(
        id SERIAL PRIMARY KEY,
        affiliate_id INTEGER NOT NULL REFERENCES users(id),
        iso_year INTEGER NOT NULL,
        iso_week INTEGER NOT NULL,
        amount NUMERIC NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(affiliate_id, iso_year, iso_week)
    );
    """)

    conn.commit()

    # seed admin
    cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    admin = cur.fetchone()
    if not admin:
        admin_pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
        cur.execute("""INSERT INTO users(name,email,password_hash,role,approved,created_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    ('Admin','admin@local', generate_password_hash(admin_pwd),'admin',1, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    # seed category + sample product
    cur.execute("SELECT COUNT(*) AS n FROM products")
    if cur.fetchone()['n'] == 0:
        cur.execute("INSERT INTO categories(name) VALUES('عام') ON CONFLICT DO NOTHING;")
        cur.execute("SELECT id FROM categories WHERE name='عام'")
        cat_id = cur.fetchone()['id']
        cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,category_id,delivery_mode,notes,created_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    ('مثال: خلاط مطبخ','وصف موجز.',12990,800,700,'static/img/placeholder.svg', cat_id, 'home','ملاحظة إدارية تجريبية.', datetime.now(timezone.utc).isoformat()))
        conn.commit()

    cur.close()
    conn.close()

init_db()

# ---------------- Auth utils ----------------
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator

def current_user():
    if 'user_id' not in session: return None
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM users WHERE id=%s", (session['user_id'],))
    u = cur.fetchone(); cur.close(); conn.close()
    return u

# ---------------- Bonus helpers ----------------
def get_current_iso_year_week():
    iso = datetime.now(timezone.utc).isocalendar()  # (year, week, weekday)
    return iso.year, iso.week

def calc_weekly_bonus_pending(affiliate_id):
    """
    يحسب العلاوة المتاحة لهذا الأسبوع (غير المصروفة):
    - يحسب عدد الطلبات المؤكدة (delivered) خلال آخر 7 أيام.
    - كل 10 = 1000 دج.
    - لو مسجلة في bonuses هذا الأسبوع → 0.
    """
    conn = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    cur = pg_exec(conn, """
        SELECT COUNT(*) AS n
        FROM orders
        WHERE affiliate_id=%s AND status='delivered' AND created_at >= %s
    """, (affiliate_id, since))
    n = cur.fetchone()['n']
    bonus_candidate = (n // 10) * WEEKLY_BONUS_AMOUNT

    y, w = get_current_iso_year_week()
    cur2 = pg_exec(conn, "SELECT 1 FROM bonuses WHERE affiliate_id=%s AND iso_year=%s AND iso_week=%s",
                   (affiliate_id, y, w))
    already = cur2.fetchone() is not None
    cur.close(); cur2.close(); conn.close()
    return 0.0 if already else float(bonus_candidate)

def award_weekly_bonus_if_any(conn, affiliate_id, amount):
    """يسجل العلاوة للأسبوع الحالي (إن وجدت) ويمنع صرفها مرة ثانية."""
    if amount <= 0: return 0.0
    y, w = get_current_iso_year_week()
    try:
        pg_exec(conn, """INSERT INTO bonuses(affiliate_id,iso_year,iso_week,amount,created_at)
                         VALUES(%s,%s,%s,%s,%s)""",
               (affiliate_id, y, w, amount, datetime.now(timezone.utc).isoformat()))
        return float(amount)
    except psycopg2.Error:
        return 0.0

# ---------------- Auth routes ----------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        phone = request.form.get('phone','').strip()
        password = request.form.get('password','')
        if not name or not email or not phone or not password:
            flash('الرجاء ملء جميع الحقول','danger'); return redirect(url_for('register'))
        conn = get_db()
        try:
            pg_exec(conn, """INSERT INTO users(name,email,password_hash,role,approved,phone,created_at)
                             VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (name,email,generate_password_hash(password),'affiliate',0,phone,datetime.now(timezone.utc).isoformat()))
            conn.commit()
            flash('تم التسجيل. حسابك بانتظار موافقة الإدارة.','success')
            return redirect(url_for('login'))
        except psycopg2.Error:
            flash('الإيميل مستخدم مسبقًا','danger')
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        conn = get_db()
        cur = pg_exec(conn, "SELECT * FROM users WHERE email=%s", (email,))
        u = cur.fetchone(); cur.close(); conn.close()
        if u and check_password_hash(u['password_hash'], password):
            if u['role']=='affiliate' and not u['approved']:
                flash('حسابك بانتظار الموافقة','warning'); return redirect(url_for('login'))
            session['user_id']=u['id']; session['role']=u['role']
            return redirect(url_for('affiliate_products' if u['role']=='affiliate' else 'admin_dashboard'))
        flash('بيانات الدخول غير صحيحة','danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); flash('تم تسجيل الخروج','info')
    return redirect(url_for('login'))

# ---------------- Static pages ----------------
@app.route('/privacy')
def privacy():
    conn = get_db(); cur = pg_exec(conn,"SELECT * FROM pages WHERE slug='privacy'"); pg=cur.fetchone()
    cur.close(); conn.close()
    return render_template('page.html', page=pg)

@app.route('/about')
def about():
    conn = get_db(); cur = pg_exec(conn,"SELECT * FROM pages WHERE slug='about'"); pg=cur.fetchone()
    cur.close(); conn.close()
    return render_template('page.html', page=pg)

@app.route('/contact')
def contact():
    conn = get_db(); cur = pg_exec(conn,"SELECT * FROM pages WHERE slug='contact'"); pg=cur.fetchone()
    cur.close(); conn.close()
    return render_template('page.html', page=pg)

# ---------------- Affiliate ----------------
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('affiliate_products' if session.get('role')=='affiliate' else 'admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/affiliate/products')
@login_required(role='affiliate')
def affiliate_products():
    cat_id = request.args.get('cat', type=int)
    conn = get_db()
    if cat_id:
        cur = pg_exec(conn, """SELECT p.*, c.name AS category_name
                               FROM products p LEFT JOIN categories c ON c.id=p.category_id
                               WHERE p.category_id=%s ORDER BY p.id DESC""",(cat_id,))
    else:
        cur = pg_exec(conn, """SELECT p.*, c.name AS category_name
                               FROM products p LEFT JOIN categories c ON c.id=p.category_id
                               ORDER BY p.id DESC""")
    products = cur.fetchall()
    cur2 = pg_exec(conn, "SELECT * FROM categories ORDER BY name ASC")
    cats = cur2.fetchall()
    cur.close(); cur2.close(); conn.close()
    return render_template('affiliate/products.html', products=products, categories=cats, active_cat=cat_id)

@app.route('/affiliate/categories')
@login_required(role='affiliate')
def affiliate_categories():
    conn=get_db(); cur=pg_exec(conn,"SELECT * FROM categories ORDER BY name ASC"); cats = cur.fetchall()
    cur.close(); conn.close()
    return render_template('affiliate/categories.html', categories=cats)

@app.route('/affiliate/product/<int:pid>')
@login_required(role='affiliate')
def affiliate_product_detail(pid):
    conn = get_db()
    cur = pg_exec(conn, "SELECT p.*, c.name AS category_name FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=%s",(pid,))
    p = cur.fetchone()
    if not p: cur.close(); conn.close(); abort(404)
    cur2 = pg_exec(conn, "SELECT image_path FROM product_images WHERE product_id=%s ORDER BY id DESC",(pid,))
    imgs = cur2.fetchall()
    cur.close(); cur2.close(); conn.close()
    return render_template('affiliate/product_detail.html', p=p, images=imgs)

@app.route('/affiliate/order/<int:pid>', methods=['GET','POST'])
@login_required(role='affiliate')
def affiliate_order(pid):
    conn=get_db(); cur=pg_exec(conn,"SELECT * FROM products WHERE id=%s",(pid,)); p=cur.fetchone()
    if not p: cur.close(); conn.close(); abort(404)
    if request.method=='POST':
        customer_name=request.form.get('customer_name','').strip()
        customer_phone=request.form.get('customer_phone','').strip()
        customer_address=request.form.get('customer_address','').strip()
        if not customer_name or not customer_phone or not customer_address:
            flash('املأ جميع حقول الزبون','danger'); return redirect(url_for('affiliate_order', pid=pid))
        pg_exec(conn, """INSERT INTO orders(product_id,affiliate_id,customer_name,customer_phone,customer_address,status,created_at)
                         VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (pid, session['user_id'], customer_name, customer_phone, customer_address,'pending',datetime.now(timezone.utc).isoformat()))
        conn.commit(); cur.close(); conn.close()
        flash('تم إنشاء الطلبية وستظهر في لوحة الإدارة','success')
        return redirect(url_for('affiliate_orders'))
    cur.close(); conn.close()
    return render_template('affiliate/order_form.html', p=p)

@app.route('/affiliate/orders')
@login_required(role='affiliate')
def affiliate_orders():
    conn=get_db()
    cur=pg_exec(conn, """
    SELECT o.*, p.name AS product_name, p.price, p.commission, p.image_path
    FROM orders o JOIN products p ON p.id=o.product_id
    WHERE o.affiliate_id=%s ORDER BY o.id DESC
    """,(session['user_id'],))
    rows=cur.fetchall(); cur.close(); conn.close()
    return render_template('affiliate/orders.html', rows=rows)

def calc_affiliate_balance(affiliate_id):
    conn=get_db()
    cur=pg_exec(conn, """
        SELECT COALESCE(SUM(p.commission),0) AS total
        FROM orders o JOIN products p ON p.id=o.product_id
        WHERE o.affiliate_id=%s AND o.status='delivered'
    """,(affiliate_id,))
    earned=cur.fetchone()['total']
    cur2=pg_exec(conn, """
        SELECT COALESCE(SUM(amount+bonus),0) AS total FROM withdrawals
        WHERE affiliate_id=%s AND status IN ('requested','approved')
    """,(affiliate_id,))
    requested=cur2.fetchone()['total']
    cur.close(); cur2.close(); conn.close()
    return float(earned)-float(requested)

@app.route('/affiliate/commissions', methods=['GET','POST'])
@login_required(role='affiliate')
def affiliate_commissions():
    bal=calc_affiliate_balance(session['user_id'])
    bonus_pending = calc_weekly_bonus_pending(session['user_id'])  # قد يكون 0 لو صُرفت هذا الأسبوع

    if request.method=='POST':
        method=request.form.get('method')
        details=request.form.get('details','').strip()
        try: amount=float(request.form.get('amount','0') or 0)
        except: amount=0
        total_available = bal + bonus_pending
        if method not in ('ccp','rib'):
            flash('اختر CCP أو RIB','danger'); return redirect(url_for('affiliate_commissions'))
        if amount<=0 or amount>total_available:
            flash('قيمة السحب غير صالحة (تحقق من الرصيد والعلاوة).','danger'); return redirect(url_for('affiliate_commissions'))
        if amount<WITHDRAW_MIN and total_available>=WITHDRAW_MIN:
            flash(f'الحد الأدنى للسحب {WITHDRAW_MIN:.0f} دج','danger'); return redirect(url_for('affiliate_commissions'))
        conn=get_db()
        # صرف العلاوة إن وُجدت وغير مسجلة هذا الأسبوع
        awarded = award_weekly_bonus_if_any(conn, session['user_id'], bonus_pending)
        pg_exec(conn, """INSERT INTO withdrawals(affiliate_id,amount,method,details,status,bonus,created_at)
                         VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (session['user_id'], amount, method, details, 'requested', awarded, datetime.now(timezone.utc).isoformat()))
        conn.commit(); conn.close()
        flash(f'تم إرسال طلب السحب. العلاوة المضافة لهذا الأسبوع: {awarded:.0f} دج','success')
        return redirect(url_for('affiliate_commissions'))
    return render_template('affiliate/commissions.html', balance=bal, min_withdraw=WITHDRAW_MIN, bonus_pending=bonus_pending)

@app.route('/affiliate/settings', methods=['GET','POST'])
@login_required(role='affiliate')
def affiliate_settings():
    if request.method == 'POST':
        current = request.form.get('current_password','')
        new1 = request.form.get('new_password','')
        new2 = request.form.get('confirm_password','')
        if not new1 or len(new1)<6 or new1!=new2:
            flash('تحقق من كلمة السر الجديدة (≥6 ومطابقة).','danger'); return redirect(url_for('affiliate_settings'))
        conn=get_db(); cur=pg_exec(conn,"SELECT * FROM users WHERE id=%s",(session['user_id'],))
        u=cur.fetchone()
        if not u or not check_password_hash(u['password_hash'], current):
            cur.close(); conn.close(); flash('كلمة السر الحالية غير صحيحة.','danger'); return redirect(url_for('affiliate_settings'))
        pg_exec(conn,"UPDATE users SET password_hash=%s WHERE id=%s",(generate_password_hash(new1),session['user_id']))
        conn.commit(); conn.close(); flash('تم تغيير كلمة السر.','success'); return redirect(url_for('affiliate_settings'))
    return render_template('affiliate/settings.html')

# ---------------- Admin ----------------
def admin_required(f): return login_required(role='admin')(f)

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn=get_db()
    cur=pg_exec(conn,"SELECT COUNT(*) AS n FROM orders"); orders_total=cur.fetchone()['n']
    cur=pg_exec(conn,"SELECT COUNT(*) AS n FROM orders WHERE status='delivered'"); delivered=cur.fetchone()['n']
    cur=pg_exec(conn,"SELECT COUNT(*) AS n FROM orders WHERE status='canceled'"); canceled=cur.fetchone()['n']
    cur=pg_exec(conn,"SELECT COUNT(*) AS n FROM orders WHERE status='pending'"); pending=cur.fetchone()['n']
    stats={'orders_total':orders_total,'delivered':delivered,'canceled':canceled,'pending':pending}
    cur=pg_exec(conn, """
        SELECT o.*, p.name AS product_name, p.image_path, p.price, p.commission, u.name AS affiliate_name
        FROM orders o JOIN products p ON p.id=o.product_id
        JOIN users u ON u.id=o.affiliate_id
        ORDER BY o.id DESC LIMIT 20
    """)
    latest_orders=cur.fetchall()
    cur=pg_exec(conn, """
        SELECT w.*, u.name AS affiliate_name, u.email
        FROM withdrawals w JOIN users u ON u.id=w.affiliate_id
        WHERE w.status='requested' ORDER BY w.id DESC
    """)
    pending_withdraws=cur.fetchall()
    conn.close()
    return render_template('admin/dashboard.html', stats=stats, latest_orders=latest_orders, pending_withdraws=pending_withdraws)

@app.route('/admin/affiliates')
@admin_required
def admin_affiliates():
    conn=get_db()
    cur=pg_exec(conn,"SELECT * FROM users WHERE role='affiliate' AND approved=0 ORDER BY id DESC")
    pending=cur.fetchall()
    cur=pg_exec(conn,"SELECT * FROM users WHERE role='affiliate' AND approved=1 ORDER BY id DESC")
    approved=cur.fetchall()
    conn.close()
    return render_template('admin/affiliates.html', pending=pending, approved=approved)

@app.route('/admin/affiliates/<int:uid>/set', methods=['POST'])
@admin_required
def admin_affiliate_set(uid):
    action=request.form.get('action')
    if action not in ('approve','disable'):
        flash('إجراء غير صالح','danger'); return redirect(url_for('admin_affiliates'))
    conn=get_db()
    pg_exec(conn,"UPDATE users SET approved=%s WHERE id=%s",(1 if action=='approve' else 0, uid))
    conn.commit(); conn.close()
    flash('تم تحديث حالة المسوق','success'); return redirect(url_for('admin_affiliates'))

@app.route('/admin/products')
@admin_required
def admin_products():
    conn=get_db()
    cur=pg_exec(conn,"SELECT * FROM categories ORDER BY name ASC"); cats=cur.fetchall()
    cur=pg_exec(conn, """SELECT p.*, c.name AS category_name FROM products p
                         LEFT JOIN categories c ON c.id=p.category_id
                         ORDER BY p.id DESC""")
    products=cur.fetchall(); conn.close()
    return render_template('admin/products.html', products=products, categories=cats)

@app.route('/admin/categories/add', methods=['POST'])
@admin_required
def admin_category_add():
    name=request.form.get('name','').strip()
    if not name: flash('أدخل اسم التصنيف','danger'); return redirect(url_for('admin_products'))
    conn=get_db()
    try:
        pg_exec(conn,"INSERT INTO categories(name) VALUES(%s)",(name,))
        conn.commit(); flash('تمت إضافة التصنيف','success')
    except psycopg2.Error:
        flash('التصنيف موجود مسبقًا','warning')
    finally:
        conn.close()
    return redirect(url_for('admin_products'))

@app.route('/admin/products/add', methods=['POST'])
@admin_required
def admin_products_add():
    name=request.form.get('name','').strip()
    price=float(request.form.get('price','0') or 0)
    commission=float(request.form.get('commission','0') or 0)
    delivery_price=float(request.form.get('delivery_price','0') or 0)
    description=request.form.get('description','').strip()
    category_id=request.form.get('category_id', type=int)
    delivery_mode=request.form.get('delivery_mode')
    notes=request.form.get('notes','').strip()
    main_image=request.files.get('image')
    extra_images=request.files.getlist('images[]')
    if not name or price<=0 or commission<0 or delivery_price<0 or delivery_mode not in ('home','office'):
        flash('تحقق من الحقول','danger'); return redirect(url_for('admin_products'))
    main_path=save_file(main_image) or 'static/img/placeholder.svg'
    conn=get_db(); cur=conn.cursor()
    cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,category_id,delivery_mode,notes,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (name,description,price,commission,delivery_price,main_path,category_id,delivery_mode,notes,datetime.now(timezone.utc).isoformat()))
    pid=cur.fetchone()['id']
    for f in extra_images:
        p=save_file(f)
        if p: cur.execute("""INSERT INTO product_images(product_id,image_path,created_at)
                              VALUES(%s,%s,%s)""",(pid,p,datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    flash('تمت إضافة المنتج','success'); return redirect(url_for('admin_products'))

@app.route('/admin/products/new')
@admin_required
def admin_product_new():
    conn=get_db()
    cur=pg_exec(conn,"SELECT * FROM categories ORDER BY name ASC")
    cats=cur.fetchall(); conn.close()
    return render_template('admin/product_form.html', p=None, categories=cats)

@app.route('/admin/products/<int:pid>/edit', methods=['GET','POST'])
@admin_required
def admin_product_edit(pid):
    conn=get_db()
    cur=pg_exec(conn,"SELECT * FROM products WHERE id=%s",(pid,))
    p=cur.fetchone()
    if not p:
        conn.close(); abort(404)

    if request.method=='POST':
        name=request.form.get('name','').strip()
        description=request.form.get('description','').strip()
        price=float(request.form.get('price','0') or 0)
        commission=float(request.form.get('commission','0') or 0)
        delivery_price=float(request.form.get('delivery_price','0') or 0)
        category_id=request.form.get('category_id', type=int)
        delivery_mode=request.form.get('delivery_mode')
        notes=request.form.get('notes','').strip()
        main_image=request.files.get('image')
        extra_images=request.files.getlist('images[]')

        if not name or price<=0 or commission<0 or delivery_price<0 or delivery_mode not in ('home','office'):
            conn.close()
            flash('تحقق من الحقول','danger')
            return redirect(url_for('admin_product_edit', pid=pid))

        if main_image and main_image.filename:
            main_path=save_file(main_image)
        else:
            main_path=p['image_path']

        cur2=conn.cursor()
        cur2.execute("""UPDATE products
                        SET name=%s, description=%s, price=%s, commission=%s, delivery_price=%s,
                            image_path=%s, category_id=%s, delivery_mode=%s, notes=%s
                        WHERE id=%s""",
                     (name,description,price,commission,delivery_price,main_path,category_id,delivery_mode,notes,pid))

        for f in extra_images:
            path=save_file(f)
            if path:
                cur2.execute("""INSERT INTO product_images(product_id,image_path,created_at)
                                VALUES(%s,%s,%s)""",(pid,path,datetime.now(timezone.utc).isoformat()))
        conn.commit(); conn.close()
        flash('تم تعديل المنتج','success')
        return redirect(url_for('admin_products'))

    cur_c=pg_exec(conn,"SELECT * FROM categories ORDER BY name ASC"); cats=cur_c.fetchall()
    conn.close()
    return render_template('admin/product_form.html', p=p, categories=cats)

@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_products_delete(pid):
    conn=get_db()
    pg_exec(conn,"DELETE FROM products WHERE id=%s",(pid,))
    conn.commit(); conn.close()
    flash('تم حذف المنتج','info'); return redirect(url_for('admin_products'))

@app.route('/admin/pages', methods=['GET','POST'])
@admin_required
def admin_pages():
    conn=get_db()
    if request.method=='POST':
        slug=request.form.get('slug')
        title=request.form.get('title','').strip()
        content=request.form.get('content','').strip()
        if slug not in ('privacy','about','contact') or not title:
            flash('تحقق من البيانات','danger'); return redirect(url_for('admin_pages'))
        pg_exec(conn,"UPDATE pages SET title=%s, content=%s WHERE slug=%s",(title,content,slug))
        conn.commit(); flash('تم حفظ الصفحة','success')
    cur=pg_exec(conn,"SELECT * FROM pages ORDER BY slug")
    pages=cur.fetchall(); conn.close()
    return render_template('admin/pages.html', pages=pages)

@app.route('/admin/withdrawals/<int:wid>/set', methods=['POST'])
@admin_required
def admin_withdraw_set(wid):
    status=request.form.get('status')
    if status not in ('approved','rejected'):
        flash('إجراء غير صالح','danger'); return redirect(url_for('admin_dashboard'))
    conn=get_db()
    pg_exec(conn,"UPDATE withdrawals SET status=%s WHERE id=%s",(status,wid))
    conn.commit(); conn.close()
    flash('تم تحديث طلب السحب','success'); return redirect(url_for('admin_dashboard'))

@app.route('/admin/settings', methods=['GET','POST'])
@admin_required
def admin_settings():
    conn=get_db()
    if request.method=='POST':
        new_email=request.form.get('email','').strip().lower()
        new_pass=request.form.get('password','').strip()
        cur=pg_exec(conn,"SELECT * FROM users WHERE role='admin' LIMIT 1"); admin_user=cur.fetchone()
        if admin_user and new_email:
            if new_pass: pg_exec(conn,"UPDATE users SET email=%s, password_hash=%s WHERE id=%s",
                                 (new_email,generate_password_hash(new_pass),admin_user['id']))
            else: pg_exec(conn,"UPDATE users SET email=%s WHERE id=%s",(new_email,admin_user['id']))
            conn.commit(); flash('تم حفظ الإعدادات','success')
        else:
            flash('الإيميل مطلوب','danger')

    # البيانات للعرض
    cur=pg_exec(conn,"SELECT id,name,email,approved,phone,created_at FROM users WHERE role='affiliate' ORDER BY id DESC")
    affiliates=cur.fetchall()
    cur=pg_exec(conn,"SELECT id,name,email FROM users WHERE role='admin' LIMIT 1")
    admin_user=cur.fetchone()
    conn.close()
    return render_template('admin/settings.html', admin_user=admin_user, affiliates=affiliates)

# ---------------- Main ----------------
if __name__ == "__main__":
    app.run(debug=True)
