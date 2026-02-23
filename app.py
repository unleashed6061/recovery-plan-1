import os
from flask import Flask, request, render_template, send_from_directory, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from datetime import datetime
from shutil import copy2
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import subprocess

app = Flask(__name__)
app.secret_key = 'super-secret-key-change-this-123456789'  # ← CHANGE THIS in production!

# ========================= CONFIG =========================
UPLOAD_FOLDER    = 'main_server'          # Main storage (always local on VPS/machine)
LOCAL_REPLICA_FOLDER = 'replica_storage'  # Always-on local replica folder (another file in the system)
FLASH_REPLICA_FOLDER = '/media/usb/bills_replica'  # Optional flash drive path (Linux: /media/usb/; Windows: r'E:\bills_replica') - change based on OS
CLOUD_REMOTE     = 'gdrive'               # rclone remote name
CLOUD_PATH       = 'bills'                # folder inside Google Drive

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'txt'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOCAL_REPLICA_FOLDER, exist_ok=True)

DB_PATH = 'bills.db'

# ========================= DATABASE INIT =========================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS bills (
            filename TEXT PRIMARY KEY,
            upload_time TEXT,
            local_replicated INTEGER DEFAULT 0,
            flash_replicated INTEGER DEFAULT 0,
            cloud_uploaded INTEGER DEFAULT 0
        )''')
        conn.commit()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ========================= LOGIN REQUIRED DECORATOR =========================
def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# ========================= ROUTES =========================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return redirect(url_for('register'))

        hashed = generate_password_hash(password)

        try:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))
                conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists. Choose another.', 'danger')
        except Exception as e:
            flash(f'Registration error: {str(e)}', 'danger')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM users WHERE username = ?", (username,))
            result = c.fetchone()

        if result and check_password_hash(result[0], password):
            session['user'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('upload_file'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            flash('No file selected.', 'danger')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            main_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(main_path)

            # Copy to always-on local replica
            local_replica_success = 0
            try:
                copy2(main_path, os.path.join(LOCAL_REPLICA_FOLDER, filename))
                local_replica_success = 1
            except Exception as e:
                flash(f'Local replica copy failed: {str(e)}', 'warning')

            # Optional: Copy to flash replica if exists
            flash_replica_success = 0
            if os.path.exists(FLASH_REPLICA_FOLDER):
                try:
                    copy2(main_path, os.path.join(FLASH_REPLICA_FOLDER, filename))
                    flash_replica_success = 1
                except Exception as e:
                    flash(f'Flash replica copy failed (not plugged in?): {str(e)}', 'warning')
            else:
                print(f"Flash replica path not found: {FLASH_REPLICA_FOLDER} - skipping")

            # Save metadata
            upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bills (filename, upload_time, local_replicated, flash_replicated) VALUES (?, ?, ?, ?)",
                    (filename, upload_time, local_replica_success, flash_replica_success)
                )
                conn.commit()

            flash('File uploaded successfully!', 'success')
            return redirect(url_for('list_files'))
        else:
            flash('File type not allowed.', 'danger')

    # GET - show form + paths
    return render_template('upload.html',
                           main_path=os.path.abspath(UPLOAD_FOLDER),
                           local_replica_path=os.path.abspath(LOCAL_REPLICA_FOLDER),
                           flash_replica_path=FLASH_REPLICA_FOLDER if os.path.exists(FLASH_REPLICA_FOLDER) else 'Not detected',
                           cloud_path=f"{CLOUD_REMOTE}:{CLOUD_PATH}")

@app.route('/files')
@login_required
def list_files():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT filename, upload_time, local_replicated, flash_replicated, cloud_uploaded FROM bills ORDER BY upload_time DESC")
        bills = cursor.fetchall()

    return render_template('files.html', bills=bills,
                           main_path=os.path.abspath(UPLOAD_FOLDER),
                           local_replica_path=os.path.abspath(LOCAL_REPLICA_FOLDER),
                           flash_replica_path=FLASH_REPLICA_FOLDER if os.path.exists(FLASH_REPLICA_FOLDER) else 'Not detected',
                           cloud_path=f"{CLOUD_REMOTE}:{CLOUD_PATH}")

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    except FileNotFoundError:
        flash('File not found.', 'danger')
        return redirect(url_for('list_files'))

# ========================= CLOUD SYNC =========================
def sync_to_cloud():
    print("[Cloud Sync] Starting...")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT filename FROM bills WHERE cloud_uploaded = 0")
        for row in cursor.fetchall():
            filename = row[0]
            local_path = os.path.join(UPLOAD_FOLDER, filename)
            if not os.path.exists(local_path):
                continue

            remote_path = f"{CLOUD_REMOTE}:{CLOUD_PATH}/{filename}"

            try:
                result = subprocess.run(
                    ['rclone', 'copyto', local_path, remote_path],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    conn.execute("UPDATE bills SET cloud_uploaded = 1 WHERE filename = ?", (filename,))
                    print(f"[Cloud Sync] Success: {filename}")
                else:
                    print(f"[Cloud Sync] Failed: {filename}\n{result.stderr}")
            except Exception as e:
                print(f"[Cloud Sync] Exception for {filename}: {str(e)}")

    conn.commit()  # commit outside loop for efficiency

# Scheduler setup
scheduler = BackgroundScheduler()
scheduler.add_job(sync_to_cloud, 'interval', minutes=5)
scheduler.start()

atexit.register(lambda: scheduler.shutdown())

# ========================= START =========================
if __name__ == '__main__':
    init_db()
    print(" * Starting Bill Storage System...")
    print(" * Login:    http://127.0.0.1:5000/login")
    print(" * Register: http://127.0.0.1:5000/register")
    app.run(debug=True, host='0.0.0.0', port=5000)