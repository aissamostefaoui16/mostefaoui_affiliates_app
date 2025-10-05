import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# --- Postgres ---
import psycopg2
import psycopg2.extras

# --- Cloudinary ---
import cloudinary
import cloudinary.uploader

APP_NAME = "Mostefaoui DZShop Affiliates (PG + Cloudinary)"
WITHDRAW_MIN = 5000.0  # DZD

# مجلد محلي يُستخدم فقط عند عدم توفر Cloudinary (للتجربة محليًا)
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'}

# بيئات
DATABASE_URL = os.environ.get("DATABASE_URL")  # من Render → Postgres → (Internal أو External URL)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Add it in Render → Environment.")

# Cloudinary: مفعّل إذا المتغير موجود
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()
USE_CLOUDINARY = bool(CLOUDINARY_URL)
if USE_CLOUDINARY:
    # يكفي وضع CLOUDINARY_URL، لكن نؤكّد بالضبط
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- Helpers ----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_file(fs):
    """
    تحفظ الصورة في Cloudinary إن كان مفعّل،
    وإلا تحفظها محليًا في static/uploads (للاستخدام المحلي فقط).
    ترجع مسار/رابط صالح للتخزين في قاعدة البيانات.
    """
    if not fs or not allowed_file(fs.filename):
        return None

    if USE_CLOUDINARY:
        # نرفع الصورة إلى مجلد products داخل حسابك
        # يمكن إضافة تحكمات لاحقة: public_id، tags، الخ.
        result = cloudinary.uploader.upload(
            fs,
            folder="dzshop/products",           # مجلد من اختيارك
            resource_type="image",              # نوع الملف
            use_filename=True, unique_filename=True
            # يمكنك إضافة تحويلات افتراضية هنا إن حبيت
            # transformation=[{"quality": "auto", "fetch_format": "auto"}]
        )
        # نخزن الرابط الآمن (HTTPS). هذا يبقى دائمًا.
        return result.get('secure_url')  # مثال: https://res.cloudinary.com/<cloud>/image/upload/v.../file.png

    # حفظ محلي (للاستعمال خارج Render أو عند غياب CLOUDINARY_URL)
    filename = secure_filename(fs.filename)
    base, ext = os.path.splitext(filename)
    uniq = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    filename = f"{base}_{uniq}{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fs.save(path)
    return path.replace("\\", "/")  # توحيد الفواصل

@app.context_processor
def inject_helpers():
    def static_url(path):
        # صورة افتراضية إذا لا يوجد مسار
        if not path or path.strip() == "":
            return url_for('static', filename='img/placeholder.svg')
        # لو المسار محلي محفوظ "static/..." نحوله إلى url_for
        if path.startswith('static/'):
            return url_for('static', filename=path.split('static/', 1)[1])
        # لو رابط خارجي (Cloudinary)، نعيده كما هو
        if path.startswith('http://') or path.startswith('https://'):
            return path
        return path
    return dict(static_url=static_url)

# -------------- DB (Postgres) --------------
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn

