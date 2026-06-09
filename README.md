# LinenTrack — Beach Rental Linen Bag Tracker

Track linen bags and loaner items across your beach rental properties.
Warehouse staff scans bags in/out. Managers see who has what, by home or by cleaner.

---

## What's included

- **Scan Bag** — scan or type a bag ID → instantly see which home it belongs to → assign a cleaner (checkout) or confirm return (checkin)
- **Who Has What** — see every cleaner and which bags they currently have
- **By Home** — see all bags for each property and where they are
- **Activity Log** — full history of every checkout and return
- **Admin: Cleaners** — add/remove cleaners
- **Admin: Settings** — manage homes, add bags, change PINs

---

## Default PINs

| Role | PIN |
|------|-----|
| Warehouse staff | `1234` |
| Admin/Manager | `9999` |

**Change these immediately** in Settings after first login.

---

## Setup

### Requirements
- Python 3.8 or newer
- pip

### Install
```bash
pip install flask
```

### Run
```bash
python3 server.py
```

Open your browser (or iPad) to: **http://YOUR-SERVER-IP:3000**

To run on a different port:
```bash
PORT=8080 python3 server.py
```

---

## Running on a server (always-on)

### Option A — simple VPS (DigitalOcean, Linode, etc.)
1. Upload the whole `linentrack/` folder to your server
2. Install flask: `pip install flask`
3. Run with: `python3 server.py`
4. To keep it running after logout, use `screen` or `tmux`:
   ```bash
   screen -S linentrack
   python3 server.py
   # Press Ctrl+A then D to detach
   ```

### Option B — systemd service (auto-starts on reboot)
Create `/etc/systemd/system/linentrack.service`:
```ini
[Unit]
Description=LinenTrack
After=network.target

[Service]
WorkingDirectory=/path/to/linentrack
ExecStart=python3 server.py
Restart=always
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl enable linentrack
sudo systemctl start linentrack
```

---

## Bag ID format

Recommended format: `HOME-XX-Y`
- `HOME-01-A`, `HOME-01-B`, `HOME-01-C` → bags for home #1
- `HOME-02-A`, `HOME-02-B` → bags for home #2

Print QR codes for each bag ID and stick them on durable weatherproof labels.
Any Bluetooth barcode/QR scanner (like the Tera HW0006, ~$40) pairs to an iPad
like a keyboard and types the bag ID directly into the scan field.

---

## Scanner setup (iPad + Bluetooth scanner)

1. Pair your Bluetooth scanner to the iPad like a Bluetooth keyboard
2. Open Safari → go to your server's IP address → bookmark it to Home Screen
3. Open the Home Screen app → enter warehouse PIN
4. Tap the scan zone to focus the input field
5. Scan a bag tag → the scanner types the ID → tap "Look up bag"

Many scanners can be set to auto-submit (add Enter after scan) — check your
scanner's programming guide to enable this.

---

## Data

All data is stored in `db/linentrack.db` — a single SQLite file.
Back this file up regularly (it's the only file that matters).

---

## File structure

```
linentrack/
├── server.py          ← Flask server + all API routes
├── db/
│   └── linentrack.db  ← SQLite database (auto-created)
└── public/
    └── index.html     ← Full web app (login + warehouse + admin views)
```
