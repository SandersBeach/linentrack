import os, json
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime
import psycopg2
import psycopg2.extras
import pytz

app = Flask(__name__, static_folder='public', static_url_path='')
CENTRAL = pytz.timezone('America/Chicago')

def now_central():
    return datetime.now(pytz.utc).astimezone(CENTRAL).strftime('%Y-%m-%d %H:%M:%S')

_DB_URL = (os.environ.get('DATABASE_URL') or
           os.environ.get('DATABASE_PUBLIC_URL') or
           'postgresql://postgres:vPzxJamFkEIxprlqLqPLdUgYFDkTZicQ@acela.proxy.rlwy.net:57535/railway')

def get_db():
    db_url = _DB_URL
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    conn = psycopg2.connect(db_url, sslmode='require')
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS homes (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS cleaners (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS bags (
            id TEXT PRIMARY KEY,
            home_id INTEGER NOT NULL REFERENCES homes(id),
            status TEXT DEFAULT 'in',
            cleaner_id INTEGER REFERENCES cleaners(id),
            checked_out TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS maintenance_staff (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS loaners (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT DEFAULT 'in',
            home_id INTEGER REFERENCES homes(id),
            staff_id INTEGER REFERENCES maintenance_staff(id),
            deployed_at TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS activity (
            id SERIAL PRIMARY KEY,
            ts TEXT DEFAULT '',
            action TEXT NOT NULL,
            bag_id TEXT NOT NULL,
            home_name TEXT,
            cleaner_name TEXT,
            notes TEXT,
            activity_type TEXT DEFAULT 'linen'
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()

    for key, val in [('warehouse_pin','1234'),('admin_pin','9999'),('maintenance_pin','5678')]:
        cur.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO NOTHING", (key, val))
    conn.commit()

    # Seed homes if empty
    cur.execute("SELECT COUNT(*) FROM homes")
    if cur.fetchone()[0] == 0:
        homes = [
            ("5 Pond Cypress Way","HOME-01"),("9 Running Oak Circle","HOME-02"),
            ("12 Viridian Park Drive","HOME-03"),("19 Muhly Circle","HOME-04"),
            ("20 Tall Timber Court","HOME-05"),("21 Chanel Court","HOME-06"),
            ("22 Flatwood Street","HOME-07"),("25 Rain Lily Lane","HOME-08"),
            ("25 Lake District Lane","HOME-09"),("29 Royal Fern Way","HOME-10"),
            ("31 Bluejack Street","HOME-11"),("35 Suzanne Drive","HOME-12"),
            ("37 Red Cedar Way","HOME-13"),("37 Compass Point II, Unit 106","HOME-14"),
            ("43 Sand Hill Circle","HOME-15"),("44 Thicket Circle","HOME-16"),
            ("46 Pine Needle Way","HOME-17"),("49 Bluejack Street","HOME-18"),
            ("51 Mistflower Lane","HOME-19"),("53 Muhly Circle","HOME-20"),
            ("65 Pond Cypress Circle","HOME-21"),("70 Scrub Oak Circle","HOME-22"),
            ("70 Sunset Ridge Lane","HOME-23"),("72 Needlerush Drive","HOME-24"),
            ("73 Holly Street","HOME-25"),("73 Pond Cypress Circle","HOME-26"),
            ("75 East Summersweet Lane","HOME-27"),("80 Scrub Oak Circle","HOME-28"),
            ("86 Sunset Ridge Lane","HOME-29"),("90 Flatwood Street","HOME-30"),
            ("91 Bluejack Street","HOME-31"),("93 Needlerush Drive","HOME-32"),
            ("97 East Summersweet Lane","HOME-33"),("99 Pond Cypress Way","HOME-34"),
            ("100 Tumblehome Way","HOME-35"),("109 Dandelion Drive","HOME-36"),
            ("124 Sunset Ridge Lane","HOME-37"),("134 Royal Fern Way","HOME-38"),
            ("138 East Royal Fern Way","HOME-39"),("142 Mystic Cobalt Street","HOME-40"),
            ("157 Sunflower Street","HOME-41"),("176 Red Cedar Way","HOME-42"),
            ("179 Pine Needle Way","HOME-43"),("184 East Royal Fern Way","HOME-44"),
            ("194 Spartina Circle","HOME-45"),("202 East Royal Fern Way","HOME-46"),
            ("209 Western Lake Drive","HOME-47"),("254 Spartina Circle","HOME-48"),
            ("255 Garfield Street","HOME-49"),("260 Needlerush Drive","HOME-50"),
            ("262 Garfield Street","HOME-51"),("263 Magnolia Street","HOME-52"),
            ("271 Red Cedar Way","HOME-53"),("295 Salt Box Lane","HOME-54"),
            ("349 Needlerush Drive","HOME-55"),("369 Spartina Circle","HOME-56"),
            ("379 East Royal Fern Way","HOME-57"),("394 Western Lake Drive","HOME-58"),
            ("406 Red Cedar Way","HOME-59"),("410 Pine Needle Way","HOME-60"),
            ("422 Pine Needle Way","HOME-61"),("428 Red Cedar Way","HOME-62"),
            ("433 Pine Needle Way","HOME-63"),("442 East Royal Fern Way","HOME-64"),
            ("446 Western Lake Drive","HOME-65"),("672 Western Lake Drive","HOME-66"),
            ("728 Western Lake Drive","HOME-67"),("1217 Western Lake Drive","HOME-68"),
            ("1352 Western Lake Drive","HOME-69"),("1735 East Co Hwy 30A #203","HOME-70"),
            ("2060 E Co Hwy 30A","HOME-71"),("2743 E Co Hwy 30A, Unit 303","HOME-72"),
            ("2912 E. Co Hwy 30A","HOME-73"),
        ]
        for name, code in homes:
            cur.execute("INSERT INTO homes(name,code) VALUES(%s,%s)", (name, code))
        conn.commit()

        cleaners = ["A&D Cleaning","Dream Clean","Elizabeth Varo","Gesiane Barbosa",
                    "Jennifer Hawkins","Juan Carlos Rocha","Mario Cruz","Miranda Edney","Monserrat Guzman"]
        for c in cleaners:
            cur.execute("INSERT INTO cleaners(name) VALUES(%s)", (c,))
        conn.commit()

        cur.execute("SELECT id, code FROM homes")
        all_homes = cur.fetchall()
        for home_id, code in all_homes:
            for i in range(1, 11):
                bag_id = f"{code}-{chr(64+i)}"
                cur.execute("INSERT INTO bags(id,home_id,status) VALUES(%s,%s,'in') ON CONFLICT(id) DO NOTHING", (bag_id, home_id))
        conn.commit()

    # Seed maintenance staff if empty
    cur.execute("SELECT COUNT(*) FROM maintenance_staff")
    if cur.fetchone()[0] == 0:
        staff = ["Chris Cook","Keith Evans","Chuck Howard","Jonathan Inabnet","Shawn Stidham"]
        for s in staff:
            cur.execute("INSERT INTO maintenance_staff(name) VALUES(%s)", (s,))
        conn.commit()

    # Seed loaners if empty
    cur.execute("SELECT COUNT(*) FROM loaners")
    if cur.fetchone()[0] == 0:
        loaners = [
            ("LOAN-FAN-40","Box Fan 40","Fan"),("LOAN-FAN-41","Box Fan 41","Fan"),
            ("LOAN-FAN-42","Box Fan 42","Fan"),("LOAN-FAN-43","Box Fan 43","Fan"),
            ("LOAN-FAN-44","Box Fan 44","Fan"),("LOAN-FAN-45","Box Fan 45","Fan"),
            ("LOAN-FAN-46","Box Fan 46","Fan"),("LOAN-FAN-47","Box Fan 47","Fan"),
            ("LOAN-FAN-48","Box Fan 48","Fan"),("LOAN-FAN-49","Box Fan 49","Fan"),
            ("LOAN-FAN-50","Box Fan 50","Fan"),
            ("LOAN-FFAN-01","Floor Fan 1","Fan"),("LOAN-FFAN-02","Floor Fan 2","Fan"),
            ("LOAN-MW-51","Microwave 51","Microwave"),("LOAN-MW-52","Microwave 52","Microwave"),
            ("LOAN-FRIDGE-01","Fridge 1","Fridge"),("LOAN-FRIDGE-02","Fridge 2","Fridge"),
            ("LOAN-FRIDGE-03","Fridge 3","Fridge"),("LOAN-FRIDGE-04","Fridge 4","Fridge"),
            ("LOAN-AC-22","AC 22","AC"),("LOAN-AC-23","AC 23","AC"),("LOAN-AC-24","AC 24","AC"),
            ("LOAN-GRILL-01","Temp Grill","Grill"),("LOAN-DRYER-61","Dryer 61","Dryer"),
        ]
        for lid, lname, lcat in loaners:
            cur.execute("INSERT INTO loaners(id,name,category,status) VALUES(%s,%s,%s,'in')", (lid, lname, lcat))
        conn.commit()

    cur.close(); conn.close()

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth', methods=['POST'])
def auth():
    pin = request.json.get('pin','')
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings WHERE key IN ('warehouse_pin','admin_pin','maintenance_pin')")
    pins = {row[0]: row[1] for row in cur.fetchall()}
    cur.close(); conn.close()
    if pin == pins.get('admin_pin'): return jsonify({'role':'admin'})
    if pin == pins.get('warehouse_pin'): return jsonify({'role':'warehouse'})
    if pin == pins.get('maintenance_pin'): return jsonify({'role':'maintenance'})
    return jsonify({'error':'Wrong PIN'}), 401

# ── Bag lookup ────────────────────────────────────────────────────────────────
@app.route('/api/bag/<bag_id>')
def get_bag(bag_id):
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT b.*, h.name as home_name, h.code as home_code, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        WHERE b.id=%s
    """, (bag_id.upper(),))
    bag = cur.fetchone()
    cur.close(); conn.close()
    if not bag: return jsonify({'error':'Bag not found'}), 404
    return jsonify(dict(bag))

@app.route('/api/bag/<bag_id>/checkout', methods=['POST'])
def checkout(bag_id):
    data = request.json
    bid = bag_id.upper()
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM bags WHERE id=%s", (bid,))
    bag = cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}), 404
    if bag['status'] == 'out': cur.close(); conn.close(); return jsonify({'error':'Already checked out'}), 400
    cur.execute("SELECT * FROM cleaners WHERE id=%s", (data['cleaner_id'],))
    cleaner = cur.fetchone()
    cur.execute("SELECT * FROM homes WHERE id=%s", (bag['home_id'],))
    home = cur.fetchone()
    cur.execute("UPDATE bags SET status='out', cleaner_id=%s, checked_out=%s WHERE id=%s",
        (data['cleaner_id'], now_central(), bid))
    cur.execute("INSERT INTO activity(ts,action,bag_id,home_name,cleaner_name,activity_type) VALUES(%s,%s,%s,%s,%s,'linen')",
        (now_central(), 'Sent out', bid, home['name'], cleaner['name']))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'bag_id':bid,'home':home['name'],'cleaner':cleaner['name']})

@app.route('/api/bag/<bag_id>/checkin', methods=['POST'])
def checkin(bag_id):
    data = request.json or {}
    bid = bag_id.upper()
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT b.*, h.name as home_name, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        WHERE b.id=%s
    """, (bid,))
    bag = cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Bag not found'}), 404
    if bag['status'] == 'in': cur.close(); conn.close(); return jsonify({'error':'Already at warehouse'}), 400
    notes = data.get('notes','')
    cur.execute("UPDATE bags SET status='in', cleaner_id=NULL, checked_out=NULL, notes=%s WHERE id=%s", (notes, bid))
    cur.execute("INSERT INTO activity(ts,action,bag_id,home_name,cleaner_name,notes,activity_type) VALUES(%s,%s,%s,%s,%s,%s,'linen')",
        (now_central(), 'Returned', bid, bag['home_name'], bag['cleaner_name'], notes))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'bag_id':bid,'home':bag['home_name'],'cleaner':bag['cleaner_name']})

# ── Loaner lookup ─────────────────────────────────────────────────────────────
@app.route('/api/loaner/<loaner_id>')
def get_loaner(loaner_id):
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT l.*, h.name as home_name, s.name as staff_name
        FROM loaners l
        LEFT JOIN homes h ON l.home_id=h.id
        LEFT JOIN maintenance_staff s ON l.staff_id=s.id
        WHERE l.id=%s
    """, (loaner_id.upper(),))
    loaner = cur.fetchone()
    cur.close(); conn.close()
    if not loaner: return jsonify({'error':'Loaner item not found'}), 404
    return jsonify(dict(loaner))

@app.route('/api/loaner/<loaner_id>/deploy', methods=['POST'])
def deploy_loaner(loaner_id):
    data = request.json
    lid = loaner_id.upper()
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM loaners WHERE id=%s", (lid,))
    loaner = cur.fetchone()
    if not loaner: cur.close(); conn.close(); return jsonify({'error':'Item not found'}), 404
    if loaner['status'] == 'out': cur.close(); conn.close(); return jsonify({'error':'Already deployed'}), 400
    cur.execute("SELECT * FROM maintenance_staff WHERE id=%s", (data['staff_id'],))
    staff = cur.fetchone()
    cur.execute("SELECT * FROM homes WHERE id=%s", (data['home_id'],))
    home = cur.fetchone()
    cur.execute("UPDATE loaners SET status='out', staff_id=%s, home_id=%s, deployed_at=%s WHERE id=%s",
        (data['staff_id'], data['home_id'], now_central(), lid))
    cur.execute("INSERT INTO activity(ts,action,bag_id,home_name,cleaner_name,activity_type) VALUES(%s,%s,%s,%s,%s,'loaner')",
        (now_central(), 'Deployed', lid, home['name'], staff['name']))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'item':loaner['name'],'home':home['name'],'staff':staff['name']})

@app.route('/api/loaner/<loaner_id>/retrieve', methods=['POST'])
def retrieve_loaner(loaner_id):
    data = request.json or {}
    lid = loaner_id.upper()
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT l.*, h.name as home_name, s.name as staff_name
        FROM loaners l
        LEFT JOIN homes h ON l.home_id=h.id
        LEFT JOIN maintenance_staff s ON l.staff_id=s.id
        WHERE l.id=%s
    """, (lid,))
    loaner = cur.fetchone()
    if not loaner: cur.close(); conn.close(); return jsonify({'error':'Item not found'}), 404
    if loaner['status'] == 'in': cur.close(); conn.close(); return jsonify({'error':'Already at warehouse'}), 400
    notes = data.get('notes','')
    cur.execute("UPDATE loaners SET status='in', home_id=NULL, staff_id=NULL, deployed_at=NULL, notes=%s WHERE id=%s", (notes, lid))
    cur.execute("INSERT INTO activity(ts,action,bag_id,home_name,cleaner_name,notes,activity_type) VALUES(%s,%s,%s,%s,%s,%s,'loaner')",
        (now_central(), 'Retrieved', lid, loaner['home_name'], loaner['staff_name'], notes))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True,'item':loaner['name'],'home':loaner['home_name'],'staff':loaner['staff_name']})

