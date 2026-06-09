import sqlite3, json, os
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='public', static_url_path='')
DB_PATH = os.path.join(os.path.dirname(__file__), 'db', 'linentrack.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS homes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS cleaners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
            action TEXT NOT NULL,
            bag_id TEXT NOT NULL,
            home_name TEXT,
            cleaner_name TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()
    for key, val in [('warehouse_pin','1234'),('admin_pin','9999')]:
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, val))
    db.commit()

    # Seed real homes and cleaners if empty
    count = db.execute("SELECT COUNT(*) FROM homes").fetchone()[0]
    if count == 0:
        homes = [
            ("44 Thicket Circle", "HOME-01"),
            ("93 Needlerush Drive", "HOME-02"),
            ("295 Salt Box Lane", "HOME-03"),
            ("428 Red Cedar Way", "HOME-04"),
            ("65 Pond Cypress Circle", "HOME-05"),
            ("91 Bluejack Street", "HOME-06"),
            ("12 Viridian Park Drive", "HOME-07"),
            ("20 Tall Timber Court", "HOME-08"),
            ("49 Bluejack Street", "HOME-09"),
            ("369 Spartina Circle", "HOME-10"),
            ("349 Needlerush Drive", "HOME-11"),
            ("202 East Royal Fern Way", "HOME-12"),
            ("134 Royal Fern Way", "HOME-13"),
            ("75 East Summersweet Lane", "HOME-14"),
            ("25 Rain Lily Lane", "HOME-15"),
            ("21 Chanel Court", "HOME-16"),
            ("262 Garfield Street", "HOME-17"),
            ("379 East Royal Fern Way", "HOME-18"),
            ("51 Mistflower Lane", "HOME-19"),
            ("1352 Western Lake Drive", "HOME-20"),
            ("442 East Royal Fern Way", "HOME-21"),
            ("73 Holly Street", "HOME-22"),
            ("43 Sand Hill Circle", "HOME-23"),
            ("446 Western Lake Drive", "HOME-24"),
            ("1735 East Co Hwy 30A #203", "HOME-25"),
            ("410 Pine Needle Way", "HOME-26"),
            ("672 Western Lake Drive", "HOME-27"),
            ("25 Lake District Lane", "HOME-28"),
            ("9 Running Oak Circle", "HOME-29"),
            ("179 Pine Needle Way", "HOME-30"),
            ("422 Pine Needle Way", "HOME-31"),
            ("31 Bluejack Street", "HOME-32"),
            ("406 Red Cedar Way", "HOME-33"),
            ("70 Scrub Oak Circle", "HOME-34"),
            ("2743 E Co Hwy 30A, Unit 303", "HOME-35"),
            ("271 Red Cedar Way", "HOME-36"),
            ("19 Muhly Circle", "HOME-37"),
            ("97 East Summersweet Lane", "HOME-38"),
            ("728 Western Lake Drive", "HOME-39"),
            ("90 Flatwood Street", "HOME-40"),
            ("22 Flatwood Street", "HOME-41"),
            ("109 Dandelion Drive", "HOME-42"),
            ("255 Garfield Street", "HOME-43"),
            ("176 Red Cedar Way", "HOME-44"),
            ("184 East Royal Fern Way", "HOME-45"),
            ("70 Sunset Ridge Lane", "HOME-46"),
            ("2912 E. Co Hwy 30A", "HOME-47"),
            ("46 Pine Needle Way", "HOME-48"),
            ("124 Sunset Ridge Lane", "HOME-49"),
            ("263 Magnolia Street", "HOME-50"),
            ("73 Pond Cypress Circle", "HOME-51"),
            ("433 Pine Needle Way", "HOME-52"),
            ("254 Spartina Circle", "HOME-53"),
            ("157 Sunflower Street", "HOME-54"),
            ("100 Tumblehome Way", "HOME-55"),
            ("260 Needlerush Drive", "HOME-56"),
            ("80 Scrub Oak Circle", "HOME-57"),
            ("394 Western Lake Drive", "HOME-58"),
            ("37 Red Cedar Way", "HOME-59"),
            ("5 Pond Cypress Way", "HOME-60"),
            ("35 Suzanne Drive", "HOME-61"),
            ("2060 E Co Hwy 30A", "HOME-62"),
            ("194 Spartina Circle", "HOME-63"),
            ("142 Mystic Cobalt Street", "HOME-64"),
            ("209 Western Lake Drive", "HOME-65"),
            ("37 Compass Point II, Unit 106", "HOME-66"),
            ("138 East Royal Fern Way", "HOME-67"),
            ("1217 Western Lake Drive", "HOME-68"),
            ("72 Needlerush Drive", "HOME-69"),
            ("86 Sunset Ridge Lane", "HOME-70"),
            ("53 Muhly Circle", "HOME-71"),
            ("29 Royal Fern Way", "HOME-72"),
            ("99 Pond Cypress Way", "HOME-73"),
        ]
        for name, code in homes:
            db.execute("INSERT INTO homes(name,code) VALUES(?,?)", (name, code))
        db.commit()

        cleaners = [
            "A&D Cleaning",
            "Dream Clean",
            "Elizabeth Varo",
            "Gesiane Barbosa",
            "Jennifer Hawkins",
            "Juan Carlos Rocha",
            "Mario Cruz",
            "Miranda Edney",
            "Monserrat Guzman",
        ]
        for c in cleaners:
            db.execute("INSERT INTO cleaners(name) VALUES(?)", (c,))
        db.commit()
    db.close()

