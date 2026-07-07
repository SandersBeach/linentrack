import os, json, qrcode, io, base64, random, string, urllib.request
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
import pytz

app = Flask(__name__, static_folder='public', static_url_path='')
CENTRAL = pytz.timezone('America/Chicago')

WAREHOUSE_PIN    = os.environ.get('WAREHOUSE_PIN',    '1234')
ADMIN_PIN        = os.environ.get('ADMIN_PIN',        '9999')
MAINTENANCE_PIN  = os.environ.get('MAINTENANCE_PIN',  '5678')
COORDINATOR_PIN  = os.environ.get('COORDINATOR_PIN',  '2468')

ALERT_EMAIL          = 'accountingdepartment@sandersbeachrentals.com'
HOUSEKEEPING_MANAGER = 'cassie@sandersbeachrentals.com'

SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'kristin@sandersbeachrentals.com')

PO_APPROVER_1_EMAIL = 'sabrina@sandersbeachrentals.com'
PO_APPROVER_1_NAME  = 'Sabrina Renshaw'
PO_APPROVER_2_EMAIL = 'sarahelizabeth@sandersbeachrentals.com'
PO_APPROVER_2_NAME  = 'Sarah Jordan'
CHUCK_EMAIL = 'chuck@sandersbeachrentals.com'
CHUCK_NAME  = 'Chuck Howard'
# PO categories that require Chuck's approval first, before going to final approval.
TWO_STAGE_CATEGORIES = {'FL Repairs/Service Calls'}

def now_central():
    return datetime.now(pytz.utc).astimezone(CENTRAL).strftime('%Y-%m-%d %H:%M:%S')

_DB_URL = (os.environ.get('DATABASE_URL') or
           os.environ.get('DATABASE_PUBLIC_URL') or
           'postgresql://postgres:vPzxJamFkEIxprlqLqPLdUgYFDkTZicQ@acela.proxy.rlwy.net:57535/railway')

def get_db():
    db_url = _DB_URL
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(db_url, sslmode='require')