# ── Loaner inventory ──────────────────────────────────────────────────────────
@app.route('/api/loaners')
def list_loaners():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT l.*, h.name as home_name, s.name as staff_name
        FROM loaners l
        LEFT JOIN homes h ON l.home_id=h.id
        LEFT JOIN maintenance_staff s ON l.staff_id=s.id
        ORDER BY l.category, l.id
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

# ── Maintenance staff ─────────────────────────────────────────────────────────
@app.route('/api/maintenance_staff')
def list_staff():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT s.*, COUNT(l.id) as items_out
        FROM maintenance_staff s
        LEFT JOIN loaners l ON s.id=l.staff_id AND l.status='out'
        WHERE s.active=1 GROUP BY s.id ORDER BY s.name
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/maintenance_staff', methods=['POST'])
def add_staff():
    name = request.json.get('name','').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO maintenance_staff(name) VALUES(%s) RETURNING id", (name,))
    new_id = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
    return jsonify({'id':new_id,'name':name})

@app.route('/api/maintenance_staff/<int:sid>', methods=['DELETE'])
def delete_staff(sid):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM loaners WHERE staff_id=%s AND status='out'", (sid,))
    if cur.fetchone()[0] > 0: cur.close(); conn.close(); return jsonify({'error':'Staff has items deployed'}), 400
    cur.execute("UPDATE maintenance_staff SET active=0 WHERE id=%s", (sid,)); conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