def row_to_dict(row):
    return dict(row) if row else None

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth', methods=['POST'])
def auth():
    pin = request.json.get('pin','')
    db = get_db()
    w_pin = db.execute("SELECT value FROM settings WHERE key='warehouse_pin'").fetchone()['value']
    a_pin = db.execute("SELECT value FROM settings WHERE key='admin_pin'").fetchone()['value']
    db.close()
    if pin == a_pin: return jsonify({'role':'admin'})
    if pin == w_pin: return jsonify({'role':'warehouse'})
    return jsonify({'error':'Wrong PIN'}), 401

# ── Bag lookup ────────────────────────────────────────────────────────────────
@app.route('/api/bag/<bag_id>')
def get_bag(bag_id):
    db = get_db()
    bag = db.execute("""
        SELECT b.*, h.name as home_name, h.code as home_code, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        WHERE b.id=?
    """, (bag_id.upper(),)).fetchone()
    db.close()
    if not bag: return jsonify({'error':'Bag not found'}), 404
    return jsonify(row_to_dict(bag))

@app.route('/api/bag/<bag_id>/checkout', methods=['POST'])
def checkout(bag_id):
    data = request.json
    bid = bag_id.upper()
    db = get_db()
    bag = db.execute("SELECT * FROM bags WHERE id=?", (bid,)).fetchone()
    if not bag: db.close(); return jsonify({'error':'Bag not found'}), 404
    if bag['status'] == 'out': db.close(); return jsonify({'error':'Already checked out'}), 400
    cleaner = db.execute("SELECT * FROM cleaners WHERE id=?", (data['cleaner_id'],)).fetchone()
    home = db.execute("SELECT * FROM homes WHERE id=?", (bag['home_id'],)).fetchone()
    db.execute("UPDATE bags SET status='out', cleaner_id=?, checked_out=? WHERE id=?",
        (data['cleaner_id'], datetime.now().isoformat(), bid))
    db.execute("INSERT INTO activity(action,bag_id,home_name,cleaner_name) VALUES(?,?,?,?)",
        ('Sent out', bid, home['name'], cleaner['name']))
    db.commit(); db.close()
    return jsonify({'success':True,'bag_id':bid,'home':home['name'],'cleaner':cleaner['name']})

@app.route('/api/bag/<bag_id>/checkin', methods=['POST'])
def checkin(bag_id):
    data = request.json or {}
    bid = bag_id.upper()
    db = get_db()
    bag = db.execute("""
        SELECT b.*, h.name as home_name, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        WHERE b.id=?
    """, (bid,)).fetchone()
    if not bag: db.close(); return jsonify({'error':'Bag not found'}), 404
    if bag['status'] == 'in': db.close(); return jsonify({'error':'Already at warehouse'}), 400
    notes = data.get('notes','')
    db.execute("UPDATE bags SET status='in', cleaner_id=NULL, checked_out=NULL, notes=? WHERE id=?", (notes, bid))
    db.execute("INSERT INTO activity(action,bag_id,home_name,cleaner_name,notes) VALUES(?,?,?,?,?)",
        ('Returned', bid, bag['home_name'], bag['cleaner_name'], notes))
    db.commit(); db.close()
    return jsonify({'success':True,'bag_id':bid,'home':bag['home_name'],'cleaner':bag['cleaner_name']})

