import os
from flask import Flask, request, render_template, send_from_directory, redirect, url_for
from werkzeug.utils import secure_filename
import sqlite3
from datetime import datetime
from shutil import copy2
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

app = Flask(__name__)

# ========================= CONFIG =========================
UPLOAD_FOLDER = 'main_storage'                    # Main server folder
REPLICA_FOLDER = r'D:\bills replica'               # <<< CHANGE THIS TO YOUR FLASH DRIVE PATH 
                                                  # Example Windows: r'E:\bills_replica'
                                                  # Example Linux/Mac: '/media/yourname/USB/bills_replica'
CLOUD_FOLDER = 'cloud_storage'                    # SIMULATED cloud (local folder)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create folders if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CLOUD_FOLDER, exist_ok=True)

DB_PATH = 'bills.db'

# ========================= DATABASE =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS bills (
        filename TEXT PRIMARY KEY,
        upload_time TEXT,
        replicated INTEGER DEFAULT 0,    -- 1 = copied to flash drive
        cloud_uploaded INTEGER DEFAULT 0 -- 1 = copied to cloud
    )''')
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ========================= ROUTES =========================
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        if file.filename == '':
            return 'No selected file'
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            main_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(main_path)

            # === Immediate copy to replica (flash drive) ===
            replica_path = os.path.join(REPLICA_FOLDER, filename)
            replicated = 0
            try:
                copy2(main_path, replica_path)
                replicated = 1
                print(f"✓ Copied to flash drive: {filename}")
            except Exception as e:
                print(f"✗ Flash drive copy failed (not plugged in?): {e}")

            # === Save record in database ===
            upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT OR REPLACE INTO bills VALUES (?, ?, ?, 0)",
                         (filename, upload_time, replicated))
            conn.commit()
            conn.close()

            return redirect(url_for('list_files'))

    return render_template('upload.html')

@app.route('/files')
def list_files():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("SELECT filename, upload_time, replicated, cloud_uploaded FROM bills ORDER BY upload_time DESC")
    bills = cursor.fetchall()
    conn.close()
    return render_template('files.html', bills=bills)

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# ========================= CLOUD SYNC (every 5 minutes) =========================
def sync_to_cloud():
    print("Running cloud sync...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("SELECT filename FROM bills WHERE cloud_uploaded = 0")
    for (filename,) in cursor.fetchall():
        main_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(main_path):
            cloud_path = os.path.join(CLOUD_FOLDER, filename)
            
            # === THIS IS THE SIMULATED CLOUD UPLOAD ===
            # Right now it just copies to a local folder (cloud_storage/)
            try:
                copy2(main_path, cloud_path)
                conn.execute("UPDATE bills SET cloud_uploaded = 1 WHERE filename = ?", (filename,))
                print(f"✓ Simulated cloud upload: {filename}")
            except Exception as e:
                print(f"✗ Simulated cloud upload failed: {e}")
    conn.commit()
    conn.close()

# Start the scheduler (runs sync_to_cloud every 5 minutes)
scheduler = BackgroundScheduler()
scheduler.add_job(func=sync_to_cloud, trigger="interval", minutes=5)
scheduler.start()

# Clean shutdown
atexit.register(lambda: scheduler.shutdown())

# ========================= RUN THE APP =========================
if __name__ == '__main__':
    init_db()
    print("Server starting... Go to http://127.0.0.1:5000 in your browser")
    app.run(debug=True, host='0.0.0.0', port=5000)