# ── Inventory & Activity ──────────────────────────────────────────────────────
@app.route('/api/inventory')
def inventory():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT b.id, b.status, b.checked_out, b.notes,
               h.name as home_name, h.code as home_code, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        ORDER BY h.code, b.id
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/activity')
def activity():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/stats')
def stats():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bags"); total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM bags WHERE status='out'"); out = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM homes"); homes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cleaners WHERE active=1"); cleaners = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM loaners WHERE status='out'"); loaners_out = cur.fetchone()[0]
    cur.close(); conn.close()
    return jsonify({'total':total,'out':out,'in':total-out,'homes':homes,'cleaners':cleaners,'loaners_out':loaners_out})

# ── Homes ─────────────────────────────────────────────────────────────────────
@app.route('/api/homes')
def list_homes():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT h.*, COUNT(b.id) as bag_count,
               SUM(CASE WHEN b.status='out' THEN 1 ELSE 0 END) as out_count
        FROM homes h LEFT JOIN bags b ON h.id=b.home_id
        GROUP BY h.id ORDER BY h.code
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/homes', methods=['POST'])
def add_home():
    data = request.json
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO homes(name,code) VALUES(%s,%s) RETURNING id", (data['name'].strip(), data['code'].strip().upper()))
        new_id = cur.fetchone()[0]; conn.commit()
        return jsonify({'id':new_id,'name':data['name'],'code':data['code']})
    except: conn.rollback(); return jsonify({'error':'Home or code already exists'}), 400
    finally: cur.close(); conn.close()

