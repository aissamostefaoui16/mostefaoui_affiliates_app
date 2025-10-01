import os
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

APP_NAME = "Mostefaoui DZShop Affiliates"
WITHDRAW_MIN = 5000.0  # DZD
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
DB_PATH = os.environ.get('DB_PATH', 'app.db')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- Utils ----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_file(fs):
    if fs and allowed_file(fs.filename):
        filename = secure_filename(fs.filename)
        base, ext = os.path.splitext(filename)
        uniq = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        filename = f"{base}_{uniq}{ext}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        fs.save(path)
        # نخلي المسار بـ / مهما كان النظام
        return path.replace("\\", "/")
    return None

# Helper لعمل رابط صحيح للصور
@app.context_processor
def inject_helpers():
    def static_url(path):
        # صورة افتراضية إذا مافيش
        if not path or path.strip() == "":
            return url_for('static', filename='img/placeholder.svg')
        # لو مخزّن "static/..." حوّلها إلى url_for('static', filename=...)
        if path.startswith('static/'):
            return url_for('static', filename=path.split('static/', 1)[1])
        # لو رابط خارجي كامل http(s):// اتركه كما هو
        return path
    return dict(static_url=static_url)

# ---------------- DB ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('affiliate','admin')),
            approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            commission REAL NOT NULL,
            delivery_price REAL NOT NULL,
            image_path TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_images(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            affiliate_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            customer_address TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','delivered','canceled')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(affiliate_id) REFERENCES users(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('requested','approved','rejected')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(affiliate_id) REFERENCES users(id)
        )
    ''')
    conn.commit()

    # Seed admin
    admin_pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
    admin = c.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin:
        c.execute("INSERT INTO users(name,email,password_hash,role,approved,created_at) VALUES(?,?,?,?,?,?)",
                  ('Admin', 'admin@local', generate_password_hash(admin_pwd), 'admin', 1, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    # Seed sample product
    pcount = c.execute("SELECT COUNT(*) as n FROM products").fetchone()['n']
    if pcount == 0:
        c.execute('''INSERT INTO products(name,description,price,commission,delivery_price,image_path,created_at)
                     VALUES(?,?,?,?,?,?,?)''',
                  ('مثال: خلاط مطبخ', 'وصف موجز للمنتج.', 12990, 800, 700, 'static/img/placeholder.svg', datetime.now(timezone.utc).isoformat()))
        conn.commit()
    conn.close()

# نضمن إنشاء الجداول حتى مع Gunicorn على Render
init_db()

# ---------------- Auth helpers ----------------
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
    u = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return u

# ---------------- Auth routes ----------------
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
            conn.execute("""INSERT INTO users(name,email,password_hash,role,approved,created_at)
                            VALUES(?,?,?,?,?,?)""",
                         (name, email, generate_password_hash(password), 'affiliate', 0, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            flash('تم التسجيل بنجاح. حسابك بانتظار موافقة الإدارة.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
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
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if u and check_password_hash(u['password_hash'], password):
            if u['role'] == 'affiliate' and (('approved' in u.keys() and not u['approved']) or (u['approved'] == 0)):
                flash('حسابك بانتظار موافقة الإدارة', 'warning')
                return redirect(url_for('login'))
            session['user_id'] = u['id']
            session['role'] = u['role']
            flash('تم تسجيل الدخول', 'success')
            if u['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('affiliate_products'))
        flash('بيانات الدخول غير صحيحة', 'danger')
    return render_template('login.html', app_name=APP_NAME)

@app.route('/logout')
def logout():
    session.clear()
    flash('تم تسجيل الخروج', 'info')
    return redirect(url_for('login'))

# ---------------- Affiliate views ----------------
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('affiliate_products' if session.get('role') == 'affiliate' else 'admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/affiliate/products')
@login_required(role='affiliate')
def affiliate_products():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('affiliate/products.html', products=products, app_name=APP_NAME)

@app.route('/affiliate/product/<int:pid>')
@login_required(role='affiliate')
def affiliate_product_detail(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close(); abort(404)
    imgs = conn.execute("SELECT image_path FROM product_images WHERE product_id=? ORDER BY id DESC", (pid,)).fetchall()
    conn.close()
    return render_template('affiliate/product_detail.html', p=p, images=imgs, app_name=APP_NAME)

@app.route('/affiliate/order/<int:pid>', methods=['GET', 'POST'])
@login_required(role='affiliate')
def affiliate_order(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close(); abort(404)
    if request.method == 'POST':
        customer_name = request.form.get('customer_name', '').strip()
        customer_phone = request.form.get('customer_phone', '').strip()
        customer_address = request.form.get('customer_address', '').strip()
        if not customer_name or not customer_phone or not customer_address:
            flash('املأ جميع حقول الزبون', 'danger')
            return redirect(url_for('affiliate_order', pid=pid))
        conn.execute("""INSERT INTO orders(product_id, affiliate_id, customer_name, customer_phone, customer_address, status, created_at)
                        VALUES(?,?,?,?,?,?,?)""",
                     (pid, session['user_id'], customer_name, customer_phone, customer_address, 'pending', datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        flash('تم إنشاء الطلبية وستظهر في لوحة الإدارة', 'success')
        return redirect(url_for('affiliate_orders'))
    conn.close()
    return render_template('affiliate/order_form.html', p=p, app_name=APP_NAME)

@app.route('/affiliate/orders')
@login_required(role='affiliate')
def affiliate_orders():
    conn = get_db()
    rows = conn.execute("""SELECT o.*, p.name as product_name, p.price, p.commission, p.image_path
                           FROM orders o JOIN products p ON p.id=o.product_id
                           WHERE o.affiliate_id=?
                           ORDER BY o.id DESC""", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('affiliate/orders.html', rows=rows, app_name=APP_NAME)

def calc_affiliate_balance(affiliate_id):
    conn = get_db()
    earned = conn.execute("""SELECT IFNULL(SUM(p.commission),0) as total
                             FROM orders o JOIN products p ON p.id=o.product_id
                             WHERE o.affiliate_id=? AND o.status='delivered' """, (affiliate_id,)).fetchone()['total']
    requested = conn.execute("""SELECT IFNULL(SUM(amount),0) as total FROM withdrawals
                                WHERE affiliate_id=? AND status IN ('requested','approved')""", (affiliate_id,)).fetchone()['total']
    conn.close()
    return earned - requested

@app.route('/affiliate/commissions', methods=['GET', 'POST'])
@login_required(role='affiliate')
def affiliate_commissions():
    bal = calc_affiliate_balance(session['user_id'])
    if request.method == 'POST':
        method = request.form.get('method')
        details = request.form.get('details', '').strip()
        try:
            amount = float(request.form.get('amount', '0') or 0)
        except:
            amount = 0
        if method not in ('ccp', 'rib'):
            flash('اختر وسيلة السحب (CCP أو RIB)', 'danger')
            return redirect(url_for('affiliate_commissions'))
        if amount <= 0 or amount > bal:
            flash('قيمة السحب غير صالحة', 'danger')
            return redirect(url_for('affiliate_commissions'))
        if amount < WITHDRAW_MIN:
            flash(f'الحد الأدنى للسحب هو {WITHDRAW_MIN:.0f} دج', 'danger')
            return redirect(url_for('affiliate_commissions'))
        conn = get_db()
        conn.execute("""INSERT INTO withdrawals(affiliate_id, amount, method, details, status, created_at)
                        VALUES(?,?,?,?,?,?)""",
                     (session['user_id'], amount, method, details, 'requested', datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        flash('تم إرسال طلب السحب وسيصلك إشعار بعد المعالجة', 'success')
        return redirect(url_for('affiliate_commissions'))
    return render_template('affiliate/commissions.html', balance=bal, min_withdraw=WITHDRAW_MIN, app_name=APP_NAME)

# ---------------- Admin views ----------------
def admin_required(f):
    return login_required(role='admin')(f)

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    stats = {}
    stats['orders_total'] = conn.execute("SELECT COUNT(*) as n FROM orders").fetchone()['n']
    stats['delivered'] = conn.execute("SELECT COUNT(*) as n FROM orders WHERE status='delivered'").fetchone()['n']
    stats['canceled'] = conn.execute("SELECT COUNT(*) as n FROM orders WHERE status='canceled'").fetchone()['n']
    stats['pending'] = conn.execute("SELECT COUNT(*) as n FROM orders WHERE status='pending'").fetchone()['n']
    latest_orders = conn.execute("""SELECT o.*, p.name as product_name, p.image_path, p.price, p.commission,
                                    u.name as affiliate_name
                                    FROM orders o
                                    JOIN products p ON p.id=o.product_id
                                    JOIN users u ON u.id=o.affiliate_id
                                    ORDER BY o.id DESC LIMIT 20""").fetchall()
    pending_withdraws = conn.execute("""SELECT w.*, u.name as affiliate_name, u.email
                                        FROM withdrawals w JOIN users u ON u.id=w.affiliate_id
                                        WHERE w.status='requested' ORDER BY w.id DESC""").fetchall()
    conn.close()
    return render_template('admin/dashboard.html', stats=stats, latest_orders=latest_orders, pending_withdraws=pending_withdraws, app_name=APP_NAME)

@app.route('/admin/orders/<int:oid>/status', methods=['POST'])
@admin_required
def admin_update_order_status(oid):
    status = request.form.get('status')
    if status not in ('pending', 'delivered', 'canceled'):
        flash('حالة غير صالحة', 'danger')
        return redirect(url_for('admin_dashboard'))
    conn = get_db()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    conn.commit()
    conn.close()
    flash('تم تحديث الحالة', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/affiliates')
@admin_required
def admin_affiliates():
    conn = get_db()
    pending = conn.execute("SELECT * FROM users WHERE role='affiliate' AND (approved=0)").fetchall()
    approved = conn.execute("SELECT * FROM users WHERE role='affiliate' AND (approved=1)").fetchall()
    conn.close()
    return render_template('admin/affiliates.html', pending=pending, approved=approved, app_name=APP_NAME)

@app.route('/admin/affiliates/<int:uid>/set', methods=['POST'])
@admin_required
def admin_affiliate_set(uid):
    action = request.form.get('action')
    if action not in ('approve','disable'):
        flash('إجراء غير صالح', 'danger')
        return redirect(url_for('admin_affiliates'))
    conn = get_db()
    conn.execute("UPDATE users SET approved=? WHERE id=?", (1 if action=='approve' else 0, uid))
    conn.commit()
    conn.close()
    flash('تم تحديث حالة المسوّق', 'success')
    return redirect(url_for('admin_affiliates'))

@app.route('/admin/products')
@admin_required
def admin_products():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('admin/products.html', products=products, app_name=APP_NAME)

@app.route('/admin/products/add', methods=['POST'])
@admin_required
def admin_products_add():
    name = request.form.get('name', '').strip()
    price = float(request.form.get('price', '0') or 0)
    commission = float(request.form.get('commission', '0') or 0)
    delivery_price = float(request.form.get('delivery_price', '0') or 0)
    description = request.form.get('description', '').strip()
    main_image = request.files.get('image')          # uploaded file
    extra_images = request.files.getlist('images[]') # multiple files

    if not name or price <= 0 or commission < 0 or delivery_price < 0:
        flash('تحقق من الحقول', 'danger')
        return redirect(url_for('admin_products'))

    main_path = save_file(main_image) or 'static/img/placeholder.svg'
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO products(name,description,price,commission,delivery_price,image_path,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (name, description, price, commission, delivery_price, main_path, datetime.now(timezone.utc).isoformat()))
    pid = cur.lastrowid

    for f in extra_images:
        p = save_file(f)
        if p:
            cur.execute("""INSERT INTO product_images(product_id,image_path,created_at) VALUES(?,?,?)""",
                        (pid, p, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    flash('تمت إضافة المنتج', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_products_delete(pid):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    flash('تم حذف المنتج', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/withdrawals/<int:wid>/set', methods=['POST'])
@admin_required
def admin_withdraw_set(wid):
    status = request.form.get('status')
    if status not in ('approved', 'rejected'):
        flash('إجراء غير صالح', 'danger')
        return redirect(url_for('admin_dashboard'))
    conn = get_db()
    conn.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, wid))
    conn.commit()
    conn.close()
    flash('تم تحديث طلب السحب', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    conn = get_db()
    if request.method == 'POST':
        new_email = request.form.get('email', '').strip().lower()
        new_pass = request.form.get('password', '').strip()
        if not new_email:
            flash('الإيميل مطلوب', 'danger')
        else:
            admin_user = conn.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone()
            if admin_user:
                if new_pass:
                    conn.execute("UPDATE users SET email=?, password_hash=? WHERE id=?",
                                 (new_email, generate_password_hash(new_pass), admin_user['id']))
                else:
                    conn.execute("UPDATE users SET email=? WHERE id=?",
                                 (new_email, admin_user['id']))
                conn.commit()
                flash('تم حفظ الإعدادات', 'success')
            else:
                flash('لا يوجد مستخدم أدمن', 'danger')

    affiliates = conn.execute("SELECT id,name,email,approved,created_at FROM users WHERE role='affiliate' ORDER BY id DESC").fetchall()
    admin_user = conn.execute("SELECT id,name,email FROM users WHERE role='admin' LIMIT 1").fetchone()
    conn.close()
    return render_template('admin/settings.html', admin_user=admin_user, affiliates=affiliates, app_name=APP_NAME)

# ------------- main -------------
if __name__ == '__main__':
    # مفيدة للتشغيل المحلي
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, host=host, port=port)