def pg_exec(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # جداول
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('affiliate','admin')),
            approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            price NUMERIC NOT NULL,
            commission NUMERIC NOT NULL,
            delivery_price NUMERIC NOT NULL,
            image_path TEXT,
            created_at TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_images(
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id),
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals(
            id SERIAL PRIMARY KEY,
            affiliate_id INTEGER NOT NULL REFERENCES users(id),
            amount NUMERIC NOT NULL,
            method TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('requested','approved','rejected')),
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()

    # Seed admin
    cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    admin = cur.fetchone()
    if not admin:
        admin_pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
        cur.execute("""INSERT INTO users(name,email,password_hash,role,approved,created_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    ('Admin', 'admin@local', generate_password_hash(admin_pwd), 'admin', 1, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    # Seed sample product
    cur.execute("SELECT COUNT(*) AS n FROM products")
    pcount = cur.fetchone()['n']
    if pcount == 0:
        cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,created_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    ('مثال: خلاط مطبخ', 'وصف موجز للمنتج.', 12990, 800, 700,
                     'static/img/placeholder.svg', datetime.now(timezone.utc).isoformat()))
        conn.commit()

    cur.close()
    conn.close()

# نضمن التهيئة عند تحميل التطبيق مع Gunicorn (Render)
init_db()

# -------------- Auth helpers --------------
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
    if 'user_id' not in session:
        return None
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM users WHERE id=%s", (session['user_id'],))
    u = cur.fetchone()
    cur.close(); conn.close()
    return u

# -------------- Auth routes --------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not name or not email or not password:
            flash('الرجاء ملء جميع الحقول', 'danger')
            return redirect(url_for('register'))
        conn = get_db()
        try:
            pg_exec(conn, """INSERT INTO users(name,email,password_hash,role,approved,created_at)
                             VALUES(%s,%s,%s,%s,%s,%s)""",
                    (name, email, generate_password_hash(password), 'affiliate', 0, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            flash('تم التسجيل بنجاح. حسابك بانتظار موافقة الإدارة.', 'success')
            return redirect(url_for('login'))
        except psycopg2.Error:
            flash('الإيميل مستخدم مسبقًا', 'danger')
        finally:
            conn.close()
    return render_template('register.html', app_name=APP_NAME)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_db()
        cur = pg_exec(conn, "SELECT * FROM users WHERE email=%s", (email,))
        u = cur.fetchone()
        cur.close(); conn.close()
        if u and check_password_hash(u['password_hash'], password):
            if u['role'] == 'affiliate' and (not u['approved']):
                flash('حسابك بانتظار موافقة الإدارة', 'warning')
                return redirect(url_for('login'))
            session['user_id'] = u['id']
            session['role'] = u['role']
            flash('تم تسجيل الدخول', 'success')
            return redirect(url_for('admin_dashboard' if u['role']=='admin' else 'affiliate_products'))
        flash('بيانات الدخول غير صحيحة', 'danger')
    return render_template('login.html', app_name=APP_NAME)

@app.route('/logout')
def logout():
    session.clear()
    flash('تم تسجيل الخروج', 'info')
    return redirect(url_for('login'))

# -------------- Affiliate --------------
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('affiliate_products' if session.get('role')=='affiliate' else 'admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/affiliate/products')
@login_required(role='affiliate')
def affiliate_products():
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM products ORDER BY id DESC")
    products = cur.fetchall()
    cur.close(); conn.close()
    return render_template('affiliate/products.html', products=products, app_name=APP_NAME)

@app.route('/affiliate/product/<int:pid>')
@login_required(role='affiliate')
def affiliate_product_detail(pid):
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM products WHERE id=%s", (pid,))
    p = cur.fetchone()
    if not p:
        cur.close(); conn.close(); abort(404)
    cur2 = pg_exec(conn, "SELECT image_path FROM product_images WHERE product_id=%s ORDER BY id DESC", (pid,))
    imgs = cur2.fetchall()
    cur.close(); cur2.close(); conn.close()
    return render_template('affiliate/product_detail.html', p=p, images=imgs, app_name=APP_NAME)

@app.route('/affiliate/order/<int:pid>', methods=['GET','POST'])
@login_required(role='affiliate')
def affiliate_order(pid):
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM products WHERE id=%s", (pid,))
    p = cur.fetchone()
    if not p:
        cur.close(); conn.close(); abort(404)
    if request.method == 'POST':
        customer_name = request.form.get('customer_name','').strip()
        customer_phone = request.form.get('customer_phone','').strip()
        customer_address = request.form.get('customer_address','').strip()
        if not customer_name or not customer_phone or not customer_address:
            flash('املأ جميع حقول الزبون', 'danger')
            return redirect(url_for('affiliate_order', pid=pid))
        pg_exec(conn, """INSERT INTO orders(product_id,affiliate_id,customer_name,customer_phone,customer_address,status,created_at)
                         VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (pid, session['user_id'], customer_name, customer_phone, customer_address, 'pending', datetime.now(timezone.utc).isoformat()))
        conn.commit()
        cur.close(); conn.close()
        flash('تم إنشاء الطلبية وستظهر في لوحة الإدارة', 'success')
        return redirect(url_for('affiliate_orders'))
    cur.close(); conn.close()
    return render_template('affiliate/order_form.html', p=p, app_name=APP_NAME)

@app.route('/affiliate/orders')
@login_required(role='affiliate')
def affiliate_orders():
    conn = get_db()
    cur = pg_exec(conn, """
        SELECT o.*, p.name AS product_name, p.price, p.commission, p.image_path
        FROM orders o JOIN products p ON p.id=o.product_id
        WHERE o.affiliate_id=%s
        ORDER BY o.id DESC
    """, (session['user_id'],))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template('affiliate/orders.html', rows=rows, app_name=APP_NAME)