def generate_cleaner_pin(conn):
    """Generate a unique random 5-digit PIN for a cleaner."""
    cur = conn.cursor()
    while True:
        pin = ''.join(random.choices(string.digits, k=5))
        # Make sure it doesn't collide with staff PINs or other cleaner PINs
        if pin in (WAREHOUSE_PIN, ADMIN_PIN, MAINTENANCE_PIN, COORDINATOR_PIN):
            continue
        cur.execute("SELECT COUNT(*) FROM cleaners WHERE pin=%s", (pin,))
        if cur.fetchone()[0] == 0:
            cur.close()
            return pin

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS homes (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS cleaners (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, active INTEGER DEFAULT 1,
            pin TEXT, email TEXT, phone TEXT
        );
        CREATE TABLE IF NOT EXISTS bags (
            id TEXT PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
            status TEXT DEFAULT 'in', cleaner_id INTEGER REFERENCES cleaners(id),
            checked_out TEXT, staged_at TEXT, picked_up_at TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY, bag_id TEXT NOT NULL, home_id INTEGER NOT NULL,
            cleaner_id INTEGER, action TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS loaner_staff (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS loaners (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            status TEXT DEFAULT 'in', home_id INTEGER REFERENCES homes(id),
            staff_id INTEGER REFERENCES loaner_staff(id), checked_out TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS loaner_transactions (
            id SERIAL PRIMARY KEY, loaner_id TEXT NOT NULL, staff_id INTEGER,
            home_id INTEGER, action TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS supply_items (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'General', quantity INTEGER NOT NULL DEFAULT 0,
            low_stock_threshold INTEGER NOT NULL DEFAULT 5, unit TEXT NOT NULL DEFAULT 'units',
            created_at TEXT NOT NULL, qr_code TEXT
        );
        CREATE TABLE IF NOT EXISTS supply_transactions (
            id SERIAL PRIMARY KEY, supply_id INTEGER NOT NULL REFERENCES supply_items(id),
            action TEXT NOT NULL, quantity INTEGER NOT NULL, quantity_after INTEGER NOT NULL,
            performed_by TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory_counts (
            id SERIAL PRIMARY KEY, areas TEXT NOT NULL, started_at TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0, variances INTEGER NOT NULL DEFAULT 0,
            details TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hk_supply_items (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'General', quantity INTEGER NOT NULL DEFAULT 0,
            low_stock_threshold INTEGER NOT NULL DEFAULT 5, unit TEXT NOT NULL DEFAULT 'units',
            created_at TEXT NOT NULL, qr_code TEXT
        );
        CREATE TABLE IF NOT EXISTS hk_supply_transactions (
            id SERIAL PRIMARY KEY, supply_id INTEGER NOT NULL REFERENCES hk_supply_items(id),
            action TEXT NOT NULL, quantity INTEGER NOT NULL, quantity_after INTEGER NOT NULL,
            performed_by TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS supply_orders (
            id SERIAL PRIMARY KEY,
            module TEXT NOT NULL,
            ordered_by TEXT NOT NULL,
            vendor TEXT,
            status TEXT NOT NULL DEFAULT 'Ordered',
            notes TEXT,
            ordered_at TEXT NOT NULL,
            received_at TEXT,
            received_by TEXT,
            has_discrepancy INTEGER DEFAULT 0,
            discrepancy_resolved INTEGER DEFAULT 0,
            discrepancy_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS supply_order_items (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES supply_orders(id),
            item_name TEXT NOT NULL,
            matched_supply_id INTEGER,
            matched_supply_table TEXT,
            cases_ordered NUMERIC(10,2) NOT NULL DEFAULT 1,
            units_per_case NUMERIC(10,2) NOT NULL DEFAULT 1,
            expected_units INTEGER NOT NULL DEFAULT 0,
            received_units INTEGER,
            unit_label TEXT NOT NULL DEFAULT 'units',
            price NUMERIC(10,2),
            line_discrepancy INTEGER DEFAULT 0,
            receive_notes TEXT
        );
        ALTER TABLE supply_order_items ADD COLUMN IF NOT EXISTS receive_notes TEXT;
        CREATE TABLE IF NOT EXISTS staff_members (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            pin TEXT NOT NULL UNIQUE,
            email TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS email TEXT;
        CREATE TABLE IF NOT EXISTS store_items (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'General',
            quantity INTEGER NOT NULL DEFAULT 0,
            price NUMERIC(10,2) DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS store_transactions (
            id SERIAL PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES store_items(id),
            action TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            quantity_after INTEGER NOT NULL DEFAULT 0,
            property_address TEXT,
            performed_by TEXT NOT NULL,
            performed_by_email TEXT,
            transaction_type TEXT NOT NULL DEFAULT 'sold_out',
            expected_return_date TEXT,
            returned_at TEXT,
            is_overdue INTEGER DEFAULT 0,
            overdue_alerted INTEGER DEFAULT 0,
            notes TEXT,
            timestamp TEXT NOT NULL
        );
        ALTER TABLE store_transactions ADD COLUMN IF NOT EXISTS performed_by_email TEXT;
        ALTER TABLE store_transactions ADD COLUMN IF NOT EXISTS expected_return_date TEXT;
        ALTER TABLE store_transactions ADD COLUMN IF NOT EXISTS returned_at TEXT;
        ALTER TABLE store_transactions ADD COLUMN IF NOT EXISTS is_overdue INTEGER DEFAULT 0;
        ALTER TABLE store_transactions ADD COLUMN IF NOT EXISTS overdue_alerted INTEGER DEFAULT 0;
        CREATE TABLE IF NOT EXISTS forecast_pack_list (
            id SERIAL PRIMARY KEY,
            address TEXT NOT NULL UNIQUE,
            property_name TEXT,
            supplies JSONB NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS forecast_reservations (
            id SERIAL PRIMARY KEY,
            lease_id TEXT,
            arrive TEXT NOT NULL,
            depart TEXT NOT NULL,
            unit_address TEXT NOT NULL,
            area TEXT,
            uploaded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS po_requests (
            id SERIAL PRIMARY KEY,
            employee_name TEXT NOT NULL, employee_email TEXT NOT NULL,
            vendor TEXT NOT NULL, amount NUMERIC(10,2) NOT NULL,
            category TEXT NOT NULL, description TEXT NOT NULL,
            date_needed TEXT NOT NULL, urgency TEXT NOT NULL DEFAULT 'Routine',
            status TEXT NOT NULL DEFAULT 'Pending',
            approver_notes TEXT, approved_by TEXT,
            submitted_at TEXT NOT NULL, decided_at TEXT
        );
    """)
    # Safe schema migrations
    for col_sql in [
        "ALTER TABLE supply_items ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'General'",
        "ALTER TABLE supply_items ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT 'units'",
        "ALTER TABLE supply_items ADD COLUMN IF NOT EXISTS qr_code TEXT",
        "ALTER TABLE cleaners ADD COLUMN IF NOT EXISTS pin TEXT",
        "ALTER TABLE cleaners ADD COLUMN IF NOT EXISTS email TEXT",
        "ALTER TABLE cleaners ADD COLUMN IF NOT EXISTS phone TEXT",
        "ALTER TABLE bags ADD COLUMN IF NOT EXISTS staged_at TEXT",
        "ALTER TABLE bags ADD COLUMN IF NOT EXISTS picked_up_at TEXT",
        "ALTER TABLE bags ADD COLUMN IF NOT EXISTS overdue_alerted INTEGER DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS staff_name TEXT",
        "ALTER TABLE loaner_transactions ADD COLUMN IF NOT EXISTS performed_by_name TEXT",
        "ALTER TABLE loaners ADD COLUMN IF NOT EXISTS checked_out_by TEXT",
        "ALTER TABLE loaners ADD COLUMN IF NOT EXISTS checked_out TEXT",
        "ALTER TABLE po_requests ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'final'",
        "ALTER TABLE po_requests ADD COLUMN IF NOT EXISTS stage1_approved_by TEXT",
        "ALTER TABLE po_requests ADD COLUMN IF NOT EXISTS stage1_notes TEXT",
        "ALTER TABLE po_requests ADD COLUMN IF NOT EXISTS stage1_decided_at TEXT",
    ]:
        try: cur.execute(col_sql)
        except Exception as e:
            print(f'Migration note: {e}')
            conn.rollback()
    conn.commit(); cur.close(); conn.close()

# ── Helpers ───────────────────────────────────────────────────────────────────

def check_pin(pin):
    p = str(pin)
    if p == ADMIN_PIN: return 'admin'
    if p == MAINTENANCE_PIN: return 'maintenance'
    if p == WAREHOUSE_PIN: return 'warehouse'
    if p == COORDINATOR_PIN: return 'coordinator'
    return None

def send_email(subject, body, to=ALERT_EMAIL, html_body=None):
    print(f'[EMAIL ATTEMPT] {subject} | key_present={bool(SENDGRID_API_KEY)} | from={FROM_EMAIL}', flush=True)
    if not SENDGRID_API_KEY:
        print(f'[EMAIL SKIPPED - no API key] {subject}', flush=True); return False
    try:
        recipients = [to] if isinstance(to, str) else to
        to_list = [{'email': r} for r in recipients]
        content = [{'type': 'text/plain', 'value': body}]
        if html_body:
            content.append({'type': 'text/html', 'value': html_body})
        payload = json.dumps({
            'personalizations': [{'to': to_list}],
            'from': {'email': FROM_EMAIL, 'name': 'Sanders Beach Rentals'},
            'subject': subject,
            'content': content
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.sendgrid.com/v3/mail/send',
            data=payload,
            headers={
                'Authorization': f'Bearer {SENDGRID_API_KEY}',
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        with urllib.request.urlopen(req) as resp:
            print(f'[EMAIL SENT] {subject} -> {recipients} (status {resp.status})', flush=True)
        return True
    except Exception as e:
        import traceback; print(f'[EMAIL ERROR] {subject}: {e}', flush=True); traceback.print_exc()
        return False

def send_overdue_email(bag, cleaner):
    """Send overdue alert to cleaner + CC housekeeping manager."""
    hours = int((datetime.now(pytz.utc) - datetime.fromisoformat(bag['picked_up_at'].replace(' ', 'T')).replace(tzinfo=pytz.utc)).total_seconds() / 3600)
    subject = f"⚠️ Overdue Linen Bag — {bag['home_name']} ({bag['id']})"
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <div style="background:#D85A30;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">⚠️ Overdue Linen Bag</h2>
        <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals · LinenTrack</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        <p style="color:#444;margin:0 0 16px">Hi {cleaner['name']}, a linen bag assigned to you has not been returned after 24 hours.</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888;width:120px">Bag ID</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600;font-family:monospace">{bag['id']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Home</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600">{bag['home_name']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Picked up</td><td style="padding:8px 0;border-bottom:1px solid #eee">{bag['picked_up_at']}</td></tr>
          <tr><td style="padding:8px 0;color:#888">Hours out</td><td style="padding:8px 0;font-weight:600;color:#D85A30">{hours} hours</td></tr>
        </table>
        <p style="margin:16px 0 0;font-size:13px;color:#666">Please return this bag to the warehouse as soon as possible. If you have already returned it, please let the housekeeping manager know.</p>
        <p style="margin:12px 0 0;font-size:12px;color:#aaa;text-align:center">Sanders Beach Rentals · LinenTrack</p>
      </div>
    </div>"""
    plain = f"""Hi {cleaner['name']},

A linen bag assigned to you is overdue (over 24 hours since pickup).

Bag: {bag['id']}
Home: {bag['home_name']}
Picked up: {bag['picked_up_at']}
Hours out: {hours}

Please return this bag to the warehouse as soon as possible.

Sanders Beach Rentals"""

    recipients = []
    if cleaner.get('email'): recipients.append(cleaner['email'])
    recipients.append(HOUSEKEEPING_MANAGER)
    if recipients:
        return send_email(subject, plain, to=recipients, html_body=html)
    return False

def send_po_approver_email(req):
    urgency_emoji = {'Routine': '📋', 'At Risk': '⚠️', 'Unstayable': '🚨'}.get(req['urgency'], '📋')
    is_chuck_stage = req.get('stage') == 'chuck'
    subject_prefix = "Level 1 Approval Needed — " if is_chuck_stage else ""
    subject = f"{urgency_emoji} {subject_prefix}New PO Request — {req['vendor']} (${req['amount']:.2f})"
    approvals_url = 'https://sbrlinens.up.railway.app/po-approvals'
    stage_note = '<p style="margin:0 0 16px;color:#444;font-weight:600">This is a maintenance repair/service request — your approval is needed before it goes to final sign-off.</p>' if is_chuck_stage else ''
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <div style="background:#95B9B8;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">New Purchase Request</h2>
        <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        {stage_note}
        <p style="margin:0 0 16px;color:#444">A new purchase request has been submitted and needs your approval.</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888;width:140px">Employee</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600">{req['employee_name']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Vendor</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600">{req['vendor']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Amount</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600;font-size:16px">${req['amount']:.2f}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Category</td><td style="padding:8px 0;border-bottom:1px solid #eee">{req['category']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Urgency</td><td style="padding:8px 0;border-bottom:1px solid #eee">{urgency_emoji} {req['urgency']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Date Needed</td><td style="padding:8px 0;border-bottom:1px solid #eee">{req['date_needed']}</td></tr>
          <tr><td style="padding:8px 0;color:#888;vertical-align:top">Description</td><td style="padding:8px 0">{req['description']}</td></tr>
        </table>
        <div style="margin-top:20px;text-align:center">
          <a href="{approvals_url}" style="background:#95B9B8;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;display:inline-block">Review &amp; Approve Request →</a>
        </div>
      </div>
    </div>"""
    plain = f"New PO Request — {req['vendor']} (${req['amount']:.2f})\n\nEmployee: {req['employee_name']}\nVendor: {req['vendor']}\nAmount: ${req['amount']:.2f}\nCategory: {req['category']}\nUrgency: {req['urgency']}\nDate Needed: {req['date_needed']}\nDescription: {req['description']}\n\nReview and approve: {approvals_url}"
    recipients = [CHUCK_EMAIL] if is_chuck_stage else [PO_APPROVER_1_EMAIL, PO_APPROVER_2_EMAIL]
    return send_email(subject, plain, to=recipients, html_body=html)

def send_po_decision_email(req):
    status = req['status']
    emoji = '✅' if status == 'Approved' else '❌'
    subject = f"{emoji} Your Purchase Request has been {status}"
    color = '#2d7a4f' if status == 'Approved' else '#c0392b'
    bg = '#e8f5ee' if status == 'Approved' else '#fdecea'
    notes_html = f'<tr><td style="padding:8px 0;color:#888;vertical-align:top">Notes</td><td style="padding:8px 0">{req["approver_notes"]}</td></tr>' if req.get('approver_notes') else ''
    stage1_html = f'<tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Initial Approval</td><td style="padding:8px 0;border-bottom:1px solid #eee">{req["stage1_approved_by"]}</td></tr>' if req.get('stage1_approved_by') else ''
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <div style="background:#95B9B8;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Purchase Request {status}</h2>
        <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        <div style="background:{bg};border-radius:6px;padding:12px 16px;margin-bottom:20px;text-align:center">
          <span style="font-size:24px">{emoji}</span>
          <span style="color:{color};font-weight:700;font-size:16px;margin-left:8px">Your request has been {status}</span>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888;width:140px">Vendor</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600">{req['vendor']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Amount</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:600">${req['amount']:.2f}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Category</td><td style="padding:8px 0;border-bottom:1px solid #eee">{req['category']}</td></tr>
          <tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#888">Decision by</td><td style="padding:8px 0;border-bottom:1px solid #eee">{req.get('approved_by','Sanders Beach Rentals Management')}</td></tr>
          {stage1_html}
          {notes_html}
        </table>
      </div>
    </div>"""
    plain = f"Your purchase request has been {status}.\n\nVendor: {req['vendor']}\nAmount: ${req['amount']:.2f}\nDecision by: {req.get('approved_by','Sanders Beach Rentals Management')}\n{('Notes: '+req['approver_notes']) if req.get('approver_notes') else ''}"
    return send_email(subject, plain, to=req['employee_email'], html_body=html)

def make_supply_qr(supply_id):
    url = f'https://sbrlinens.up.railway.app?supply={supply_id}'
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO(); img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

def make_hk_supply_qr(supply_id):
    url = f'https://sbrlinens.up.railway.app?hksupply={supply_id}'
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO(); img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

# ── Static routes ─────────────────────────────────────────────────────────────

@app.route('/')
def index(): return send_from_directory('public', 'index.html')

@app.route('/po-approvals')
def po_approvals(): return send_from_directory('public', 'po-approvals.html')

@app.route('/pickup')
def pickup(): return send_from_directory('public', 'pickup.html')

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json or {}
    role = check_pin(str(data.get('pin','')))
    if role: return jsonify({'success':True,'role':role})
    return jsonify({'success':False}), 401

@app.route('/api/cleaner-auth', methods=['POST'])
def cleaner_auth():
    """Authenticate a cleaner by their 5-digit PIN."""
    data = request.json or {}
    pin = str(data.get('pin', '')).strip()
    if not pin: return jsonify({'success':False,'error':'PIN required'}), 400
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,name,email FROM cleaners WHERE pin=%s AND active=1", (pin,))
    cleaner = cur.fetchone(); cur.close(); conn.close()
    if not cleaner: return jsonify({'success':False,'error':'Invalid PIN'}), 401
    return jsonify({'success':True,'cleaner':{'id':cleaner['id'],'name':cleaner['name'],'email':cleaner['email']}})

@app.route('/api/settings/pins', methods=['POST'])
def save_pins():
    data = request.json or {}
    global WAREHOUSE_PIN, ADMIN_PIN, MAINTENANCE_PIN, COORDINATOR_PIN
    if data.get('warehouse_pin'): WAREHOUSE_PIN = data['warehouse_pin']
    if data.get('admin_pin'): ADMIN_PIN = data['admin_pin']
    if data.get('maintenance_pin'): MAINTENANCE_PIN = data['maintenance_pin']
    if data.get('coordinator_pin'): COORDINATOR_PIN = data['coordinator_pin']
    return jsonify({'success':True})

# ── Homes ─────────────────────────────────────────────────────────────────────

@app.route('/api/homes', methods=['GET'])
def get_homes():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT h.id, h.name, h.code,
        COUNT(b.id) AS bag_count, COUNT(CASE WHEN b.status='out' THEN 1 END) AS out_count
        FROM homes h LEFT JOIN bags b ON b.home_id=h.id GROUP BY h.id ORDER BY h.code""")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/homes', methods=['POST'])
def add_home():
    data=request.json or {}; name=data.get('name','').strip(); code=data.get('code','').strip().upper()
    if not name or not code: return jsonify({'error':'Name and code required'}),400
    conn=get_db(); cur=conn.cursor()
    try:
        cur.execute('INSERT INTO homes (name,code) VALUES (%s,%s)',(name,code))
        conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'error':'Home already exists'}),409

@app.route('/api/homes/<int:hid>', methods=['DELETE'])
def delete_home(hid):
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT COUNT(*) FROM bags WHERE home_id=%s',(hid,)); n=cur.fetchone()[0]
    if n>0: cur.close(); conn.close(); return jsonify({'error':'Remove bags first'}),400
    cur.execute('DELETE FROM homes WHERE id=%s',(hid,)); conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})

# ── Bags ──────────────────────────────────────────────────────────────────────

@app.route('/api/bags', methods=['POST'])
def add_bag():
    data=request.json or {}; bag_id=data.get('bag_id','').strip().upper(); home_id=data.get('home_id')
    if not bag_id or not home_id: return jsonify({'error':'bag_id and home_id required'}),400
    conn=get_db(); cur=conn.cursor()
    try:
        cur.execute('INSERT INTO bags (id,home_id,status) VALUES (%s,%s,%s)',(bag_id,home_id,'in'))
        conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'id':bag_id})
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'error':'Bag ID already exists'}),409

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT b.id, b.status, b.checked_out, b.staged_at, b.picked_up_at, b.notes,
        h.name AS home_name, h.code AS home_code,
        c.name AS cleaner_name, c.id AS cleaner_id
        FROM bags b JOIN homes h ON h.id=b.home_id LEFT JOIN cleaners c ON c.id=b.cleaner_id
        ORDER BY h.code, b.id""")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bags"); total=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM bags WHERE status='out'"); out=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM bags WHERE status='staged'"); staged=cur.fetchone()[0]
    cur.close(); conn.close(); return jsonify({'total':total,'out':out,'staged':staged,'in':total-out-staged})

@app.route('/api/bag/<path:bag_id>', methods=['GET'])
def get_bag(bag_id):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,h.name AS home_name FROM bags b JOIN homes h ON h.id=b.home_id WHERE b.id=%s",(bag_id.upper(),))
    row=cur.fetchone(); cur.close(); conn.close()
    if not row: return jsonify({'error':'Not found'}),404
    return jsonify(row)

@app.route('/api/bag/<path:bag_id>/checkout', methods=['POST'])
def checkout(bag_id):
    """Linen attendant stages a bag for a cleaner (status: in → staged)."""
    data=request.json or {}; cleaner_id=data.get('cleaner_id'); staff_name=data.get('staff_name','').strip()
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,h.name AS home_name FROM bags b JOIN homes h ON h.id=b.home_id WHERE b.id=%s",(bag_id.upper(),))
    bag=cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}),404
    if bag['status'] in ('out','staged'): cur.close(); conn.close(); return jsonify({'error':'Already staged or checked out'}),400
    ts=now_central()
    cur.execute("UPDATE bags SET status='staged',cleaner_id=%s,staged_at=%s,picked_up_at=NULL,overdue_alerted=0 WHERE id=%s",(cleaner_id,ts,bag_id.upper()))
    cur.execute("INSERT INTO transactions (bag_id,home_id,cleaner_id,action,timestamp,staff_name) VALUES (%s,%s,%s,'Staged',%s,%s)",(bag_id.upper(),bag['home_id'],cleaner_id,ts,staff_name or None))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'home':bag['home_name'],'status':'staged'})

@app.route('/api/bag/<path:bag_id>/pickup', methods=['POST'])
def pickup_bag(bag_id):
    """Cleaner scans to confirm pickup (status: staged → out). 24hr timer starts here."""
    data=request.json or {}
    cleaner_pin=str(data.get('cleaner_pin',''))
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Verify cleaner PIN
    cur.execute("SELECT id,name FROM cleaners WHERE pin=%s AND active=1",(cleaner_pin,))
    cleaner=cur.fetchone()
    if not cleaner: cur.close(); conn.close(); return jsonify({'error':'Invalid PIN'}),401
    cur.execute("SELECT b.*,h.name AS home_name FROM bags b JOIN homes h ON h.id=b.home_id WHERE b.id=%s",(bag_id.upper(),))
    bag=cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}),404
    if bag['status'] == 'in': cur.close(); conn.close(); return jsonify({'error':'This bag has not been staged yet'}),400
    if bag['status'] == 'out': cur.close(); conn.close(); return jsonify({'error':'Already picked up'}),400
    # Check it's staged for THIS cleaner
    if bag['cleaner_id'] != cleaner['id']:
        # Get the correct cleaner name for the error
        cur.execute("SELECT name FROM cleaners WHERE id=%s",(bag['cleaner_id'],))
        assigned=cur.fetchone()
        assigned_name = assigned['name'] if assigned else 'another cleaner'
        cur.close(); conn.close()
        return jsonify({'error':f'This bag is staged for {assigned_name}, not you. Please put it back.','wrong_cleaner':True,'assigned_to':assigned_name}),403
    ts=now_central()
    cur.execute("UPDATE bags SET status='out',picked_up_at=%s,overdue_alerted=0 WHERE id=%s",(ts,bag_id.upper()))
    cur.execute("INSERT INTO transactions (bag_id,home_id,cleaner_id,action,timestamp) VALUES (%s,%s,%s,'Picked up',%s)",(bag_id.upper(),bag['home_id'],cleaner['id'],ts))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'home':bag['home_name'],'cleaner':cleaner['name']})

@app.route('/api/bag/<path:bag_id>/checkin', methods=['POST'])
def checkin(bag_id):
    data=request.json or {}; notes=data.get('notes',''); staff_name=data.get('staff_name','').strip()
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,h.name AS home_name,c.name AS cleaner_name FROM bags b JOIN homes h ON h.id=b.home_id LEFT JOIN cleaners c ON c.id=b.cleaner_id WHERE b.id=%s",(bag_id.upper(),))
    bag=cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}),404
    if bag['status']=='in': cur.close(); conn.close(); return jsonify({'error':'Already checked in'}),400
    ts=now_central()
    cur.execute("INSERT INTO transactions (bag_id,home_id,cleaner_id,action,timestamp,notes,staff_name) VALUES (%s,%s,%s,'Returned',%s,%s,%s)",(bag_id.upper(),bag['home_id'],bag['cleaner_id'],ts,notes,staff_name or None))
    cur.execute("UPDATE bags SET status='in',cleaner_id=NULL,staged_at=NULL,picked_up_at=NULL,checked_out=NULL,overdue_alerted=0 WHERE id=%s",(bag_id.upper(),))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'home':bag['home_name'],'cleaner':bag['cleaner_name'] or '—'})

# ── Cleaner staged bags (for pickup page) ─────────────────────────────────────

@app.route('/api/cleaner/<int:cleaner_id>/staged-bags', methods=['GET'])
def get_staged_bags(cleaner_id):
    """Return bags staged for this cleaner that haven't been picked up yet."""
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT b.id, b.status, b.staged_at, h.name AS home_name, h.code AS home_code
        FROM bags b JOIN homes h ON h.id=b.home_id
        WHERE b.cleaner_id=%s AND b.status='staged'
        ORDER BY h.code, b.id""",(cleaner_id,))
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

# ── Overdue check (called on a schedule or manually) ─────────────────────────

@app.route('/api/check-overdue', methods=['POST'])
def check_overdue():
    """Find bags picked up 24+ hours ago and send overdue emails if not already alerted."""
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT b.id, b.picked_up_at, b.overdue_alerted,
        h.name AS home_name, c.id AS cleaner_id, c.name AS cleaner_name, c.email AS cleaner_email
        FROM bags b
        JOIN homes h ON h.id=b.home_id
        JOIN cleaners c ON c.id=b.cleaner_id
        WHERE b.status='out' AND b.picked_up_at IS NOT NULL AND b.overdue_alerted=0""")
    bags=cur.fetchall()
    alerted=[]
    now_utc=datetime.now(pytz.utc)
    for bag in bags:
        try:
            pickup_utc=datetime.fromisoformat(bag['picked_up_at'].replace(' ','T')).replace(tzinfo=CENTRAL).astimezone(pytz.utc)
            hours_out=(now_utc-pickup_utc).total_seconds()/3600
            if hours_out>=24:
                cleaner={'id':bag['cleaner_id'],'name':bag['cleaner_name'],'email':bag['cleaner_email']}
                sent=send_overdue_email(dict(bag), cleaner)
                if sent:
                    cur2=conn.cursor()
                    cur2.execute("UPDATE bags SET overdue_alerted=1 WHERE id=%s",(bag['id'],))
                    conn.commit(); cur2.close()
                    alerted.append(bag['id'])
        except Exception as e:
            print(f'Overdue check error for {bag["id"]}: {e}')
    cur.close(); conn.close()
    return jsonify({'checked':len(bags),'alerted':alerted})

# ── Cleaners ──────────────────────────────────────────────────────────────────

@app.route('/api/cleaners', methods=['GET'])
def get_cleaners():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT c.id,c.name,c.email,c.phone,c.pin,
        COUNT(CASE WHEN b.status IN ('out','staged') THEN 1 END) AS bags_out
        FROM cleaners c LEFT JOIN bags b ON b.cleaner_id=c.id
        WHERE c.active=1 GROUP BY c.id ORDER BY c.name""")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/cleaners', methods=['POST'])
def add_cleaner():
    data=request.json or {}; name=data.get('name','').strip()
    email=data.get('email','').strip(); phone=data.get('phone','').strip()
    if not name: return jsonify({'error':'Name required'}),400
    conn=get_db(); cur=conn.cursor()
    pin=generate_cleaner_pin(conn)
    cur.execute('INSERT INTO cleaners (name,email,phone,pin) VALUES (%s,%s,%s,%s)',(name,email or None,phone or None,pin))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'pin':pin})

@app.route('/api/cleaners/<int:cid>', methods=['PUT'])
def update_cleaner(cid):
    data=request.json or {}
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE cleaners SET name=%s,email=%s,phone=%s WHERE id=%s",
        (data.get('name','').strip(), data.get('email','').strip() or None,
         data.get('phone','').strip() or None, cid))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})

@app.route('/api/cleaners/<int:cid>/reset-pin', methods=['POST'])
def reset_cleaner_pin(cid):
    conn=get_db(); cur=conn.cursor()
    pin=generate_cleaner_pin(conn)
    cur.execute("UPDATE cleaners SET pin=%s WHERE id=%s",(pin,cid))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'pin':pin})

@app.route('/api/cleaners/<int:cid>', methods=['DELETE'])
def delete_cleaner(cid):
    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bags WHERE cleaner_id=%s AND status IN ('out','staged')",(cid,)); n=cur.fetchone()[0]
    if n>0: cur.close(); conn.close(); return jsonify({'error':'Cleaner has bags out or staged'}),400
    cur.execute('UPDATE cleaners SET active=0 WHERE id=%s',(cid,)); conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})

# ── Activity log ──────────────────────────────────────────────────────────────

@app.route('/api/activity', methods=['GET'])
def get_activity():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT t.id,'bag' AS activity_type, t.bag_id, h.name AS home_name,
               c.name AS cleaner_name, t.action, t.timestamp AS ts, t.notes, t.staff_name
        FROM transactions t JOIN homes h ON h.id=t.home_id LEFT JOIN cleaners c ON c.id=t.cleaner_id
        UNION ALL
        SELECT lt.id,'loaner' AS activity_type, lt.loaner_id AS bag_id, h.name AS home_name,
               s.name AS cleaner_name, lt.action, lt.timestamp AS ts, lt.notes, lt.performed_by_name AS staff_name
        FROM loaner_transactions lt LEFT JOIN homes h ON h.id=lt.home_id LEFT JOIN loaner_staff s ON s.id=lt.staff_id
        ORDER BY ts DESC LIMIT 500""")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

