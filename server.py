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
    ]:
        try: cur.execute(col_sql)
        except Exception as e: print(f'Migration note: {e}')
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
    if not SENDGRID_API_KEY:
        print(f'[EMAIL SKIPPED - no API key] {subject}'); return False
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
            print(f'[EMAIL SENT] {subject} → {recipients} (status {resp.status})')
        return True
    except Exception as e:
        print(f'[EMAIL ERROR] {subject}: {e}')
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
    subject = f"{urgency_emoji} New PO Request — {req['vendor']} (${req['amount']:.2f})"
    approvals_url = 'https://sbrlinens.up.railway.app/po-approvals'
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <div style="background:#95B9B8;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">New Purchase Request</h2>
        <p style="color:#fff;margin:4px 0 0;font-size:13px;opacity:0.9">Sanders Beach Rentals</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
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
    return send_email(subject, plain, to=[PO_APPROVER_1_EMAIL, PO_APPROVER_2_EMAIL], html_body=html)

def send_po_decision_email(req):
    status = req['status']
    emoji = '✅' if status == 'Approved' else '❌'
    subject = f"{emoji} Your Purchase Request has been {status}"
    color = '#2d7a4f' if status == 'Approved' else '#c0392b'
    bg = '#e8f5ee' if status == 'Approved' else '#fdecea'
    notes_html = f'<tr><td style="padding:8px 0;color:#888;vertical-align:top">Notes</td><td style="padding:8px 0">{req["approver_notes"]}</td></tr>' if req.get('approver_notes') else ''
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
    data=request.json or {}; cleaner_id=data.get('cleaner_id')
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,h.name AS home_name FROM bags b JOIN homes h ON h.id=b.home_id WHERE b.id=%s",(bag_id.upper(),))
    bag=cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}),404
    if bag['status'] in ('out','staged'): cur.close(); conn.close(); return jsonify({'error':'Already staged or checked out'}),400
    ts=now_central()
    cur.execute("UPDATE bags SET status='staged',cleaner_id=%s,staged_at=%s,picked_up_at=NULL,overdue_alerted=0 WHERE id=%s",(cleaner_id,ts,bag_id.upper()))
    cur.execute("INSERT INTO transactions (bag_id,home_id,cleaner_id,action,timestamp) VALUES (%s,%s,%s,'Staged',%s)",(bag_id.upper(),bag['home_id'],cleaner_id,ts))
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
    data=request.json or {}; notes=data.get('notes','')
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,h.name AS home_name,c.name AS cleaner_name FROM bags b JOIN homes h ON h.id=b.home_id LEFT JOIN cleaners c ON c.id=b.cleaner_id WHERE b.id=%s",(bag_id.upper(),))
    bag=cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}),404
    if bag['status']=='in': cur.close(); conn.close(); return jsonify({'error':'Already checked in'}),400
    ts=now_central()
    cur.execute("INSERT INTO transactions (bag_id,home_id,cleaner_id,action,timestamp,notes) VALUES (%s,%s,%s,'Returned',%s,%s)",(bag_id.upper(),bag['home_id'],bag['cleaner_id'],ts,notes))
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
               c.name AS cleaner_name, t.action, t.timestamp AS ts, t.notes
        FROM transactions t JOIN homes h ON h.id=t.home_id LEFT JOIN cleaners c ON c.id=t.cleaner_id
        UNION ALL
        SELECT lt.id,'loaner' AS activity_type, lt.loaner_id AS bag_id, h.name AS home_name,
               s.name AS cleaner_name, lt.action, lt.timestamp AS ts, lt.notes
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
    data=request.json or {}; staff_id=data.get('staff_id'); home_id=data.get('home_id')
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT l.*,s.name AS sname,h.name AS hname FROM loaners l LEFT JOIN loaner_staff s ON s.id=%s LEFT JOIN homes h ON h.id=%s WHERE l.id=%s",(staff_id,home_id,loaner_id.upper()))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if row['status']=='out': cur.close(); conn.close(); return jsonify({'error':'Already deployed'}),400
    ts=now_central()
    cur.execute("UPDATE loaners SET status='out',staff_id=%s,home_id=%s,checked_out=%s WHERE id=%s",(staff_id,home_id,ts,loaner_id.upper()))
    cur.execute("INSERT INTO loaner_transactions (loaner_id,staff_id,home_id,action,timestamp) VALUES (%s,%s,%s,'Deployed',%s)",(loaner_id.upper(),staff_id,home_id,ts))
    conn.commit()
    staff_name=row.get('sname','Staff'); home_name=row.get('hname','Unknown')
    cur.close(); conn.close(); return jsonify({'success':True,'item':row['name'],'staff':staff_name,'home':home_name})

@app.route('/api/loaner/<path:loaner_id>/retrieve', methods=['POST'])
def retrieve_loaner(loaner_id):
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT l.*,h.name AS home_name FROM loaners l LEFT JOIN homes h ON h.id=l.home_id WHERE l.id=%s",(loaner_id.upper(),))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'error':'Item not found'}),404
    if row['status']=='in': cur.close(); conn.close(); return jsonify({'error':'Already in warehouse'}),400
    ts=now_central()
    cur.execute("INSERT INTO loaner_transactions (loaner_id,staff_id,home_id,action,timestamp) VALUES (%s,%s,%s,'Retrieved',%s)",(loaner_id.upper(),row['staff_id'],row['home_id'],ts))
    cur.execute("UPDATE loaners SET status='in',staff_id=NULL,home_id=NULL,checked_out=NULL WHERE id=%s",(loaner_id.upper(),))
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
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO po_requests
        (employee_name,employee_email,vendor,amount,category,description,date_needed,urgency,status,submitted_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'Pending',%s) RETURNING id""",
        (data['employee_name'].strip(), data['employee_email'].strip().lower(),
         data['vendor'].strip(), amount, data['category'].strip(),
         data['description'].strip(), data['date_needed'].strip(),
         data.get('urgency','Routine'), now_central()))
    row=cur.fetchone(); conn.commit(); req_id=row['id']
    cur.execute("SELECT * FROM po_requests WHERE id=%s",(req_id,)); req=cur.fetchone()
    cur.close(); conn.close()
    send_po_approver_email(req)
    return jsonify({'success':True,'id':req_id})

@app.route('/api/po-requests/<int:rid>/decide', methods=['POST'])
def decide_po_request(rid):
    data=request.json or {}
    status=data.get('status','')
    if status not in ('Approved','Denied'): return jsonify({'error':'Status must be Approved or Denied'}),400
    approver_name=data.get('approver_name','Sanders Beach Rentals Management').strip()
    notes=data.get('notes','').strip()
    conn=get_db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM po_requests WHERE id=%s",(rid,)); req=cur.fetchone()
    if not req: cur.close(); conn.close(); return jsonify({'error':'Request not found'}),404
    if req['status'] != 'Pending': cur.close(); conn.close(); return jsonify({'error':'Already decided'}),400
    ts=now_central()
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