def calc_affiliate_balance(affiliate_id):
    conn = get_db()
    cur = pg_exec(conn, """
        SELECT COALESCE(SUM(p.commission),0) AS total
        FROM orders o JOIN products p ON p.id=o.product_id
        WHERE o.affiliate_id=%s AND o.status='delivered'
    """, (affiliate_id,))
    earned = cur.fetchone()['total']
    cur2 = pg_exec(conn, """
        SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals
        WHERE affiliate_id=%s AND status IN ('requested','approved')
    """, (affiliate_id,))
    requested = cur2.fetchone()['total']
    cur.close(); cur2.close(); conn.close()
    return float(earned) - float(requested)

@app.route('/affiliate/commissions', methods=['GET','POST'])
@login_required(role='affiliate')
def affiliate_commissions():
    bal = calc_affiliate_balance(session['user_id'])
    if request.method == 'POST':
        method = request.form.get('method')
        details = request.form.get('details','').strip()
        try:
            amount = float(request.form.get('amount','0') or 0)
        except:
            amount = 0
        if method not in ('ccp','rib'):
            flash('اختر وسيلة السحب (CCP أو RIB)', 'danger')
            return redirect(url_for('affiliate_commissions'))
        if amount <= 0 or amount > bal:
            flash('قيمة السحب غير صالحة', 'danger')
            return redirect(url_for('affiliate_commissions'))
        if amount < WITHDRAW_MIN:
            flash(f'الحد الأدنى للسحب هو {WITHDRAW_MIN:.0f} دج', 'danger')
            return redirect(url_for('affiliate_commissions'))
        conn = get_db()
        pg_exec(conn, """INSERT INTO withdrawals(affiliate_id,amount,method,details,status,created_at)
                         VALUES(%s,%s,%s,%s,%s,%s)""",
                (session['user_id'], amount, method, details, 'requested', datetime.now(timezone.utc).isoformat()))
        conn.commit(); conn.close()
        flash('تم إرسال طلب السحب وسيصلك إشعار بعد المعالجة', 'success')
        return redirect(url_for('affiliate_commissions'))
    return render_template('affiliate/commissions.html', balance=bal, min_withdraw=WITHDRAW_MIN, app_name=APP_NAME)

# -------------- Admin --------------
def admin_required(f):
    return login_required(role='admin')(f)

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    cur = pg_exec(conn, "SELECT COUNT(*) AS n FROM orders"); orders_total = cur.fetchone()['n']
    cur = pg_exec(conn, "SELECT COUNT(*) AS n FROM orders WHERE status='delivered'"); delivered = cur.fetchone()['n']
    cur = pg_exec(conn, "SELECT COUNT(*) AS n FROM orders WHERE status='canceled'"); canceled = cur.fetchone()['n']
    cur = pg_exec(conn, "SELECT COUNT(*) AS n FROM orders WHERE status='pending'"); pending = cur.fetchone()['n']
    stats = {'orders_total': orders_total, 'delivered': delivered, 'canceled': canceled, 'pending': pending}
    cur = pg_exec(conn, """
        SELECT o.*, p.name AS product_name, p.image_path, p.price, p.commission, u.name AS affiliate_name
        FROM orders o JOIN products p ON p.id=o.product_id
        JOIN users u ON u.id=o.affiliate_id
        ORDER BY o.id DESC LIMIT 20
    """)
    latest_orders = cur.fetchall()
    cur = pg_exec(conn, """
        SELECT w.*, u.name AS affiliate_name, u.email
        FROM withdrawals w JOIN users u ON u.id=w.affiliate_id
        WHERE w.status='requested' ORDER BY w.id DESC
    """)
    pending_withdraws = cur.fetchall()
    conn.close()
    return render_template('admin/dashboard.html', stats=stats, latest_orders=latest_orders, pending_withdraws=pending_withdraws, app_name=APP_NAME)