# ── Maintenance staff ─────────────────────────────────────────────────────────

@app.route('/api/maintenance_staff', methods=['GET'])
def get_maintenance_staff():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM maintenance_staff WHERE active=1 ORDER BY name")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

# ── Loaners ───────────────────────────────────────────────────────────────────

@app.route('/api/loaners', methods=['GET'])
def get_loaners():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT l.*,h.name AS home_name,h.code AS home_code,s.name AS staff_name
        FROM loaners l LEFT JOIN homes h ON h.id=l.home_id LEFT JOIN loaner_staff s ON s.id=l.staff_id
        ORDER BY l.category,l.name""")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/loaner/<path:loaner_id>/deploy', methods=['POST'])
def deploy_loaner(loaner_id):
    data=request.json or {}; staff_id=data.get('staff_id'); home_id=data.get('home_id'); performed_by_name=data.get('staff_name','').strip()
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT l.*,s.name AS sname,h.name AS hname FROM loaners l LEFT JOIN loaner_staff s ON s.id=%s LEFT JOIN homes h ON h.id=%s WHERE l.id=%s",(staff_id,home_id,loaner_id.upper()))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if row['status']=='out': cur.close(); conn.close(); return jsonify({'error':'Already deployed'}),400
    ts=now_central()
    cur.execute("UPDATE loaners SET status='out',staff_id=%s,home_id=%s,checked_out=%s,checked_out_by=%s WHERE id=%s",(staff_id,home_id,ts,performed_by_name or None,loaner_id.upper()))
    cur.execute("INSERT INTO loaner_transactions (loaner_id,staff_id,home_id,action,timestamp,performed_by_name) VALUES (%s,%s,%s,'Deployed',%s,%s)",(loaner_id.upper(),staff_id,home_id,ts,performed_by_name or None))
    conn.commit()
    staff_name=row.get('sname','Staff'); home_name=row.get('hname','Unknown')
    cur.close(); conn.close(); return jsonify({'success':True,'item':row['name'],'staff':performed_by_name or staff_name,'home':home_name})

@app.route('/api/loaner/<path:loaner_id>/retrieve', methods=['POST'])
def retrieve_loaner(loaner_id):
    data=request.json or {}; performed_by_name=data.get('staff_name','').strip()
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT l.*,h.name AS home_name FROM loaners l LEFT JOIN homes h ON h.id=l.home_id WHERE l.id=%s",(loaner_id.upper(),))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if row['status']=='in': cur.close(); conn.close(); return jsonify({'error':'Already in warehouse'}),400
    ts=now_central()
    cur.execute("INSERT INTO loaner_transactions (loaner_id,staff_id,home_id,action,timestamp,performed_by_name) VALUES (%s,%s,%s,'Retrieved',%s,%s)",(loaner_id.upper(),row['staff_id'],row['home_id'],ts,performed_by_name or None))
    cur.execute("UPDATE loaners SET status='in',staff_id=NULL,home_id=NULL,checked_out=NULL,checked_out_by=NULL WHERE id=%s",(loaner_id.upper(),))
    conn.commit(); home_name=row.get('home_name','Unknown'); cur.close(); conn.close(); return jsonify({'success':True,'item':row['name'],'home':home_name})

# ── SupplyTrack ───────────────────────────────────────────────────────────────

@app.route('/api/supplies', methods=['GET'])
def get_supplies():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM supply_items ORDER BY category,name")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/supplies', methods=['POST'])
def add_supply():
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin': return jsonify({'error':'Admin PIN required'}),403
    name=data.get('name','').strip(); category=data.get('category','General').strip()
    quantity=int(data.get('quantity',0)); threshold=int(data.get('low_stock_threshold',5))
    unit=data.get('unit','units').strip()
    if not name: return jsonify({'error':'Name required'}),400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("INSERT INTO supply_items (name,category,quantity,low_stock_threshold,unit,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",(name,category,quantity,threshold,unit,now_central()))
        sid=cur.fetchone()['id']; qr=make_supply_qr(sid)
        cur.execute("UPDATE supply_items SET qr_code=%s WHERE id=%s",(qr,sid))
        conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'id':sid})
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'error':'Item name already exists'}),409

@app.route('/api/supplies/<int:sid>', methods=['PUT'])
def update_supply(sid):
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin': return jsonify({'error':'Admin PIN required'}),403
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE supply_items SET name=%s,category=%s,low_stock_threshold=%s,unit=%s WHERE id=%s",(data.get('name'),data.get('category'),int(data.get('low_stock_threshold',5)),data.get('unit','units'),sid))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})

@app.route('/api/supplies/<int:sid>/transaction', methods=['POST'])
def supply_transaction(sid):
    data=request.json or {}
    role=check_pin(str(data.get('pin','')))
    if role not in ('admin','maintenance','coordinator'): return jsonify({'error':'Access denied'}),403
    action=data.get('action',''); qty=int(data.get('quantity',1))
    performed=data.get('performed_by','Staff').strip(); notes=data.get('notes','').strip()
    if action not in ('take','restock'): return jsonify({'error':'Invalid action'}),400
    if qty<=0: return jsonify({'error':'Quantity must be positive'}),400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM supply_items WHERE id=%s",(sid,)); item=cur.fetchone()
    if not item: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if action=='take' and item['quantity']<qty: cur.close(); conn.close(); return jsonify({'error':f"Only {item['quantity']} {item['unit']} in stock"}),400
    new_qty=item['quantity']-qty if action=='take' else item['quantity']+qty
    cur.execute("UPDATE supply_items SET quantity=%s WHERE id=%s",(new_qty,sid))
    cur.execute("INSERT INTO supply_transactions (supply_id,action,quantity,quantity_after,performed_by,timestamp,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",(sid,action,qty,new_qty,performed,now_central(),notes))
    conn.commit(); alert_sent=False
    if action=='take' and new_qty<=item['low_stock_threshold']:
        body=f"Low stock alert for '{item['name']}'.\nCurrent qty: {new_qty} {item['unit']}\nThreshold: {item['low_stock_threshold']}"
        alert_sent=send_email(f"LOW STOCK: {item['name']}",body)
    cur.close(); conn.close(); return jsonify({'success':True,'new_quantity':new_qty,'alert_sent':alert_sent})

@app.route('/api/supplies/<int:sid>/qr', methods=['GET'])
def get_supply_qr(sid):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name,qr_code FROM supply_items WHERE id=%s",(sid,)); row=cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'error':'Not found'}),404
    return jsonify({'name':row['name'],'qr_code':row['qr_code']})

@app.route('/api/supply-log', methods=['GET'])
def supply_log():
    limit=int(request.args.get('limit',100))
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT st.*,si.name AS item_name,si.unit FROM supply_transactions st
        JOIN supply_items si ON si.id=st.supply_id ORDER BY st.timestamp DESC LIMIT %s""",(limit,))
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)