@app.route('/api/homes/<int:hid>', methods=['DELETE'])
def delete_home(hid):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bags WHERE home_id=%s", (hid,))
    if cur.fetchone()[0] > 0: cur.close(); conn.close(); return jsonify({'error':'Remove all bags first'}), 400
    cur.execute("DELETE FROM homes WHERE id=%s", (hid,)); conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

@app.route('/api/bags', methods=['POST'])
def add_bag():
    data = request.json
    bag_id = data['bag_id'].strip().upper()
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO bags(id,home_id,status) VALUES(%s,%s,'in')", (bag_id, data['home_id']))
        conn.commit(); return jsonify({'success':True,'id':bag_id})
    except: conn.rollback(); return jsonify({'error':'Bag ID already exists'}), 400
    finally: cur.close(); conn.close()

@app.route('/api/bags/<bag_id>', methods=['DELETE'])
def delete_bag(bag_id):
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM bags WHERE id=%s", (bag_id,))
    bag = cur.fetchone()
    if not bag: cur.close(); conn.close(); return jsonify({'error':'Not found'}), 404
    if bag['status'] == 'out': cur.close(); conn.close(); return jsonify({'error':'Cannot delete a checked-out bag'}), 400
    cur.execute("DELETE FROM bags WHERE id=%s", (bag_id,)); conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