@app.route('/admin/orders/<int:oid>/status', methods=['POST'])
@admin_required
def admin_update_order_status(oid):
    status = request.form.get('status')
    if status not in ('pending','delivered','canceled'):
        flash('حالة غير صالحة', 'danger'); return redirect(url_for('admin_dashboard'))
    conn = get_db()
    pg_exec(conn, "UPDATE orders SET status=%s WHERE id=%s", (status, oid))
    conn.commit(); conn.close()
    flash('تم تحديث الحالة', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/affiliates')
@admin_required
def admin_affiliates():
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM users WHERE role='affiliate' AND approved=0 ORDER BY id DESC")
    pending = cur.fetchall()
    cur = pg_exec(conn, "SELECT * FROM users WHERE role='affiliate' AND approved=1 ORDER BY id DESC")
    approved = cur.fetchall()
    conn.close()
    return render_template('admin/affiliates.html', pending=pending, approved=approved, app_name=APP_NAME)

@app.route('/admin/affiliates/<int:uid>/set', methods=['POST'])
@admin_required
def admin_affiliate_set(uid):
    action = request.form.get('action')
    if action not in ('approve','disable'):
        flash('إجراء غير صالح', 'danger'); return redirect(url_for('admin_affiliates'))
    conn = get_db()
    pg_exec(conn, "UPDATE users SET approved=%s WHERE id=%s", (1 if action=='approve' else 0, uid))
    conn.commit(); conn.close()
    flash('تم تحديث حالة المسوّق', 'success')
    return redirect(url_for('admin_affiliates'))

@app.route('/admin/products')
@admin_required
def admin_products():
    conn = get_db()
    cur = pg_exec(conn, "SELECT * FROM products ORDER BY id DESC")
    products = cur.fetchall()
    conn.close()
    return render_template('admin/products.html', products=products, app_name=APP_NAME)

@app.route('/admin/products/add', methods=['POST'])
@admin_required
def admin_products_add():
    name = request.form.get('name','').strip()
    price = float(request.form.get('price','0') or 0)
    commission = float(request.form.get('commission','0') or 0)
    delivery_price = float(request.form.get('delivery_price','0') or 0)
    description = request.form.get('description','').strip()
    main_image = request.files.get('image')
    extra_images = request.files.getlist('images[]')
    if not name or price <= 0 or commission < 0 or delivery_price < 0:
        flash('تحقق من الحقول', 'danger'); return redirect(url_for('admin_products'))

    main_path = save_file(main_image) or 'static/img/placeholder.svg'
    conn = get_db(); cur = conn.cursor()
    cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (name, description, price, commission, delivery_price, main_path, datetime.now(timezone.utc).isoformat()))
    pid = cur.fetchone()['id']

    for f in extra_images:
        p = save_file(f)
        if p:
            cur.execute("""INSERT INTO product_images(product_id,image_path,created_at) VALUES(%s,%s,%s)""",
                        (pid, p, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    flash('تمت إضافة المنتج', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_products_delete(pid):
    conn = get_db()
    pg_exec(conn, "DELETE FROM products WHERE id=%s", (pid,))
    conn.commit(); conn.close()
    flash('تم حذف المنتج', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/withdrawals/<int:wid>/set', methods=['POST'])
@admin_required
def admin_withdraw_set(wid):
    status = request.form.get('status')
    if status not in ('approved','rejected'):
        flash('إجراء غير صالح', 'danger'); return redirect(url_for('admin_dashboard'))
    conn = get_db()
    pg_exec(conn, "UPDATE withdrawals SET status=%s WHERE id=%s", (status, wid))
    conn.commit(); conn.close()
    flash('تم تحديث طلب السحب', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/settings', methods=['GET','POST'])
@admin_required
def admin_settings():
    conn = get_db()
    if request.method == 'POST':
        new_email = request.form.get('email','').strip().lower()
        new_pass = request.form.get('password','').strip()
        cur = pg_exec(conn, "SELECT * FROM users WHERE role='admin' LIMIT 1")
        admin_user = cur.fetchone()
        if admin_user and new_email:
            if new_pass:
                pg_exec(conn, "UPDATE users SET email=%s, password_hash=%s WHERE id=%s",
                        (new_email, generate_password_hash(new_pass), admin_user['id']))
            else:
                pg_exec(conn, "UPDATE users SET email=%s WHERE id=%s",
                        (new_email, admin_user['id']))
            conn.commit()
            flash('تم حفظ الإعدادات', 'success')
        else:
            flash('الإيميل مطلوب', 'danger')
    cur = pg_exec(conn, "SELECT id,name,email,approved,created_at FROM users WHERE role='affiliate' ORDER BY id DESC")
    affiliates = cur.fetchall()
    cur = pg_exec(conn, "SELECT id,name,email FROM users WHERE role='admin' LIMIT 1")
    admin_user = cur.fetchone()
    conn.close()
    return render_template('admin/settings.html', admin_user=admin_user, affiliates=affiliates, app_name=APP_NAME)

# ملاحظة: لا نستخدم app.run هنا؛ Gunicorn سيشغّل app مباشرة عبر Start Command