# ── HousekeepingSupplyCentral ───────────────────────────────────────────────────

@app.route('/api/hk-supplies', methods=['GET'])
def get_hk_supplies():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM hk_supply_items ORDER BY category,name")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/hk-supplies', methods=['POST'])
def add_hk_supply():
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin': return jsonify({'error':'Admin PIN required'}),403
    name=data.get('name','').strip(); category=data.get('category','General').strip()
    quantity=int(data.get('quantity',0)); threshold=int(data.get('low_stock_threshold',5))
    unit=data.get('unit','units').strip()
    if not name: return jsonify({'error':'Name required'}),400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("INSERT INTO hk_supply_items (name,category,quantity,low_stock_threshold,unit,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",(name,category,quantity,threshold,unit,now_central()))
        sid=cur.fetchone()['id']; qr=make_hk_supply_qr(sid)
        cur.execute("UPDATE hk_supply_items SET qr_code=%s WHERE id=%s",(qr,sid))
        conn.commit(); cur.close(); conn.close(); return jsonify({'success':True,'id':sid})
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'error':'Item name already exists'}),409

@app.route('/api/hk-supplies/<int:sid>', methods=['PUT'])
def update_hk_supply(sid):
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin': return jsonify({'error':'Admin PIN required'}),403
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE hk_supply_items SET name=%s,category=%s,low_stock_threshold=%s,unit=%s WHERE id=%s",(data.get('name'),data.get('category'),int(data.get('low_stock_threshold',5)),data.get('unit','units'),sid))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})

@app.route('/api/hk-supplies/<int:sid>/transaction', methods=['POST'])
def hk_supply_transaction(sid):
    data=request.json or {}
    role=check_pin(str(data.get('pin','')))
    if role not in ('admin','warehouse'): return jsonify({'error':'Access denied'}),403
    action=data.get('action',''); qty=int(data.get('quantity',1))
    performed=data.get('performed_by','Staff').strip(); notes=data.get('notes','').strip()
    if action not in ('take','restock'): return jsonify({'error':'Invalid action'}),400
    if qty<=0: return jsonify({'error':'Quantity must be positive'}),400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM hk_supply_items WHERE id=%s",(sid,)); item=cur.fetchone()
    if not item: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if action=='take' and item['quantity']<qty: cur.close(); conn.close(); return jsonify({'error':f"Only {item['quantity']} {item['unit']} in stock"}),400
    new_qty=item['quantity']-qty if action=='take' else item['quantity']+qty
    cur.execute("UPDATE hk_supply_items SET quantity=%s WHERE id=%s",(new_qty,sid))
    cur.execute("INSERT INTO hk_supply_transactions (supply_id,action,quantity,quantity_after,performed_by,timestamp,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",(sid,action,qty,new_qty,performed,now_central(),notes))
    conn.commit(); alert_sent=False
    if action=='take' and new_qty<=item['low_stock_threshold']:
        body=f"Low stock alert for '{item['name']}' (Housekeeping Supplies).\nCurrent qty: {new_qty} {item['unit']}\nThreshold: {item['low_stock_threshold']}"
        alert_sent=send_email(f"LOW STOCK (Housekeeping): {item['name']}",body)
    cur.close(); conn.close(); return jsonify({'success':True,'new_quantity':new_qty,'alert_sent':alert_sent})

@app.route('/api/hk-supplies/<int:sid>/qr', methods=['GET'])
def get_hk_supply_qr(sid):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name,qr_code FROM hk_supply_items WHERE id=%s",(sid,)); row=cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'error':'Not found'}),404
    return jsonify({'name':row['name'],'qr_code':row['qr_code']})

@app.route('/api/hk-supply-log', methods=['GET'])
def hk_supply_log():
    limit=int(request.args.get('limit',100))
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT st.*,si.name AS item_name,si.unit FROM hk_supply_transactions st
        JOIN hk_supply_items si ON si.id=st.supply_id ORDER BY st.timestamp DESC LIMIT %s""",(limit,))
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/seed-hk-supplies', methods=['POST'])
def seed_hk_supplies():
    """One-time seed of real housekeeping supply inventory. Admin PIN required."""
    data = request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403

    items = [("Masking Tape", 2, "Maintenance", "rolls"), ("Plastic Stretch Wrap", 4, "Maintenance", "rolls"), ("Bathroom Trash Liners", 2000, "Trash & Liners", "liners"), ("Kitchen Trash Bags", 1100, "Trash & Liners", "bags"), ("10oz Tide Bottles", 96, "Laundry", "bottles"), ("Dishwasher Pod Packs", 1250, "Kitchen", "packs"), ("Kitchen Sponges", 432, "Kitchen", "sponges"), ("3oz Palmolive Bottles", 270, "Kitchen", "bottles"), ("Molton Brown Shampoo", 800, "Guest Amenities", "bottles"), ("Molton Brown Conditioner", 750, "Guest Amenities", "bottles"), ("Molton Brown Body Wash", 500, "Guest Amenities", "bottles"), ("Molton Brown Bar Soap", 480, "Guest Amenities", "bars"), ("Amavida Coffee Packs", 50, "Guest Amenities", "packs"), ("#4 Cone Coffee Filters", 400, "Guest Amenities", "filters"), ("Round Coffee Filters", 1350, "Guest Amenities", "filters"), ("Toilet Paper Rolls", 450, "Guest Amenities", "rolls"), ("Paper Towel Rolls", 174, "Guest Amenities", "rolls"), ("Kitchen Amenity Boxes", 336, "Guest Amenities", "boxes"), ("Laundry Detergent (Gallon)", 2, "Laundry", "gallons"), ("Pledge Cans", 18, "Cleaning Supplies", "cans"), ("Oven Cleaner", 4, "Cleaning Supplies", "bottles"), ("Stainless Steel Cleaner Cans", 7, "Cleaning Supplies", "cans"), ("Bar Keeper's Friend Cans", 3, "Cleaning Supplies", "cans"), ("SOS Scrub Pads", 50, "Cleaning Supplies", "pads"), ("Magic Erasers", 75, "Cleaning Supplies", "erasers"), ("Swiffer Duster Handles", 2, "Cleaning Supplies", "handles"), ("Swiffer Duster Pads", 2, "Cleaning Supplies", "pads"), ("2 Gal Ziploc Bags", 50, "Kitchen", "bags"), ("Large Floor Rollers", 0, "Cleaning Supplies", "rollers"), ("Small Lint Rollers", 21, "Cleaning Supplies", "rollers"), ("Bleach (Gallon)", 6, "Cleaning Supplies", "gallons"), ("White Vinegar (Gallon)", 2, "Cleaning Supplies", "gallons"), ("Kemzyme (Gallons)", 6, "Cleaning Supplies", "gallons"), ("Polishing Cleanser Bottles", 16, "Cleaning Supplies", "bottles"), ("Spor Go (Gallons)", 4, "Cleaning Supplies", "gallons"), ("Odorsorb (Gallons)", 6, "Cleaning Supplies", "gallons"), ("Drop N Go Glass Individual Cartridges", 94, "Cleaning Supplies", "cartridges"), ("Drop N Go Bathroom Cleaner Individual Cartridges", 144, "Cleaning Supplies", "cartridges"), ("Red Trash Bags", 200, "Trash & Liners", "bags"), ("Drop N Go Bathroom Spray Bottles", 1, "Cleaning Supplies", "bottles"), ("Drop N Go Glass Spray Bottles", 1, "Cleaning Supplies", "bottles"), ("Degreaser", 2, "Cleaning Supplies", "bottles")]

    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM hk_supply_transactions")
    cur.execute("DELETE FROM hk_supply_items")
    conn.commit()

    inserted = 0
    for name, qty, category, unit in items:
        try:
            if qty <= 10:
                threshold = max(1, qty)
            else:
                threshold = max(5, int(qty * 0.1))
            cur.execute(
                "INSERT INTO hk_supply_items (name,category,quantity,low_stock_threshold,unit,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (name, category, qty, threshold, unit, now_central())
            )
            sid = cur.fetchone()[0]
            qr = make_hk_supply_qr(sid)
            cur.execute("UPDATE hk_supply_items SET qr_code=%s WHERE id=%s", (qr, sid))
            inserted += 1
        except Exception as e:
            print(f'Seed error for {name}: {e}')
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True, 'inserted':inserted})


# ── OrdersCentral ─────────────────────────────────────────────────────────────

def get_supply_table(module):
    """Return the table name for a given module key."""
    return 'hk_supply_items' if module == 'housekeeping' else 'supply_items'

@app.route('/api/orders', methods=['GET'])
def get_orders():
    """List orders, optionally filtered by module and/or status."""
    module = request.args.get('module')
    status = request.args.get('status')
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    q = "SELECT * FROM supply_orders WHERE 1=1"
    params = []
    if module:
        q += " AND module=%s"; params.append(module)
    if status:
        q += " AND status=%s"; params.append(status)
    q += " ORDER BY ordered_at DESC LIMIT 200"
    cur.execute(q, params)
    orders = cur.fetchall()
    # Attach line items to each order
    for o in orders:
        cur.execute("SELECT * FROM supply_order_items WHERE order_id=%s ORDER BY id", (o['id'],))
        o['items'] = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(orders)

@app.route('/api/orders/<int:oid>', methods=['GET'])
def get_order(oid):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM supply_orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    if not order: cur.close(); conn.close(); return jsonify({'error':'Not found'}),404
    cur.execute("SELECT * FROM supply_order_items WHERE order_id=%s ORDER BY id", (oid,))
    order['items'] = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(order)

@app.route('/api/orders', methods=['POST'])
def create_order():
    """Place a new order. Expects: module, ordered_by, vendor, notes, items[]
    Each item: item_name, matched_supply_id (optional), cases_ordered, units_per_case, unit_label, price (optional)"""
    data = request.json or {}
    module = data.get('module')
    if module not in ('housekeeping', 'maintenance'):
        return jsonify({'error':'module must be housekeeping or maintenance'}), 400
    ordered_by = data.get('ordered_by','').strip()
    if not ordered_by:
        return jsonify({'error':'ordered_by is required'}), 400
    items = data.get('items', [])
    if not items:
        return jsonify({'error':'At least one item is required'}), 400

    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO supply_orders (module,ordered_by,vendor,status,notes,ordered_at) VALUES (%s,%s,%s,'Ordered',%s,%s) RETURNING id",
        (module, ordered_by, data.get('vendor','').strip() or None, data.get('notes','').strip() or None, now_central())
    )
    order_id = cur.fetchone()['id']

    table = get_supply_table(module)
    for item in items:
        name = item.get('item_name','').strip()
        if not name: continue
        cases = float(item.get('cases_ordered', 1))
        units_per_case = float(item.get('units_per_case', 1))
        expected = round(cases * units_per_case)
        matched_id = item.get('matched_supply_id')
        unit_label = item.get('unit_label','units').strip() or 'units'
        price = item.get('price')
        cur.execute(
            """INSERT INTO supply_order_items
               (order_id,item_name,matched_supply_id,matched_supply_table,cases_ordered,units_per_case,expected_units,unit_label,price)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (order_id, name, matched_id, table if matched_id else None, cases, units_per_case, expected, unit_label, price)
        )
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True, 'id':order_id})