# ── Cleaners ──────────────────────────────────────────────────────────────────
@app.route('/api/cleaners')
def list_cleaners():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.*, COUNT(b.id) as bags_out
        FROM cleaners c LEFT JOIN bags b ON c.id=b.cleaner_id AND b.status='out'
        WHERE c.active=1 GROUP BY c.id ORDER BY c.name
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/cleaners', methods=['POST'])
def add_cleaner():
    name = request.json.get('name','').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO cleaners(name) VALUES(%s) RETURNING id", (name,))
    new_id = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
    return jsonify({'id':new_id,'name':name})

@app.route('/api/cleaners/<int:cid>', methods=['DELETE'])
def delete_cleaner(cid):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bags WHERE cleaner_id=%s AND status='out'", (cid,))
    if cur.fetchone()[0] > 0: cur.close(); conn.close(); return jsonify({'error':'Cleaner has bags checked out'}), 400
    cur.execute("UPDATE cleaners SET active=0 WHERE id=%s", (cid,)); conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route('/api/settings/pins', methods=['POST'])
def save_pins():
    data = request.json
    conn = get_db(); cur = conn.cursor()
    if data.get('warehouse_pin'): cur.execute("UPDATE settings SET value=%s WHERE key='warehouse_pin'", (data['warehouse_pin'],))
    if data.get('admin_pin'): cur.execute("UPDATE settings SET value=%s WHERE key='admin_pin'", (data['admin_pin'],))
    if data.get('maintenance_pin'): cur.execute("UPDATE settings SET value=%s WHERE key='maintenance_pin'", (data['maintenance_pin'],))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'success':True})

@app.route('/')
def index(): return send_from_directory('public', 'index.html')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 3000))
    print(f"LinenTrack + LoanerTrack running on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