# ── Inventory & Activity ──────────────────────────────────────────────────────
@app.route('/api/inventory')
def inventory():
    db = get_db()
    rows = db.execute("""
        SELECT b.id, b.status, b.checked_out, b.notes,
               h.name as home_name, h.code as home_code, c.name as cleaner_name
        FROM bags b JOIN homes h ON b.home_id=h.id LEFT JOIN cleaners c ON b.cleaner_id=c.id
        ORDER BY h.code, b.id
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/activity')
def activity():
    db = get_db()
    rows = db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 200").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/stats')
def stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM bags").fetchone()[0]
    out = db.execute("SELECT COUNT(*) FROM bags WHERE status='out'").fetchone()[0]
    homes = db.execute("SELECT COUNT(*) FROM homes").fetchone()[0]
    cleaners = db.execute("SELECT COUNT(*) FROM cleaners WHERE active=1").fetchone()[0]
    db.close()
    return jsonify({'total':total,'out':out,'in':total-out,'homes':homes,'cleaners':cleaners})

# ── Homes ─────────────────────────────────────────────────────────────────────
@app.route('/api/homes')
def list_homes():
    db = get_db()
    rows = db.execute("""
        SELECT h.*, COUNT(b.id) as bag_count,
               SUM(CASE WHEN b.status='out' THEN 1 ELSE 0 END) as out_count
        FROM homes h LEFT JOIN bags b ON h.id=b.home_id
        GROUP BY h.id ORDER BY h.code
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/homes', methods=['POST'])
def add_home():
    data = request.json
    db = get_db()
    try:
        cur = db.execute("INSERT INTO homes(name,code) VALUES(?,?)", (data['name'].strip(), data['code'].strip().upper()))
        db.commit()
        return jsonify({'id':cur.lastrowid,'name':data['name'],'code':data['code']})
    except: return jsonify({'error':'Home or code already exists'}), 400
    finally: db.close()

@app.route('/api/homes/<int:hid>', methods=['DELETE'])
def delete_home(hid):
    db = get_db()
    bags = db.execute("SELECT COUNT(*) FROM bags WHERE home_id=?", (hid,)).fetchone()[0]
    if bags > 0: db.close(); return jsonify({'error':'Remove all bags from this home first'}), 400
    db.execute("DELETE FROM homes WHERE id=?", (hid,)); db.commit(); db.close()
    return jsonify({'success':True})

# ── Bags ──────────────────────────────────────────────────────────────────────
@app.route('/api/bags', methods=['POST'])
def add_bag():
    data = request.json
    bag_id = data['bag_id'].strip().upper()
    db = get_db()
    try:
        db.execute("INSERT INTO bags(id,home_id,status) VALUES(?,?,?)", (bag_id, data['home_id'], 'in'))
        db.commit()
        return jsonify({'success':True,'id':bag_id})
    except: return jsonify({'error':'Bag ID already exists'}), 400
    finally: db.close()

@app.route('/api/bags/<bag_id>', methods=['DELETE'])
def delete_bag(bag_id):
    db = get_db()
    bag = db.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not bag: db.close(); return jsonify({'error':'Not found'}), 404
    if bag['status'] == 'out': db.close(); return jsonify({'error':'Cannot delete a checked-out bag'}), 400
    db.execute("DELETE FROM bags WHERE id=?", (bag_id,)); db.commit(); db.close()
    return jsonify({'success':True})

# ── Cleaners ──────────────────────────────────────────────────────────────────
@app.route('/api/cleaners')
def list_cleaners():
    db = get_db()
    rows = db.execute("""
        SELECT c.*, COUNT(b.id) as bags_out
        FROM cleaners c LEFT JOIN bags b ON c.id=b.cleaner_id AND b.status='out'
        WHERE c.active=1 GROUP BY c.id ORDER BY c.name
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/cleaners', methods=['POST'])
def add_cleaner():
    name = request.json.get('name','').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    db = get_db()
    cur = db.execute("INSERT INTO cleaners(name) VALUES(?)", (name,))
    db.commit(); db.close()
    return jsonify({'id':cur.lastrowid,'name':name})

@app.route('/api/cleaners/<int:cid>', methods=['DELETE'])
def delete_cleaner(cid):
    db = get_db()
    out = db.execute("SELECT COUNT(*) FROM bags WHERE cleaner_id=? AND status='out'", (cid,)).fetchone()[0]
    if out > 0: db.close(); return jsonify({'error':'Cleaner has bags checked out'}), 400
    db.execute("UPDATE cleaners SET active=0 WHERE id=?", (cid,)); db.commit(); db.close()
    return jsonify({'success':True})

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route('/api/settings/pins', methods=['POST'])
def save_pins():
    data = request.json
    db = get_db()
    if data.get('warehouse_pin'): db.execute("UPDATE settings SET value=? WHERE key='warehouse_pin'", (data['warehouse_pin'],))
    if data.get('admin_pin'): db.execute("UPDATE settings SET value=? WHERE key='admin_pin'", (data['admin_pin'],))
    db.commit(); db.close()
    return jsonify({'success':True})

@app.route('/')
def index(): return send_from_directory('public', 'index.html')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 3000))
    print(f"LinenTrack running on http://localhost:{port}")
    print("Warehouse PIN: 1234  |  Admin PIN: 9999")
    app.run(host='0.0.0.0', port=port, debug=False)