@app.route('/api/orders/<int:oid>/receive', methods=['POST'])
def receive_order(oid):
    """Mark an order received. Expects: received_by, items[{id, received_units}]"""
    data = request.json or {}
    received_by = data.get('received_by','').strip()
    if not received_by:
        return jsonify({'error':'received_by is required'}), 400
    receive_notes = data.get('notes','').strip()
    item_updates = data.get('items', [])

    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM supply_orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    if not order: cur.close(); conn.close(); return jsonify({'error':'Order not found'}),404
    if order['status'] == 'Received': cur.close(); conn.close(); return jsonify({'error':'Already received'}),400

    has_discrepancy = False
    for upd in item_updates:
        item_id = upd.get('id')
        received_units = upd.get('received_units')
        if item_id is None or received_units is None: continue
        cur.execute("SELECT * FROM supply_order_items WHERE id=%s AND order_id=%s", (item_id, oid))
        line = cur.fetchone()
        if not line: continue
        received_units = int(received_units)
        line_discrepancy = 1 if received_units != line['expected_units'] else 0
        if line_discrepancy: has_discrepancy = True
        line_notes = upd.get('notes','').strip() or None
        cur.execute(
            "UPDATE supply_order_items SET received_units=%s, line_discrepancy=%s, receive_notes=%s WHERE id=%s",
            (received_units, line_discrepancy, line_notes, item_id)
        )
        # Auto-update inventory if this line is matched to a real item
        if line['matched_supply_id'] and line['matched_supply_table'] and received_units > 0:
            table = line['matched_supply_table']
            cur.execute(f"SELECT quantity FROM {table} WHERE id=%s", (line['matched_supply_id'],))
            row = cur.fetchone()
            if row:
                new_qty = row['quantity'] + received_units
                cur.execute(f"UPDATE {table} SET quantity=%s WHERE id=%s", (new_qty, line['matched_supply_id']))

    ts = now_central()
    cur.execute(
        "UPDATE supply_orders SET status='Received', received_at=%s, received_by=%s, has_discrepancy=%s, discrepancy_notes=%s WHERE id=%s",
        (ts, received_by, 1 if has_discrepancy else 0, receive_notes or None, oid)
    )
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True, 'has_discrepancy':has_discrepancy})

@app.route('/api/orders/<int:oid>/resolve-discrepancy', methods=['POST'])
def resolve_discrepancy(oid):
    data = request.json or {}
    notes = data.get('notes','').strip()
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE supply_orders SET discrepancy_resolved=1, discrepancy_notes=%s WHERE id=%s", (notes, oid))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

@app.route('/api/orders/<int:oid>', methods=['DELETE'])
def cancel_order(oid):
    """Cancel a pending order (only if not yet received)."""
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT status FROM supply_orders WHERE id=%s",(oid,))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'error':'Not found'}),404
    if row['status']=='Received': cur.close(); conn.close(); return jsonify({'error':'Cannot cancel a received order'}),400
    cur.execute("DELETE FROM supply_order_items WHERE order_id=%s",(oid,))
    cur.execute("DELETE FROM supply_orders WHERE id=%s",(oid,))
    conn.commit(); cur.close(); conn.close(); return jsonify({'success':True})




# ── Staff PIN Management ──────────────────────────────────────────────────────

STAFF_SEED = [['Kristin', 'admin', '5145'], ['Sarah Elizabeth', 'admin', '7343'], ['Sabrina', 'admin', '9197'], ['Jennifer Matthews', 'admin', '5586'], ['Jessica', 'coordinator', '2129'], ['Chris', 'maintenance', '5269'], ['Keith', 'maintenance', '7836'], ['Chuck', 'maintenance', '4133'], ['Jonathan', 'maintenance', '7154'], ['Shawn', 'maintenance', '5700'], ['Laura Durrance', 'inspector', '4250'], ['Stephanie Pierantoni', 'inspector', '9534'], ['Alexis Rains', 'inspector', '1693'], ['Dawn Bailey', 'inspector', '2761'], ['Cassie Sloan', 'inspector', '7410'], ['Micah Haigler', 'inspector', '7982'], ['Warehouse Staff 1', 'warehouse', '6460'], ['Warehouse Staff 2', 'warehouse', '1544']]

