import os
import re
import shutil
import socket
import subprocess
import threading
import tempfile
import uuid
from pathlib import Path
from windows_clipboard import copy_files
from queue import Queue
from flask import Flask, render_template, request, send_file, send_from_directory, jsonify, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename
import qrcode
from io import BytesIO
from PIL import Image, ImageTk
import pyperclip
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

UPLOAD_QUEUE = Queue()
TUNNEL_EVENTS = Queue()
OUTGOING_FILES = {}
OUTGOING_FILES_LOCK = threading.Lock()
TUNNEL_URL_PATTERN = re.compile(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com')

app = Flask(__name__, static_folder='static', template_folder='templates')

# Default transfer folder (Downloads)
DEFAULT_FOLDER = os.path.join(os.path.expanduser('~'), 'Downloads')
transfer_folder = DEFAULT_FOLDER


def get_local_ip():
    configured_host = os.environ.get('QR_HOST')
    if configured_host:
        return configured_host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


@app.route('/')
def index():
    ip = get_local_ip()
    port = app.config.get('PORT', 0)
    url = f'http://{ip}:{port}/'
    return render_template('index.html', server_url=url, transfer_folder=transfer_folder)


@app.route('/upload', methods=['POST'])
def upload():
    global transfer_folder
    if 'file' not in request.files:
        return 'No file', 400
    f = request.files['file']
    if f.filename == '':
        return 'Empty filename', 400
    filename = secure_filename(f.filename)
    os.makedirs(transfer_folder, exist_ok=True)
    dest = os.path.join(transfer_folder, filename)
    f.save(dest)
    UPLOAD_QUEUE.put({'filename': filename, 'path': dest})
    return jsonify({'status': 'ok', 'filename': filename})


@app.route('/download/<path:fname>')
def download(fname):
    return send_from_directory(transfer_folder, fname, as_attachment=True)


@app.route('/api/transfer_folder')
def api_transfer_folder():
    return jsonify({'transfer_folder': transfer_folder})


@app.route('/api/files')
def api_files():
    files = []
    if os.path.isdir(transfer_folder):
        for entry in Path(transfer_folder).iterdir():
            if entry.is_file():
                files.append({
                    'name': entry.name,
                    'size': entry.stat().st_size,
                    'download_url': url_for('download', fname=entry.name),
                })
    files.sort(key=lambda item: item['name'].casefold())
    return jsonify({'files': files})


@app.route('/api/iphone-files')
def api_iphone_files():
    with OUTGOING_FILES_LOCK:
        files = list(OUTGOING_FILES.items())
    files = [
        {
            'id': file_id,
            'name': path.name,
            'size': path.stat().st_size,
            'download_url': url_for('iphone_download', file_id=file_id, _external=True),
        }
        for file_id, path in files
        if path.is_file()
    ]
    files.sort(key=lambda item: item['name'].casefold())
    return jsonify({'files': files})


@app.route('/iphone-download/<file_id>')
def iphone_download(file_id):
    with OUTGOING_FILES_LOCK:
        path = OUTGOING_FILES.get(file_id)
    if path is None or not path.is_file():
        return jsonify({'error': 'File is not available for iPhone download'}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route('/clipboard', methods=['GET', 'POST'])
def clipboard():
    if request.method == 'GET':
        try:
            return jsonify({'text': pyperclip.paste()})
        except pyperclip.PyperclipException as exc:
            return jsonify({'error': f'Windows clipboard unavailable: {exc}'}), 500

    data = request.get_json(silent=True) or {}
    text = data.get('text')
    if not isinstance(text, str):
        return jsonify({'error': 'Text is required'}), 400
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:
        return jsonify({'error': f'Windows clipboard unavailable: {exc}'}), 500
    UPLOAD_QUEUE.put({'filename': 'Clipboard text', 'path': ''})
    return jsonify({'status': 'ok'})


@app.route('/clipboard/files', methods=['POST'])
def clipboard_files():
    files = request.files.getlist('file')
    if not files or any(not item.filename for item in files):
        return jsonify({'error': 'Paste an image or file first'}), 400
    # File clipboard entries need backing files for subsequent Ctrl+V operations.
    # Retain these in the OS temp folder, independently of the Downloads folder.
    paths = []
    try:
        cache = Path(tempfile.mkdtemp(prefix='QR-Airdrop-clipboard-'))
        for index, item in enumerate(files):
            folder = cache / str(index)
            folder.mkdir()
            path = folder / (secure_filename(item.filename) or 'clipboard-file')
            item.save(path)
            paths.append(str(path))
        copy_files(paths)
    except (OSError, ValueError) as exc:
        return jsonify({'error': f'Clipboard transfer failed: {exc}'}), 500
    names = [Path(path).name for path in paths]
    UPLOAD_QUEUE.put({'filename': 'Clipboard: ' + ', '.join(names), 'path': 'Windows Clipboard'})
    return jsonify({'status': 'ok', 'filenames': names})


def run_flask(port=0):
    app.config['PORT'] = port
    app.run(host='0.0.0.0', port=port, threaded=True)


def read_tunnel_output(process):
    last_output = ''
    if process.stdout is not None:
        for line in process.stdout:
            last_output = line.strip()
            match = TUNNEL_URL_PATTERN.search(line)
            if match:
                TUNNEL_EVENTS.put({
                    'process': process,
                    'type': 'ready',
                    'url': match.group(0),
                })
        process.stdout.close()
    return_code = process.wait()
    TUNNEL_EVENTS.put({
        'process': process,
        'type': 'exited',
        'returncode': return_code,
        'output': last_output,
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


class QRApp:
    def __init__(self, port=5000):
        self.port = port
        self.root = tk.Tk()
        self.root.title('QR AirDrop')
        self.url_var = tk.StringVar()
        self.status_list = []
        self.outgoing_file_ids = []
        self.tunnel_url = None
        self.tunnel_process = None
        self.tunnel_server = None
        self.tunnel_thread = None

        self.frame = ttk.Frame(self.root, padding=12)
        self.frame.grid()

        self.qr_label = ttk.Label(self.frame)
        self.qr_label.grid(row=0, column=0, columnspan=2)

        self.url_label = ttk.Label(self.frame, textvariable=self.url_var)
        self.url_label.grid(row=3, column=0, columnspan=2, pady=(6,0))

        self.status_box = tk.Listbox(self.frame, width=60, height=10)
        self.status_box.grid(row=1, column=0, columnspan=2, pady=(8, 0))

        ttk.Label(self.frame, text='Files selected for iPhone').grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.outgoing_box = tk.Listbox(self.frame, width=60, height=4)
        self.outgoing_box.grid(row=5, column=0, columnspan=2, pady=(4, 0))

        self.choose_files_btn = ttk.Button(
            self.frame, text='Choose Files for iPhone', command=self.choose_outgoing_files)
        self.choose_files_btn.grid(row=6, column=0, sticky='ew', pady=8)
        self.remove_file_btn = ttk.Button(
            self.frame, text='Remove Selected', command=self.remove_outgoing_files)
        self.remove_file_btn.grid(row=6, column=1, sticky='ew', pady=8)

        self.change_btn = ttk.Button(self.frame, text='Change Folder', command=self.change_folder)
        self.change_btn.grid(row=2, column=0, sticky='ew', pady=8)

        self.open_btn = ttk.Button(self.frame, text='Open Folder', command=self.open_folder)
        self.open_btn.grid(row=2, column=1, sticky='ew', pady=8)

        self.start_tunnel_btn = ttk.Button(
            self.frame, text='Start Secure Cellular Tunnel', command=self.start_tunnel)
        self.start_tunnel_btn.grid(row=7, column=0, sticky='ew', pady=8)
        self.stop_tunnel_btn = ttk.Button(
            self.frame, text='Stop Tunnel', command=self.stop_tunnel, state='disabled')
        self.stop_tunnel_btn.grid(row=7, column=1, sticky='ew', pady=8)

        self.update_qr()
        self.poll_queue()
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def update_qr(self):
        if self.tunnel_url:
            url = self.tunnel_url
        else:
            ip = get_local_ip()
            url = f'http://{ip}:{self.port}/'
        self.url_var.set(url)
        qr = qrcode.make(url)
        qr = qr.resize((300, 300), Image.NEAREST)
        self.qr_img = ImageTk.PhotoImage(qr)
        self.qr_label.config(image=self.qr_img)

    def start_tunnel(self):
        if self.tunnel_process is not None:
            return
        if not messagebox.askyesno(
                'Start secure cellular tunnel',
                'This uses Cloudflare to relay traffic from the internet to this app. '
                'Only continue if your company allows it. Access will require a one-time '
                'PIN sent to the email address you allow.',
                parent=self.root):
            return

        cloudflared = shutil.which('cloudflared')
        if cloudflared is None:
            messagebox.showerror(
                'cloudflared not found',
                'Install cloudflared from https://developers.cloudflare.com/tunnel/downloads/, '
                'then restart WindowsQR.',
                parent=self.root)
            return

        allowed_mail = simpledialog.askstring(
            'Allow iPhone access',
            'Enter the email address allowed to access the tunnel. '
            'Multiple addresses may be comma-separated.',
            parent=self.root)
        if allowed_mail is None:
            return
        allowed_emails = [email.strip() for email in allowed_mail.split(',')]
        if any(not email or '@' not in email or any(char.isspace() for char in email)
               for email in allowed_emails):
            messagebox.showerror(
                'Invalid email address',
                'Enter one or more email addresses separated by commas.',
                parent=self.root)
            return

        try:
            tunnel_server = make_server(
                '127.0.0.1',
                0,
                ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=1),
                threaded=True)
        except OSError as exc:
            messagebox.showerror('Could not start tunnel service', str(exc), parent=self.root)
            return

        self.tunnel_server = tunnel_server
        self.tunnel_thread = threading.Thread(target=tunnel_server.serve_forever, daemon=True)
        self.tunnel_thread.start()
        command = [
            cloudflared,
            'tunnel',
            '--url',
            f'http://127.0.0.1:{tunnel_server.server_port}',
            '--allowed-mail',
            ','.join(allowed_emails),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1)
        except OSError as exc:
            self._stop_tunnel_server()
            messagebox.showerror('Could not start cloudflared', str(exc), parent=self.root)
            return

        self.tunnel_process = process
        self.start_tunnel_btn.config(state='disabled')
        self.stop_tunnel_btn.config(state='normal')
        self.add_status('Starting Cloudflare Quick Tunnel; waiting for its URL.')
        threading.Thread(target=read_tunnel_output, args=(process,), daemon=True).start()

    def _stop_tunnel_server(self):
        if self.tunnel_server is not None:
            self.tunnel_server.shutdown()
            self.tunnel_server.server_close()
            self.tunnel_server = None
            self.tunnel_thread = None

    def stop_tunnel(self):
        process = self.tunnel_process
        self.tunnel_process = None
        self.tunnel_url = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self._stop_tunnel_server()
        self.start_tunnel_btn.config(state='normal')
        self.stop_tunnel_btn.config(state='disabled')
        self.update_qr()
        self.add_status('Cloudflare tunnel stopped; local network QR restored.')

    def close(self):
        self.stop_tunnel()
        self.root.destroy()

    def change_folder(self):
        global transfer_folder
        new = filedialog.askdirectory(initialdir=transfer_folder, parent=self.root)
        if new:
            transfer_folder = new
            self.add_status(f'Transfer folder changed: {transfer_folder}')

    def choose_outgoing_files(self):
        paths = filedialog.askopenfilenames(
            title='Choose files to make available to iPhone',
            parent=self.root)
        for selected_path in paths:
            path = Path(selected_path)
            if not path.is_file():
                continue
            file_id = uuid.uuid4().hex
            with OUTGOING_FILES_LOCK:
                OUTGOING_FILES[file_id] = path
            self.outgoing_file_ids.append(file_id)
            self.outgoing_box.insert(tk.END, path.name)
        if paths:
            self.add_status(f'Made {len(paths)} file(s) available to iPhone')

    def remove_outgoing_files(self):
        selected = self.outgoing_box.curselection()
        for index in reversed(selected):
            file_id = self.outgoing_file_ids.pop(index)
            with OUTGOING_FILES_LOCK:
                OUTGOING_FILES.pop(file_id, None)
            self.outgoing_box.delete(index)

    def open_folder(self):
        try:
            os.startfile(transfer_folder)
        except Exception:
            # macOS / Linux
            import subprocess
            opener = 'open' if os.name == 'posix' else 'xdg-open'
            subprocess.Popen([opener, transfer_folder])

    def add_status(self, text):
        self.status_box.insert(0, text)

    def poll_queue(self):
        while not UPLOAD_QUEUE.empty():
            ev = UPLOAD_QUEUE.get()
            self.add_status(f"Received: {ev['filename']} -> {ev['path']}")
        while not TUNNEL_EVENTS.empty():
            event = TUNNEL_EVENTS.get()
            if event['process'] is not self.tunnel_process:
                continue
            if event['type'] == 'ready':
                self.tunnel_url = event['url'] + '/'
                self.update_qr()
                self.add_status('Secure cellular tunnel ready; scan the updated QR code.')
            else:
                detail = event['output'] or f"cloudflared exited with code {event['returncode']}"
                self.stop_tunnel()
                if event['returncode'] != 0:
                    messagebox.showerror('Cloudflare tunnel stopped', detail, parent=self.root)
        self.root.after(500, self.poll_queue)

    def run(self):
        self.root.mainloop()


def get_available_port(preferred=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('0.0.0.0', preferred))
        return s.getsockname()[1]
    except OSError:
        pass
    finally:
        s.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback:
        fallback.bind(('0.0.0.0', 0))
        return fallback.getsockname()[1]


def start(port=0):
    # Port 0 asks the operating system for an available ephemeral port.
    chosen_port = get_available_port(port)
    print(f'Using port {chosen_port} for server')
    print(f'Open this address from the iPhone: http://{get_local_ip()}:{chosen_port}/')
    flask_thread = threading.Thread(target=run_flask, kwargs={'port': chosen_port}, daemon=True)
    flask_thread.start()
    app = QRApp(port=chosen_port)
    app.run()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '0'))
    start(port=port)