def check_staff_pin(pin):
    """Check PIN against staff table. Returns dict with name, role or None."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM staff_members WHERE pin=%s AND active=1", (str(pin),))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[STAFF PIN ERROR] {e}")
        return None

@app.route('/api/staff/auth', methods=['POST'])
def staff_auth():
    """Authenticate a PIN — checks individual staff PINs first, then falls
    back to the legacy shared role PINs (admin/warehouse/maintenance/coordinator)
    so anyone not yet migrated to an individual PIN still works."""
    data = request.json or {}
    pin = str(data.get('pin', ''))
    staff = check_staff_pin(pin)
    if staff:
        return jsonify({'success': True, 'name': staff['name'], 'role': staff['role'], 'id': staff['id'], 'email': staff.get('email') or ''})
    legacy_role = check_pin(pin)
    if legacy_role:
        return jsonify({'success': True, 'name': legacy_role.capitalize(), 'role': legacy_role, 'is_master': legacy_role == 'admin'})
    return jsonify({'error': 'Invalid PIN'}), 401

@app.route('/api/staff', methods=['GET'])
def get_staff():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,name,role,email,active,created_at FROM staff_members ORDER BY role,name")
    rows=cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/staff/reveal', methods=['POST'])
def reveal_staff_pins():
    """Admin-only: returns staff list WITH real PINs included."""
    data = request.json or {}
    if check_pin(str(data.get('admin_pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,name,role,email,active,created_at,pin FROM staff_members ORDER BY role,name")
    rows=cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

# One-time known emails, carried over from the old hardcoded inspector list,
# used to backfill staff_members.email so overdue-loan alerts keep working
# once StoreCentral switches to login-based attribution.
KNOWN_STAFF_EMAILS = {
    "Laura Durrance": "laura@sandersbeachrentals.com",
    "Stephanie Pierantoni": "stephanie@sandersbeachrentals.com",
    "Alexis Rains": "alexis@sandersbeachrentals.com",
    "Dawn Bailey": "dawn@sandersbeachrentals.com",
    "Cassie Sloan": "cassie@sandersbeachrentals.com",
    "Micah Haigler": "micah@sandersbeachrentals.com",
}

@app.route('/api/staff/sync-known-emails', methods=['POST'])
def sync_known_emails():
    """Admin-only, one-time: backfill emails for staff whose email is missing,
    using the known map above. Does not overwrite an email that's already set."""
    data = request.json or {}
    if check_pin(str(data.get('admin_pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    updated = []
    for name, email in KNOWN_STAFF_EMAILS.items():
        cur.execute(
            "UPDATE staff_members SET email=%s WHERE name=%s AND (email IS NULL OR email='')",
            (email, name)
        )
        if cur.rowcount > 0:
            updated.append(name)
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success': True, 'updated': updated})

@app.route('/api/staff', methods=['POST'])
def add_staff():
    data=request.json or {}
    if check_pin(str(data.get('admin_pin',''))) != 'admin' and not check_staff_pin(str(data.get('admin_pin',''))) :
        return jsonify({'error':'Admin PIN required'}), 403
    name=data.get('name','').strip()
    role=data.get('role','warehouse')
    pin=str(data.get('pin','')).strip()
    email=data.get('email','').strip() or None
    if not name or not pin: return jsonify({'error':'Name and PIN required'}), 400
    if len(pin) != 4 or not pin.isdigit(): return jsonify({'error':'PIN must be exactly 4 digits'}), 400
    conn=get_db(); cur=conn.cursor()
    try:
        cur.execute("INSERT INTO staff_members (name,role,pin,email,active,created_at) VALUES (%s,%s,%s,%s,1,%s)",
            (name,role,pin,email,now_central()))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success':True})
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error':'PIN already in use'}), 409

@app.route('/api/staff/<int:sid>', methods=['PUT'])
def update_staff(sid):
    data=request.json or {}
    if check_pin(str(data.get('admin_pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    fields=[]; params=[]
    if 'name' in data: fields.append('name=%s'); params.append(data['name'].strip())
    if 'role' in data: fields.append('role=%s'); params.append(data['role'])
    if 'email' in data: fields.append('email=%s'); params.append(data['email'].strip() or None)
    if 'pin' in data:
        new_pin=str(data['pin']).strip()
        if len(new_pin)!=4 or not new_pin.isdigit():
            cur.close(); conn.close(); return jsonify({'error':'PIN must be 4 digits'}), 400
        fields.append('pin=%s'); params.append(new_pin)
    if 'active' in data: fields.append('active=%s'); params.append(int(data['active']))
    if not fields: cur.close(); conn.close(); return jsonify({'error':'Nothing to update'}), 400
    params.append(sid)
    try:
        cur.execute(f"UPDATE staff_members SET {','.join(fields)} WHERE id=%s", params)
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success':True})
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error':'PIN already in use by another staff member'}), 409

@app.route('/api/staff/<int:sid>', methods=['DELETE'])
def delete_staff(sid):
    """Admin-only: permanently remove a single staff record (e.g. an accidental duplicate)."""
    data = request.json or {}
    if check_pin(str(data.get('admin_pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    cur.execute("DELETE FROM staff_members WHERE id=%s", (sid,))
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    if not deleted:
        return jsonify({'error':'Staff member not found'}), 404
    return jsonify({'success':True})

@app.route('/api/seed-staff', methods=['POST'])
def seed_staff():
    """Seed staff members. Admin PIN required."""
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    cur.execute("DELETE FROM staff_members")
    conn.commit()
    inserted=0
    for name, role, pin in STAFF_SEED:
        try:
            cur.execute("INSERT INTO staff_members (name,role,pin,email,active,created_at) VALUES (%s,%s,%s,NULL,1,%s)",
                (name, role, pin, now_central()))
            inserted+=1
        except Exception as e:
            print(f"Seed error for {name}: {e}")
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'inserted':inserted})

# ── StoreCentral ──────────────────────────────────────────────────────────────

ACCOUNTING_EMAIL = 'accountingdepartment@sandersbeachrentals.com'

STORE_ITEMS_SEED = [
    # (name, category, quantity, price)
    ("King Duvet Insert", "Bedroom/Bath", 7, 0),
    ("Queen Duvet Insert", "Bedroom/Bath", 4, 0),
    ("Twin Duvet Insert", "Bedroom/Bath", 4, 0),
    ("King Mattress Pad", "Bedroom/Bath", 2, 0),
    ("Queen Mattress Pad", "Bedroom/Bath", 2, 0),
    ("Twin Mattress Pad", "Bedroom/Bath", 15, 0),
    ("Standard Pillows", "Bedroom/Bath", 4, 0),
    ("King Pillows", "Bedroom/Bath", 12, 0),
    ("Toilet Brushes", "Bathrooms", 2, 0),
    ("Hair Dryers", "Bathrooms", 1, 0),
    ("Shower Liners 72x72", "Bathrooms", 2, 0),
    ("Shower Liners 72x78", "Bathrooms", 1, 0),
    ("Shower Liners 72x84", "Bathrooms", 2, 0),
    ("Shower Curtain", "Bathrooms", 2, 0),
    ("Shower Curtain Hooks", "Bathrooms", 0, 0),
    ("Blenders", "Kitchen", 0, 0),
    ("Hand Mixer", "Kitchen", 1, 0),
    ("Toaster", "Kitchen", 2, 0),
    ("Cookie Sheets", "Kitchen", 6, 0),
    ("Muffin Tins", "Kitchen", 1, 0),
    ("Cutting Boards", "Kitchen", 4, 0),
    ("Glass Bakeware (Pyrex)", "Kitchen", 17, 0),
    ("Can Opener", "Kitchen", 2, 0),
    ("Wine Opener", "Kitchen", 1, 0),
    ("Cheese Grater", "Kitchen", 1, 0),
    ("Knife Set", "Kitchen", 1, 0),
    ("Knife Sharpener", "Kitchen", 1, 0),
    ("Kettle", "Kitchen", 1, 0),
    ("Keurig", "Kitchen", 1, 0),
    ("Cuisinart Coffee Maker", "Kitchen", 1, 0),
    ("Cuisinart Carafe 12 Cup", "Kitchen", 6, 0),
    ("Cuisinart Carafe 14 Cup", "Kitchen", 2, 0),
    ("Skillets", "Kitchen", 1, 0),
    ("Cooking Pots", "Kitchen", 3, 0),
    ("Roasting Pans", "Kitchen", 2, 0),
    ("Cooking Utensil Sets", "Kitchen", 2, 0),
    ("Convection Oven", "Kitchen", 2, 0),
    ("Salad Forks", "Kitchen/Flatware", 36, 0),
    ("Dinner Forks", "Kitchen/Flatware", 48, 0),
    ("Teaspoon", "Kitchen/Flatware", 48, 0),
    ("Tablespoon", "Kitchen/Flatware", 48, 0),
    ("Butter Knife", "Kitchen/Flatware", 108, 0),
    ("Steak Knife", "Kitchen/Flatware", 84, 0),
    ("Tall Drinking Glasses", "Kitchen/Glasses", 111, 0),
    ("Short Drinking Glasses", "Kitchen/Glasses", 86, 0),
    ("Wine Glasses", "Kitchen/Glasses", 21, 0),
    ("Coffee Mugs", "Kitchen/Glasses", 22, 0),
    ("Serving Plates", "Kitchen/Dishware", 0, 0),
    ("Serving Bowls", "Kitchen/Dishware", 10, 0),
    ("Dinner Plates", "Kitchen/Dishware", 0, 0),
    ("Salad Plates", "Kitchen/Dishware", 0, 0),
    ("Bowls", "Kitchen/Dishware", 2, 0),
    ("Broom & Dust Pan Set", "Additional Housewares", 2, 0),
    ("Vacuum", "Additional Housewares", 1, 0),
    ("Iron", "Additional Housewares", 1, 0),
    ("Ironing Board", "Additional Housewares", 1, 0),
    ("Ironing Board Cover", "Additional Housewares", 2, 0),
    ("Wooden Hangers", "Additional Housewares", 105, 0),
]

@app.route('/api/store/items', methods=['GET'])
def get_store_items():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM store_items ORDER BY category, name")
    rows=cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/store/items', methods=['POST'])
def add_store_item():
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    name=data.get('name','').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO store_items (name,category,quantity,price,created_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (name, data.get('category','General'), int(data.get('quantity',0)), float(data.get('price',0)), now_central()))
    sid=cur.fetchone()['id']
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'id':sid})

@app.route('/api/store/items/<int:sid>', methods=['PUT'])
def update_store_item(sid):
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    cur.execute("UPDATE store_items SET name=%s,category=%s,price=%s WHERE id=%s",
        (data.get('name'), data.get('category','General'), float(data.get('price',0)), sid))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

@app.route('/api/store/checkout', methods=['POST'])
def store_checkout():
    """Check out a store item as loan or sold_out."""
    data=request.json or {}
    item_id=data.get('item_id')
    qty=int(data.get('quantity',1))
    property_address=data.get('property_address','').strip()
    performed_by=data.get('performed_by','').strip()
    performed_by_email=data.get('performed_by_email','').strip()
    transaction_type=data.get('transaction_type','sold_out')
    expected_return=data.get('expected_return_date','').strip() or None
    notes=data.get('notes','').strip() or None

    if not item_id or not performed_by or not property_address:
        return jsonify({'error':'item_id, performed_by, and property_address required'}), 400
    if transaction_type not in ('loan','sold_out'):
        return jsonify({'error':'transaction_type must be loan or sold_out'}), 400
    if transaction_type == 'loan' and not expected_return:
        return jsonify({'error':'expected_return_date required for loans'}), 400

    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM store_items WHERE id=%s",(item_id,))
    item=cur.fetchone()
    if not item: cur.close(); conn.close(); return jsonify({'error':'Item not found'}), 404
    if item['quantity'] < qty:
        cur.close(); conn.close()
        return jsonify({'error':f'Only {item["quantity"]} in stock'}), 400

    new_qty = item['quantity'] - qty
    cur.execute("UPDATE store_items SET quantity=%s WHERE id=%s",(new_qty, item_id))
    cur.execute("""INSERT INTO store_transactions
        (item_id,action,quantity,quantity_after,property_address,performed_by,performed_by_email,
         transaction_type,expected_return_date,notes,timestamp)
        VALUES (%s,'checkout',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (item_id,qty,new_qty,property_address,performed_by,performed_by_email,
         transaction_type,expected_return,notes,now_central()))
    tx_id=cur.fetchone()['id']
    conn.commit()

    # Send accounting email if sold out
    if transaction_type == 'sold_out':
        total_value = float(item['price'] or 0) * qty
        price_line = f"\nUnit Price: ${item['price']:.2f}\nTotal to Bill: ${total_value:.2f}" if item['price'] else "\n(No price on file — please confirm billing amount)"
        body = f"""STORE ITEM SOLD OUT — BILLING REQUIRED

Property: {property_address}
Item: {item['name']}
Category: {item['category']}
Quantity: {qty}
{price_line}

Checked out by: {performed_by}
Date: {now_central()}
{f'Notes: {notes}' if notes else ''}

This item has been marked as sold out and will remain at the property. Please bill the homeowner accordingly.

— SandersCentral StoreCentral"""

        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
          <div style="background:#95B9B8;padding:16px 20px;border-radius:8px 8px 0 0">
            <h2 style="color:#fff;margin:0;font-size:18px">🏪 StoreCentral — Billing Required</h2>
            <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals · SandersCentral</p>
          </div>
          <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
            <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:16px">
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600;width:40%">Property</td><td style="padding:8px;border-bottom:1px solid #eee">{property_address}</td></tr>
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Item</td><td style="padding:8px;border-bottom:1px solid #eee">{item['name']}</td></tr>
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Category</td><td style="padding:8px;border-bottom:1px solid #eee">{item['category']}</td></tr>
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Quantity</td><td style="padding:8px;border-bottom:1px solid #eee">{qty}</td></tr>
              {'<tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Unit Price</td><td style="padding:8px;border-bottom:1px solid #eee">$'+f"{item['price']:.2f}"+'</td></tr><tr><td style="padding:8px;background:#fef9e7;font-weight:700;color:#c0392b">Total to Bill</td><td style="padding:8px;border-bottom:1px solid #eee;font-weight:700;color:#c0392b;font-size:16px">$'+f"{total_value:.2f}"+'</td></tr>' if item['price'] else '<tr><td style="padding:8px;background:#fef9e7;font-weight:600;color:#c0392b">Billing Amount</td><td style="padding:8px;border-bottom:1px solid #eee;color:#c0392b">No price on file — please confirm</td></tr>'}
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Checked out by</td><td style="padding:8px;border-bottom:1px solid #eee">{performed_by}</td></tr>
              <tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Date</td><td style="padding:8px;border-bottom:1px solid #eee">{now_central()}</td></tr>
              {f'<tr><td style="padding:8px;background:#f9f9f9;font-weight:600">Notes</td><td style="padding:8px;border-bottom:1px solid #eee">{notes}</td></tr>' if notes else ''}
            </table>
            <div style="background:#fdecea;padding:12px;border-radius:6px;font-size:13px;color:#c0392b">
              <strong>Action required:</strong> Please bill the homeowner for this item.
            </div>
            <p style="margin:16px 0 0;font-size:11px;color:#aaa;text-align:center">SandersCentral · StoreCentral</p>
          </div>
        </div>"""

        send_email(
            f"BILLING REQUIRED: {item['name']} → {property_address}",
            body, to=ACCOUNTING_EMAIL, html_body=html
        )

    cur.close(); conn.close()
    return jsonify({'success':True,'transaction_id':tx_id,'new_quantity':new_qty})

@app.route('/api/store/loans', methods=['GET'])
def get_active_loans():
    """Get all currently active (unreturned) loans."""
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT st.*, si.name as item_name, si.category, si.price
        FROM store_transactions st
        JOIN store_items si ON si.id=st.item_id
        WHERE st.transaction_type='loan' AND st.returned_at IS NULL
        ORDER BY st.expected_return_date ASC""")
    rows=cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/store/return/<int:tx_id>', methods=['POST'])
def return_store_item(tx_id):
    """Mark a loaned item as returned."""
    data=request.json or {}
    notes=data.get('notes','').strip() or None
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM store_transactions WHERE id=%s",(tx_id,))
    tx=cur.fetchone()
    if not tx: cur.close(); conn.close(); return jsonify({'error':'Not found'}), 404
    if tx['returned_at']: cur.close(); conn.close(); return jsonify({'error':'Already returned'}), 400
    # Add quantity back to inventory
    cur.execute("UPDATE store_items SET quantity=quantity+%s WHERE id=%s",(tx['quantity'],tx['item_id']))
    cur.execute("UPDATE store_transactions SET returned_at=%s, notes=COALESCE(notes||' | '||%s,%s) WHERE id=%s",
        (now_central(), notes, notes, tx_id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

@app.route('/api/store/log', methods=['GET'])
def store_log():
    limit=int(request.args.get('limit',200))
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT st.*, si.name as item_name, si.category, si.price
        FROM store_transactions st JOIN store_items si ON si.id=st.item_id
        ORDER BY st.timestamp DESC LIMIT %s""",(limit,))
    rows=cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/store/check-overdue', methods=['POST'])
def check_store_overdue():
    """Flag loans past their expected return date and email the inspector."""
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT st.*, si.name as item_name
        FROM store_transactions st JOIN store_items si ON si.id=st.item_id
        WHERE st.transaction_type='loan' AND st.returned_at IS NULL
        AND st.overdue_alerted=0 AND st.expected_return_date < %s""",(now_central()[:10],))
    overdue=cur.fetchall()
    alerted=0
    for tx in overdue:
        body = f"OVERDUE LOAN ALERT\n\nItem: {tx['item_name']}\nProperty: {tx['property_address']}\nExpected return: {tx['expected_return_date']}\nChecked out by: {tx['performed_by']}\n\nPlease follow up to arrange return of this item.\n\n— SandersCentral StoreCentral"
        if tx['performed_by_email']:
            send_email(f"OVERDUE: {tx['item_name']} from {tx['property_address']}", body, to=tx['performed_by_email'])
        cur.execute("UPDATE store_transactions SET is_overdue=1, overdue_alerted=1 WHERE id=%s",(tx['id'],))
        alerted+=1
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'alerted':alerted})

@app.route('/api/seed-store', methods=['POST'])
def seed_store():
    """Seed store inventory. Admin PIN required."""
    data=request.json or {}
    if check_pin(str(data.get('pin',''))) != 'admin':
        return jsonify({'error':'Admin PIN required'}), 403
    conn=get_db(); cur=conn.cursor()
    cur.execute("DELETE FROM store_transactions")
    cur.execute("DELETE FROM store_items")
    conn.commit()
    inserted=0
    for name,category,qty,price in STORE_ITEMS_SEED:
        cur.execute("INSERT INTO store_items (name,category,quantity,price,created_at) VALUES (%s,%s,%s,%s,%s)",
            (name,category,qty,price,now_central()))
        inserted+=1
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'inserted':inserted})

# ── ForecastCentral ───────────────────────────────────────────────────────────

SARAH_EMAIL = 'sarahelizabeth@sandersbeachrentals.com'

SUPPLY_MAP = {
    11: ['Toilet Paper Rolls'],
    12: ['Bathroom Trash Liners'],
    13: ['Molton Brown Shampoo', 'Molton Brown Conditioner', 'Molton Brown Body Wash'],
    14: ['Molton Brown Bar Soap'],
    15: ['Kitchen Amenity Boxes'],
    # 16 = pool towels, skip
    17: ['Paper Towel Rolls'],
    18: ['Kitchen Trash Bags'],
    19: ['Round Coffee Filters'],
    20: ['Amavida Coffee Packs'],
    21: ['3oz Palmolive Bottles'],
    22: ['Dishwasher Pod Packs'],
    23: ['Kitchen Sponges'],
    24: ['10oz Tide Bottles'],
}

def parse_pack_list_csv(content):
    """Parse pack list CSV → {address: {supply_name: qty}}"""
    import csv, io
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    pack_list = {}
    for row in rows:
        if not row: continue
        first = row[0].strip().strip('"')
        if not first or not (first[0].isdigit() or first[0].isalpha()): continue
        # Must look like an address - has a number or starts with a digit
        if not any(c.isdigit() for c in first[:5]): continue
        addr = first.lower().strip()
        prop_name = row[1].strip() if len(row) > 1 else addr
        supplies = {}
        for col_idx, supply_names in SUPPLY_MAP.items():
            if col_idx >= len(row): continue
            val = row[col_idx].strip()
            try: qty = int(float(val)) if val else 0
            except: qty = 0
            for name in supply_names:
                supplies[name] = qty
        if any(v > 0 for v in supplies.values()):
            pack_list[addr] = {'property_name': prop_name, 'supplies': supplies}
    return pack_list

def parse_reservations_csv(content):
    """Parse reservation CSV → list of {lease_id, arrive, depart, unit, area}"""
    import csv, io
    reader = csv.reader(io.StringIO(content))
    reservations = []
    header_found = False
    for row in reader:
        if not row: continue
        if 'Lease ID' in row[0] or 'Lease ID' in (row[0] if row else ''):
            header_found = True
            continue
        if not header_found: continue
        if not row[0].strip() or not row[0].strip().isdigit(): continue
        try:
            reservations.append({
                'lease_id': row[0].strip(),
                'arrive': row[1].strip(),
                'depart': row[2].strip(),
                'area': row[4].strip() if len(row) > 4 else '',
                'unit': row[5].strip().strip('"').lower() if len(row) > 5 else ''
            })
        except: pass
    return reservations

def run_forecast(conn, date_from=None, date_to=None):
    """Calculate supply needs from pack list × turnovers, compare to HK inventory."""
    from collections import defaultdict
    from datetime import datetime
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Load pack list
    cur.execute("SELECT address, supplies FROM forecast_pack_list")
    pack_list = {row['address']: row['supplies'] for row in cur.fetchall()}

    # Load reservations with optional date filter
    q = "SELECT unit_address, arrive, depart FROM forecast_reservations WHERE 1=1"
    params = []
    if date_from:
        # Convert M/D/YYYY strings to dates for proper comparison
        q += " AND TO_DATE(arrive, 'MM/DD/YYYY') >= %s::date"; params.append(date_from)
    if date_to:
        q += " AND TO_DATE(arrive, 'MM/DD/YYYY') <= %s::date"; params.append(date_to)
    cur.execute(q, params)
    reservations = cur.fetchall()

    # Count turnovers per property
    turnovers = defaultdict(int)
    unmatched = set()
    for r in reservations:
        unit = r['unit_address'].lower().strip()
        if unit in pack_list:
            turnovers[unit] += 1
        else:
            unmatched.add(unit)

    # Sum supply needs
    needed = defaultdict(int)
    breakdown = defaultdict(list)  # supply → list of (property, turnovers, qty_per_turnover, subtotal)
    for unit, count in turnovers.items():
        if unit not in pack_list: continue
        supplies = pack_list[unit]
        if isinstance(supplies, str):
            import json
            supplies = json.loads(supplies)
        for supply_name, qty_per_turn in supplies.items():
            if qty_per_turn > 0:
                subtotal = qty_per_turn * count
                needed[supply_name] += subtotal
                breakdown[supply_name].append({
                    'property': unit,
                    'turnovers': count,
                    'per_turnover': qty_per_turn,
                    'subtotal': subtotal
                })

    # Load current HK inventory
    cur.execute("SELECT name, quantity FROM hk_supply_items")
    inventory = {row['name']: row['quantity'] for row in cur.fetchall()}

    # Load pending orders for HK
    cur.execute("""
        SELECT soi.item_name, SUM(soi.expected_units) as on_order
        FROM supply_order_items soi
        JOIN supply_orders so ON so.id = soi.order_id
        WHERE so.module = 'housekeeping' AND so.status = 'Ordered'
        GROUP BY soi.item_name
    """)
    on_order = {row['item_name']: row['on_order'] for row in cur.fetchall()}

    # Build results
    results = []
    for supply_name in sorted(needed.keys()):
        qty_needed = needed[supply_name]
        current_stock = inventory.get(supply_name, 0)
        on_order_qty = on_order.get(supply_name, 0)
        available = current_stock + on_order_qty
        shortfall = max(0, qty_needed - available)
        results.append({
            'supply_name': supply_name,
            'needed': qty_needed,
            'current_stock': current_stock,
            'on_order': on_order_qty,
            'available': available,
            'shortfall': shortfall,
            'ok': shortfall == 0,
            'breakdown': breakdown[supply_name][:5]  # top 5 properties
        })

    cur.close()
    return {
        'results': results,
        'total_turnovers': sum(turnovers.values()),
        'properties_matched': len(turnovers),
        'properties_unmatched': list(unmatched)[:10],
        'reservation_count': len(reservations)
    }


@app.route('/api/forecast/debug', methods=['GET'])
def forecast_debug():
    """Debug endpoint to check address matching."""
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT address FROM forecast_pack_list ORDER BY address LIMIT 10")
    pack_sample = [r['address'] for r in cur.fetchall()]
    cur.execute("SELECT unit_address FROM forecast_reservations ORDER BY unit_address LIMIT 10")
    res_sample = [r['unit_address'] for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) as cnt FROM forecast_pack_list")
    pack_count = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) as cnt FROM forecast_reservations")
    res_count = cur.fetchone()['cnt']
    # Check actual matches
    cur.execute("""SELECT COUNT(DISTINCT fr.unit_address) as matched
        FROM forecast_reservations fr
        JOIN forecast_pack_list fp ON LOWER(TRIM(fp.address)) = LOWER(TRIM(fr.unit_address))""")
    matched = cur.fetchone()['matched']
    cur.close(); conn.close()
    return jsonify({
        'pack_list_count': pack_count,
        'reservation_count': res_count,
        'matched_addresses': matched,
        'pack_list_sample': pack_sample,
        'reservation_sample': res_sample
    })

@app.route('/api/forecast/upload-packlist', methods=['POST'])
def upload_pack_list():
    """Upload and parse a pack list CSV."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig', errors='replace')
    pack_list = parse_pack_list_csv(content)
    if not pack_list:
        return jsonify({'error': 'No valid pack list data found in file'}), 400
    conn = get_db(); cur = conn.cursor()
    inserted = 0
    for addr, data in pack_list.items():
        import json
        cur.execute("""
            INSERT INTO forecast_pack_list (address, property_name, supplies, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (address) DO UPDATE SET
                property_name=EXCLUDED.property_name,
                supplies=EXCLUDED.supplies,
                updated_at=EXCLUDED.updated_at
        """, (addr, data['property_name'], json.dumps(data['supplies']), now_central()))
        inserted += 1
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success': True, 'properties_loaded': inserted})

@app.route('/api/forecast/upload-reservations', methods=['POST'])
def upload_reservations():
    """Upload and parse a reservations CSV."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig', errors='replace')
    reservations = parse_reservations_csv(content)
    if not reservations:
        return jsonify({'error': 'No valid reservation data found in file'}), 400
    conn = get_db(); cur = conn.cursor()
    # Replace all reservations on each upload (fresh data)
    cur.execute("DELETE FROM forecast_reservations")
    uploaded_at = now_central()
    for r in reservations:
        cur.execute("""
            INSERT INTO forecast_reservations (lease_id, arrive, depart, unit_address, area, uploaded_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (r['lease_id'], r['arrive'], r['depart'], r['unit'], r['area'], uploaded_at))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success': True, 'reservations_loaded': len(reservations)})

@app.route('/api/forecast/run', methods=['GET'])
def run_forecast_api():
    """Run the forecast calculation."""
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    conn = get_db()
    try:
        result = run_forecast(conn, date_from, date_to)
    finally:
        conn.close()
    return jsonify(result)

@app.route('/api/forecast/status', methods=['GET'])
def forecast_status():
    """Get counts of loaded pack list and reservations."""
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as cnt, MAX(updated_at) as last FROM forecast_pack_list")
    pl = cur.fetchone()
    cur.execute("""SELECT COUNT(*) as cnt, MAX(uploaded_at) as last,
        TO_CHAR(MIN(TO_DATE(arrive,'MM/DD/YYYY')),'MM/DD/YYYY') as min_date,
        TO_CHAR(MAX(TO_DATE(depart,'MM/DD/YYYY')),'MM/DD/YYYY') as max_date
        FROM forecast_reservations""")
    res = cur.fetchone()
    cur.close(); conn.close()
    return jsonify({
        'pack_list_count': pl['cnt'],
        'pack_list_updated': pl['last'],
        'reservation_count': res['cnt'],
        'reservations_updated': res['last'],
        'date_range': f"{res['min_date']} to {res['max_date']}" if res['min_date'] else None
    })


@app.route('/api/forecast/box-packing', methods=['GET'])
def box_packing():
    """Calculate how many amenity boxes can be packed vs how many are needed."""
    BOX_CONTENTS = {
        'Kitchen Trash Bags':      5,
        'Round Coffee Filters':    2,
        'Amavida Coffee Packs':    1,
        '3oz Palmolive Bottles':   1,
        'Dishwasher Pod Packs':    2,
        'Kitchen Sponges':         1,
        '10oz Tide Bottles':       1,
        'Kitchen Amenity Boxes':   1,
    }

    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Current inventory of each box ingredient
    names = list(BOX_CONTENTS.keys())
    cur.execute("SELECT name, quantity FROM hk_supply_items WHERE name = ANY(%s)", (names,))
    inventory = {row['name']: row['quantity'] for row in cur.fetchall()}

    # How many boxes each ingredient can support
    ingredient_limits = {}
    for item, per_box in BOX_CONTENTS.items():
        stock = inventory.get(item, 0)
        ingredient_limits[item] = stock // per_box if per_box > 0 else 0

    # Max boxes packable = minimum across all ingredients
    can_pack = min(ingredient_limits.values()) if ingredient_limits else 0
    bottleneck = min(ingredient_limits, key=ingredient_limits.get) if ingredient_limits else None

    # Boxes needed from forecast
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    q = """
        SELECT fp.supplies, COUNT(fr.id) as turnovers
        FROM forecast_reservations fr
        JOIN forecast_pack_list fp ON fp.address = fr.unit_address
        WHERE 1=1
    """
    params = []
    if date_from:
        q += " AND fr.arrive >= %s"; params.append(date_from)
    if date_to:
        q += " AND fr.arrive <= %s"; params.append(date_to)
    q += " GROUP BY fp.address, fp.supplies"
    cur.execute(q, params)
    rows = cur.fetchall()
    import json
    boxes_needed = 0
    for row in rows:
        supplies = row['supplies'] if isinstance(row['supplies'], dict) else json.loads(row['supplies'])
        boxes_per_turn = supplies.get('Kitchen Amenity Boxes', 0)
        boxes_needed += boxes_per_turn * row['turnovers']

    shortfall = max(0, boxes_needed - can_pack)

    # Build per-ingredient detail
    ingredients = []
    for item, per_box in BOX_CONTENTS.items():
        stock = inventory.get(item, 0)
        needed_total = per_box * boxes_needed
        can_support = ingredient_limits[item]
        short = max(0, needed_total - stock)
        ingredients.append({
            'item': item,
            'per_box': per_box,
            'in_stock': stock,
            'needed_total': needed_total,
            'can_support_boxes': can_support,
            'short': short,
            'is_bottleneck': item == bottleneck,
            'ok': short == 0
        })

    cur.close(); conn.close()
    return jsonify({
        'boxes_needed': boxes_needed,
        'can_pack': can_pack,
        'shortfall': shortfall,
        'bottleneck': bottleneck,
        'ingredients': ingredients
    })

@app.route('/api/forecast/email-sarah', methods=['POST'])
def email_sarah_forecast():
    """Email Sarah the forecast shortfall report."""
    date_from = request.json.get('from') if request.json else None
    date_to = request.json.get('to') if request.json else None
    conn = get_db()
    try:
        result = run_forecast(conn, date_from, date_to)
    finally:
        conn.close()

    shortfalls = [r for r in result['results'] if not r['ok']]
    if not shortfalls:
        subject = "✅ Housekeeping Supply Forecast — All Items Sufficient"
        body_text = f"Good news! Based on {result['reservation_count']} upcoming reservations, all housekeeping supplies are sufficiently stocked."
    else:
        subject = f"⚠️ Housekeeping Supply Forecast — {len(shortfalls)} Items Need Ordering"
        body_text = f"Based on {result['reservation_count']} upcoming reservations across {result['properties_matched']} properties:\n\n"
        body_text += "ITEMS THAT NEED TO BE ORDERED:\n"
        for r in shortfalls:
            body_text += f"  {r['supply_name']}: need {r['needed']}, have {r['current_stock']} + {r['on_order']} on order = {r['available']} available. Short by {r['shortfall']}\n"

    # Build HTML version
    shortfall_rows = ''.join(
        f'<tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:600">{r["supply_name"]}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{r["needed"]}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{r["current_stock"]}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{r["on_order"]}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:center;color:#c0392b;font-weight:700">{r["shortfall"]}</td></tr>'
        for r in shortfalls
    ) if shortfalls else '<tr><td colspan="5" style="padding:16px;text-align:center;color:#2d7a4f">All items are sufficiently stocked!</td></tr>'

    ok_rows = ''.join(
        f'<tr><td style="padding:6px;border-bottom:1px solid #eee">{r["supply_name"]}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;text-align:center">{r["needed"]}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #eee;text-align:center;color:#2d7a4f">✓ {r["available"]}</td></tr>'
        for r in result['results'] if r['ok']
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:700px;margin:0 auto;padding:20px">
      <div style="background:#95B9B8;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Housekeeping Supply Forecast</h2>
        <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals · SandersCentral</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        <p style="color:#444;margin:0 0 16px">Based on <strong>{result['reservation_count']} upcoming reservations</strong> across <strong>{result['properties_matched']} properties</strong>.</p>
        {'<div style="background:#fdecea;padding:12px;border-radius:6px;margin-bottom:16px"><strong style="color:#c0392b">⚠️ '+str(len(shortfalls))+' items need to be ordered before supply runs out.</strong></div>' if shortfalls else '<div style="background:#e8f5ee;padding:12px;border-radius:6px;margin-bottom:16px"><strong style="color:#2d7a4f">✅ All items are sufficiently stocked for the forecast period.</strong></div>'}
        {'<h3 style="font-size:14px;margin:0 0 8px">Items to Order</h3><table style="width:100%;border-collapse:collapse;font-size:13px"><tr style="background:#fdecea"><th style="padding:8px;text-align:left">Item</th><th style="padding:8px">Need</th><th style="padding:8px">In Stock</th><th style="padding:8px">On Order</th><th style="padding:8px;color:#c0392b">Short By</th></tr>' + shortfall_rows + '</table>' if shortfalls else ''}
        <h3 style="font-size:14px;margin:16px 0 8px">All Items Summary</h3>
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="background:#f5f5f5"><th style="padding:6px;text-align:left">Item</th><th style="padding:6px">Needed</th><th style="padding:6px">Available</th></tr>
          {ok_rows}
        </table>
        <p style="margin:16px 0 0;font-size:12px;color:#aaa;text-align:center">SandersCentral · ForecastCentral</p>
      </div>
    </div>"""

    sent = send_email(subject, body_text, to=SARAH_EMAIL, html_body=html)
    return jsonify({'success': True, 'email_sent': sent, 'shortfalls': len(shortfalls)})


# ── PackCentral (stub — deduction trigger) ────────────────────────────────────

@app.route('/api/pack-home', methods=['POST'])
def pack_home():
    """Deduct HK inventory when a home is packed for a reservation.
    Expects: unit_address, packed_by, reservation_id (optional)"""
    data = request.json or {}
    unit_address = data.get('unit_address','').strip().lower()
    packed_by = data.get('packed_by','').strip()
    if not unit_address or not packed_by:
        return jsonify({'error':'unit_address and packed_by required'}), 400

    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Look up pack list for this property
    cur.execute("SELECT supplies FROM forecast_pack_list WHERE address=%s", (unit_address,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({'error': f'No pack list found for {unit_address}'}), 404

    import json
    supplies = row['supplies'] if isinstance(row['supplies'], dict) else json.loads(row['supplies'])

    deductions = []
    not_found = []
    for supply_name, qty in supplies.items():
        if not qty: continue
        cur.execute("SELECT id, quantity FROM hk_supply_items WHERE name=%s", (supply_name,))
        item = cur.fetchone()
        if not item:
            not_found.append(supply_name)
            continue
        new_qty = max(0, item['quantity'] - qty)
        cur.execute("UPDATE hk_supply_items SET quantity=%s WHERE id=%s", (new_qty, item['id']))
        cur.execute("""INSERT INTO hk_supply_transactions
            (supply_id, action, quantity, quantity_after, performed_by, timestamp, notes)
            VALUES (%s,'take',%s,%s,%s,%s,%s)""",
            (item['id'], qty, new_qty, packed_by, now_central(),
             f"Auto-deducted: packed {unit_address}"))
        deductions.append({'item': supply_name, 'deducted': qty, 'remaining': new_qty})

    conn.commit(); cur.close(); conn.close()
    return jsonify({'success': True, 'deductions': deductions, 'not_found': not_found})

# ── InventoryCount ────────────────────────────────────────────────────────────

@app.route('/api/inventory-counts', methods=['GET'])
def get_inventory_counts():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,areas,started_at,item_count,variances,created_at FROM inventory_counts ORDER BY created_at DESC LIMIT 50")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/inventory-counts', methods=['POST'])
def save_inventory_count():
    data=request.json or {}
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO inventory_counts (areas,started_at,item_count,variances,details,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (data.get('areas',''), data.get('started_at',''), int(data.get('item_count',0)), int(data.get('variances',0)), data.get('details','{}'), now_central()))
    row=cur.fetchone(); conn.commit(); cur.close(); conn.close(); return jsonify({'id':row['id']})

@app.route('/api/inventory-counts/<int:cid>', methods=['GET'])
def get_inventory_count(cid):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM inventory_counts WHERE id=%s",(cid,)); row=cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'error':'Not found'}),404
    return jsonify(row)

@app.route('/api/inventory-counts/<int:cid>/email', methods=['POST'])
def email_inventory_count(cid):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM inventory_counts WHERE id=%s",(cid,)); row=cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'error':'Not found'}),404
    details=json.loads(row['details'] or '{}'); variances=details.get('variances',[])
    lines=['SBR Linens — Inventory Count Report','='*40,f"Date: {row['started_at']}","Areas counted: "+row['areas'],f"Total items counted: {row['item_count']}",f"Variances found: {row['variances']}",""]
    if variances:
        lines.append("VARIANCES:")
        for v in variances:
            if v.get('type')=='loaner': lines.append(f"  {v['label']}: {v['counted']} (expected {v['expectedLabel']})")
            else: diff=v.get('diff',0); lines.append(f"  {v['label']}: counted {v.get('counted')} {v.get('unit','')} vs expected {v.get('expected')} ({'+'if diff>0 else ''}{diff})")
    else: lines.append("No variances — all items matched!")
    sent=send_email(f"Inventory Count Report — {row['started_at']}",'\n'.join(lines))
    return jsonify({'success':True,'email_sent':sent})

# ── PO Requests ───────────────────────────────────────────────────────────────

@app.route('/api/po-requests', methods=['GET'])
def get_po_requests():
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM po_requests ORDER BY submitted_at DESC LIMIT 200")
    rows=cur.fetchall(); cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/po-requests', methods=['POST'])
def create_po_request():
    data=request.json or {}
    required=['employee_name','employee_email','vendor','amount','category','description','date_needed']
    for f in required:
        if not data.get(f): return jsonify({'error':f'{f} is required'}),400
    try: amount=float(data['amount'])
    except: return jsonify({'error':'Invalid amount'}),400
    category = data['category'].strip()
    initial_stage = 'chuck' if category in TWO_STAGE_CATEGORIES else 'final'
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO po_requests
        (employee_name,employee_email,vendor,amount,category,description,date_needed,urgency,status,submitted_at,stage)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'Pending',%s,%s) RETURNING id""",
        (data['employee_name'].strip(), data['employee_email'].strip().lower(),
         data['vendor'].strip(), amount, category,
         data['description'].strip(), data['date_needed'].strip(),
         data.get('urgency','Routine'), now_central(), initial_stage))
    row=cur.fetchone(); conn.commit(); req_id=row['id']
    cur.execute("SELECT * FROM po_requests WHERE id=%s",(req_id,)); req=cur.fetchone()
    cur.close(); conn.close()
    try:
        send_po_approver_email(req)
    except Exception as e:
        import traceback
        print(f'[PO EMAIL CALL FAILED] {e}', flush=True)
        traceback.print_exc()
    return jsonify({'success':True,'id':req_id})

@app.route('/api/po-requests/<int:rid>/decide', methods=['POST'])
def decide_po_request(rid):
    data=request.json or {}
    status=data.get('status','')
    if status not in ('Approved','Denied'): return jsonify({'error':'Status must be Approved or Denied'}),400
    approver_name=data.get('approver_name','').strip()
    notes=data.get('notes','').strip()
    if not approver_name: return jsonify({'error':'Approver name is required'}),400
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM po_requests WHERE id=%s",(rid,)); req=cur.fetchone()
    if not req: cur.close(); conn.close(); return jsonify({'error':'Request not found'}),404
    if req['status'] != 'Pending': cur.close(); conn.close(); return jsonify({'error':'Already decided'}),400
    stage = req.get('stage') or 'final'
    ts=now_central()

    if stage == 'chuck':
        if approver_name != CHUCK_NAME:
            cur.close(); conn.close(); return jsonify({'error':'Only Chuck Howard can decide this stage'}),403
        if status == 'Denied':
            cur.execute("UPDATE po_requests SET status='Denied',approver_notes=%s,approved_by=%s,decided_at=%s WHERE id=%s",(notes,approver_name,ts,rid))
            conn.commit()
            cur.execute("SELECT * FROM po_requests WHERE id=%s",(rid,)); updated=cur.fetchone()
            cur.close(); conn.close()
            send_po_decision_email(updated)
            return jsonify({'success':True})
        # Chuck approved -> advance to final stage, notify Sabrina + Sarah, request stays Pending
        cur.execute("UPDATE po_requests SET stage='final',stage1_approved_by=%s,stage1_notes=%s,stage1_decided_at=%s WHERE id=%s",(approver_name,notes,ts,rid))
        conn.commit()
        cur.execute("SELECT * FROM po_requests WHERE id=%s",(rid,)); updated=cur.fetchone()
        cur.close(); conn.close()
        try: send_po_approver_email(updated)
        except Exception as e: print(f'[PO STAGE2 EMAIL FAILED] {e}', flush=True)
        return jsonify({'success':True})

    # stage == 'final'
    if approver_name not in (PO_APPROVER_1_NAME, PO_APPROVER_2_NAME):
        cur.close(); conn.close(); return jsonify({'error':'Only Sabrina Renshaw or Sarah Jordan can decide this stage'}),403
    cur.execute("UPDATE po_requests SET status=%s,approver_notes=%s,approved_by=%s,decided_at=%s WHERE id=%s",(status,notes,approver_name,ts,rid))
    conn.commit()
    cur.execute("SELECT * FROM po_requests WHERE id=%s",(rid,)); updated=cur.fetchone()
    cur.close(); conn.close()
    send_po_decision_email(updated)
    return jsonify({'success':True})


# ── Startup ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port=int(os.environ.get('PORT',3000))
    app.run(host='0.0.0.0', port=port, debug=False)
