import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from pathlib import Path
from native_clipboard import copy_files, host_label, paste_shortcut, supports_file_clipboard
from queue import Empty, Queue
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
_LOCAL_IP_CACHE = {'value': None, 'checked_at': 0.0}
_LOCAL_IP_LOCK = threading.Lock()
_LOCAL_IP_TTL_SEC = 30.0
STATUS_LIST_LIMIT = 50

app = Flask(__name__, static_folder='static', template_folder='templates')

# Default transfer folder (Downloads)
DEFAULT_FOLDER = os.path.join(os.path.expanduser('~'), 'Downloads')
transfer_folder = DEFAULT_FOLDER
HOST_LABEL = host_label()
PASTE_SHORTCUT = paste_shortcut()


def get_local_ip(force=False):
    configured_host = os.environ.get('QR_HOST')
    if configured_host:
        return configured_host
    now = time.monotonic()
    with _LOCAL_IP_LOCK:
        cached = _LOCAL_IP_CACHE['value']
        if (
            not force
            and cached
            and (now - _LOCAL_IP_CACHE['checked_at']) < _LOCAL_IP_TTL_SEC
        ):
            return cached
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
    except Exception:
        ip = cached or '127.0.0.1'
    with _LOCAL_IP_LOCK:
        _LOCAL_IP_CACHE['value'] = ip
        _LOCAL_IP_CACHE['checked_at'] = now
    return ip


def render_qr_png(url, size=300):
    qr = qrcode.make(url)
    qr = qr.resize((size, size), Image.NEAREST)
    buffer = BytesIO()
    qr.save(buffer, format='PNG')
    return buffer.getvalue()


def macos_transform_to_foreground():
    """Make this process a normal foreground Mac app so Tk receives mouse clicks.

    Apps launched from Cursor/IDE terminals often stay background-only: the window
    draws, but buttons never see clicks until the process is transformed/activated.
    """
    if sys.platform != 'darwin':
        return
    try:
        from ctypes import Structure, byref, c_uint32, CDLL

        class ProcessSerialNumber(Structure):
            _fields_ = [('highLongOfPSN', c_uint32), ('lowLongOfPSN', c_uint32)]

        app_services = CDLL(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        psn = ProcessSerialNumber()
        if app_services.GetCurrentProcess(byref(psn)) != 0:
            return
        # kProcessTransformToForegroundApplication = 1
        app_services.TransformProcessType(byref(psn), 1)
        app_services.SetFrontProcess(byref(psn))
    except Exception:
        pass


def release_tk_grabs(root):
    """Clear a stuck modal grab that can swallow all clicks on macOS."""
    try:
        current = root.grab_current()
        if current is not None:
            current.grab_release()
    except tk.TclError:
        pass
    try:
        root.grab_release()
    except tk.TclError:
        pass


@app.route('/')
def index():
    ip = get_local_ip()
    port = app.config.get('PORT', 0)
    url = f'http://{ip}:{port}/'
    response = app.make_response(render_template(
        'index.html',
        server_url=url,
        transfer_folder=transfer_folder,
        host_label=HOST_LABEL,
        paste_shortcut=PASTE_SHORTCUT,
        supports_file_clipboard=supports_file_clipboard(),
    ))
    # Prevent phones from keeping a stale upload script after tunnel/auth fixes.
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


@app.route('/upload', methods=['POST'])
def upload():
    global transfer_folder
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    filename = secure_filename(f.filename) or 'upload.bin'
    try:
        os.makedirs(transfer_folder, exist_ok=True)
        dest = os.path.join(transfer_folder, filename)
        f.save(dest)
    except OSError as exc:
        return jsonify({'error': f'Could not save file: {exc}'}), 500
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
@app.route('/api/phone-files')
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
@app.route('/phone-download/<file_id>')
def iphone_download(file_id):
    with OUTGOING_FILES_LOCK:
        path = OUTGOING_FILES.get(file_id)
    if path is None or not path.is_file():
        return jsonify({'error': 'File is not available for phone download'}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route('/clipboard', methods=['GET', 'POST'])
def clipboard():
    if request.method == 'GET':
        try:
            return jsonify({'text': pyperclip.paste()})
        except pyperclip.PyperclipException as exc:
            return jsonify({'error': f'Clipboard unavailable: {exc}'}), 500

    data = request.get_json(silent=True) or {}
    text = data.get('text')
    if not isinstance(text, str):
        return jsonify({'error': 'Text is required'}), 400
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:
        return jsonify({'error': f'Clipboard unavailable: {exc}'}), 500
    UPLOAD_QUEUE.put({'filename': 'Clipboard text', 'path': ''})
    return jsonify({'status': 'ok'})


def _save_clipboard_files_to_transfer_folder(paths):
    os.makedirs(transfer_folder, exist_ok=True)
    saved = []
    for source in paths:
        source_path = Path(source)
        dest = Path(transfer_folder) / source_path.name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            dest = Path(transfer_folder) / f'{stem}-{uuid.uuid4().hex[:8]}{suffix}'
        shutil.copy2(source_path, dest)
        saved.append(str(dest))
    return saved


@app.route('/clipboard/files', methods=['POST'])
def clipboard_files():
    files = request.files.getlist('file')
    if not files or any(not item.filename for item in files):
        return jsonify({'error': 'Paste an image or file first'}), 400
    # File clipboard entries need backing files for subsequent paste operations.
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
        destination = 'clipboard'
        try:
            copy_files(paths)
            location = f'{HOST_LABEL} Clipboard'
        except OSError:
            saved = _save_clipboard_files_to_transfer_folder(paths)
            destination = 'folder'
            location = transfer_folder
            paths = saved
    except (OSError, ValueError) as exc:
        return jsonify({'error': f'Clipboard transfer failed: {exc}'}), 500
    names = [Path(path).name for path in paths]
    UPLOAD_QUEUE.put({'filename': 'Clipboard: ' + ', '.join(names), 'path': location})
    return jsonify({
        'status': 'ok',
        'filenames': names,
        'destination': destination,
        'location': location,
    })


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
    # Plain tk.Button colors — hover tracks the mouse; avoid ttk's sticky Aqua focus ring.
    _BTN_BG = '#e8e8e8'
    _BTN_HOVER = '#7eb6ff'
    _BTN_ACTIVE = '#4d94f0'
    _BTN_DISABLED = '#f2f2f2'
    _BTN_FG = '#1a1a1a'
    _BTN_DISABLED_FG = '#9a9a9a'

    def __init__(self, port=5000):
        self.port = port
        macos_transform_to_foreground()
        self.root = tk.Tk()
        self.root.title('QR AirDrop')
        self.url_var = tk.StringVar()
        self.hover_var = tk.StringVar(value='Hover: —')
        self.status_list = []
        self.outgoing_file_ids = []
        self.tunnel_url = None
        self.tunnel_process = None
        self.tunnel_server = None
        self.tunnel_thread = None
        self._qr_request_id = 0
        self._tunnel_starting = False
        self._hover_buttons = []

        self.frame = ttk.Frame(self.root, padding=12)
        self.frame.grid()
        self.frame.columnconfigure(0, weight=1)
        self.frame.columnconfigure(1, weight=1)

        self.qr_label = ttk.Label(self.frame)
        self.qr_label.grid(row=0, column=0, columnspan=2)

        self.url_label = ttk.Label(self.frame, textvariable=self.url_var)
        self.url_label.grid(row=3, column=0, columnspan=2, pady=(6, 0))

        self.status_box = tk.Listbox(self.frame, width=60, height=10, takefocus=0)
        self.status_box.grid(row=1, column=0, columnspan=2, pady=(8, 0))

        ttk.Label(self.frame, text='Files selected for phone').grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.outgoing_box = tk.Listbox(self.frame, width=60, height=4, takefocus=0)
        self.outgoing_box.grid(row=5, column=0, columnspan=2, pady=(4, 0))

        self.change_btn = self._make_button(
            'Change Folder', self.change_folder, row=2, column=0)
        self.open_btn = self._make_button(
            'Open Folder', self.open_folder, row=2, column=1)
        self.choose_files_btn = self._make_button(
            'Choose Files for Phone', self.choose_outgoing_files, row=6, column=0)
        self.remove_file_btn = self._make_button(
            'Remove Selected', self.remove_outgoing_files, row=6, column=1)
        self.start_tunnel_btn = self._make_button(
            'Start Secure Cellular Tunnel', self.start_tunnel, row=7, column=0)
        self.stop_tunnel_btn = self._make_button(
            'Stop Tunnel', self.stop_tunnel, row=7, column=1, state='disabled')

        self.hover_label = ttk.Label(self.frame, textvariable=self.hover_var)
        self.hover_label.grid(row=8, column=0, columnspan=2, sticky='w', pady=(4, 0))

        self.update_qr()
        self.poll_queue()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<FocusIn>', self._on_focus_in, add='+')
        self.root.bind('<Map>', self._on_focus_in, add='+')
        # Escape recovers a stuck dialog grab without needing to restart.
        self.root.bind_all('<Escape>', lambda _event: release_tk_grabs(self.root))
        self.root.after(50, self._become_interactive)
        self.root.after(500, self._become_interactive)

    def _make_button(self, text, command, row, column, state='normal'):
        # Do not use command= with custom Button-1 binds on macOS Aqua — the class
        # binding often stops firing. We invoke explicitly on ButtonRelease-1.
        button = tk.Button(
            self.frame,
            text=text,
            takefocus=0,
            highlightthickness=0,
            bd=1,
            relief='raised',
            padx=10,
            pady=8,
            bg=self._BTN_BG,
            fg=self._BTN_FG,
            activebackground=self._BTN_ACTIVE,
            activeforeground=self._BTN_FG,
            disabledforeground=self._BTN_DISABLED_FG,
            cursor='hand2',
            state=state,
        )
        button.grid(row=row, column=column, sticky='ew', padx=4, pady=8)
        button._idle_bg = self._BTN_BG
        button._click_command = command
        button.bind('<Enter>', self._on_button_enter)
        button.bind('<Leave>', self._on_button_leave)
        button.bind('<ButtonPress-1>', self._on_button_press)
        button.bind('<ButtonRelease-1>', self._on_button_release)
        self._hover_buttons.append(button)
        return button

    def _on_button_enter(self, event):
        button = event.widget
        if str(button.cget('state')) == 'disabled':
            self.hover_var.set(f'Hover: {button.cget("text")} (disabled)')
            return
        button.configure(bg=self._BTN_HOVER, relief='solid')
        self.hover_var.set(f'Hover: {button.cget("text")}')

    def _on_button_leave(self, event):
        button = event.widget
        button._pressing = False
        if str(button.cget('state')) == 'disabled':
            button.configure(bg=self._BTN_DISABLED, relief='raised')
        else:
            button.configure(bg=getattr(button, '_idle_bg', self._BTN_BG), relief='raised')
        self.hover_var.set('Hover: —')

    def _on_button_press(self, event):
        button = event.widget
        if str(button.cget('state')) == 'disabled':
            return
        button._pressing = True
        button.configure(bg=self._BTN_ACTIVE)
        self.hover_var.set(f'Pressed: {button.cget("text")}')

    def _on_button_release(self, event):
        button = event.widget
        if str(button.cget('state')) == 'disabled':
            return
        # Only fire if press started on this button (ignore drag-release from elsewhere).
        if not getattr(button, '_pressing', False):
            return
        button._pressing = False
        label = button.cget('text')
        command = getattr(button, '_click_command', None)
        button.configure(bg=self._BTN_HOVER, relief='solid')
        self.hover_var.set(f'Click: {label}')
        if command is None:
            return
        # Run after the release event finishes so dialogs/Finder aren't nested in the bind.
        self.root.after_idle(lambda: self._run_button_command(label, command))

    def _run_button_command(self, label, command):
        self.hover_var.set(f'Running: {label}')
        try:
            command()
        except Exception as exc:
            self.hover_var.set(f'Error: {label}: {exc}')
            self.add_status(f'Button error ({label}): {exc}')
            messagebox.showerror('QR AirDrop', f'{label} failed:\n{exc}', parent=self.root)
            self._clear_dialog_focus()
            return
        self.hover_var.set(f'Done: {label}')

    def _set_button_state(self, button, state):
        button.configure(state=state)
        button._pressing = False
        if state == 'disabled':
            button.configure(bg=self._BTN_DISABLED, relief='raised')
        else:
            button.configure(bg=getattr(button, '_idle_bg', self._BTN_BG), relief='raised')

    def _clear_dialog_focus(self):
        release_tk_grabs(self.root)
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _on_focus_in(self, _event=None):
        release_tk_grabs(self.root)

    def _become_interactive(self):
        """Bring the window forward and ensure clicks reach Tk on macOS."""
        macos_transform_to_foreground()
        release_tk_grabs(self.root)
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.after(200, lambda: self.root.attributes('-topmost', False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def current_url(self):
        if self.tunnel_url:
            return self.tunnel_url
        return f'http://{get_local_ip()}:{self.port}/'

    def update_qr(self):
        """Update the URL label immediately; build the QR image off the UI thread."""
        url = self.current_url()
        self.url_var.set(url)
        self._qr_request_id += 1
        request_id = self._qr_request_id

        def worker():
            try:
                png_bytes = render_qr_png(url)
            except Exception:
                return
            self.root.after(0, lambda: self._apply_qr_image(request_id, png_bytes))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_qr_image(self, request_id, png_bytes):
        if request_id != self._qr_request_id:
            return
        try:
            image = Image.open(BytesIO(png_bytes))
            self.qr_img = ImageTk.PhotoImage(image)
            self.qr_label.config(image=self.qr_img)
        except Exception:
            pass

    def start_tunnel(self):
        if self.tunnel_process is not None or self._tunnel_starting:
            return
        if not messagebox.askyesno(
                'Start secure cellular tunnel',
                'This uses Cloudflare to relay traffic from the internet to this app. '
                'Only continue if your company allows it.',
                parent=self.root):
            self._clear_dialog_focus()
            return

        cloudflared = shutil.which('cloudflared')
        if cloudflared is None:
            messagebox.showerror(
                'cloudflared not found',
                'Install cloudflared (for example: brew install cloudflared), '
                'then restart QR AirDrop.',
                parent=self.root)
            self._clear_dialog_focus()
            return

        # Cloudflare's Quick Tunnel email PIN currently breaks POST uploads (401) after the
        # page loads — a known cloudflared bug. Default to URL-only access for reliable transfers.
        require_email = messagebox.askyesno(
            'Email PIN protection',
            'Require an email one-time PIN before phones can open the tunnel?\n\n'
            'Choose "No" for reliable file uploads (recommended).\n'
            'Choose "Yes" only if you need the PIN gate; Cloudflare currently returns '
            '401 Unauthorized on uploads after login.',
            parent=self.root)
        self._clear_dialog_focus()

        allowed_emails = []
        if require_email:
            allowed_mail = simpledialog.askstring(
                'Allow phone access',
                'Enter the email address allowed to access the tunnel. '
                'Multiple addresses may be comma-separated.',
                parent=self.root)
            self._clear_dialog_focus()
            if allowed_mail is None:
                return
            allowed_emails = [email.strip() for email in allowed_mail.split(',')]
            if any(not email or '@' not in email or any(char.isspace() for char in email)
                   for email in allowed_emails):
                messagebox.showerror(
                    'Invalid email address',
                    'Enter one or more email addresses separated by commas.',
                    parent=self.root)
                self._clear_dialog_focus()
                return

        self._tunnel_starting = True
        self._set_button_state(self.start_tunnel_btn, 'disabled')
        self.add_status('Starting Cloudflare Quick Tunnel; waiting for its URL.')
        threading.Thread(
            target=self._start_tunnel_worker,
            args=(cloudflared, allowed_emails),
            daemon=True,
        ).start()

    def _start_tunnel_worker(self, cloudflared, allowed_emails):
        tunnel_server = None
        try:
            tunnel_server = make_server(
                '127.0.0.1',
                0,
                ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=1),
                threaded=True)
            tunnel_thread = threading.Thread(target=tunnel_server.serve_forever, daemon=True)
            tunnel_thread.start()
            # --no-chunked-encoding is required for WSGI (Flask/Werkzeug): without it,
            # uploads often reach 100% then fail because cloudflared mishandles the response.
            command = [
                cloudflared,
                'tunnel',
                '--url',
                f'http://127.0.0.1:{tunnel_server.server_port}',
                '--no-chunked-encoding',
            ]
            if allowed_emails:
                command.extend(['--allowed-mail', ','.join(allowed_emails)])
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1)
        except OSError as exc:
            if tunnel_server is not None:
                self._stop_tunnel_server(tunnel_server)
            self.root.after(0, lambda: self._start_tunnel_failed(str(exc)))
            return

        def mark_started():
            self._tunnel_starting = False
            self.tunnel_server = tunnel_server
            self.tunnel_thread = tunnel_thread
            self.tunnel_process = process
            self._set_button_state(self.stop_tunnel_btn, 'normal')
            threading.Thread(target=read_tunnel_output, args=(process,), daemon=True).start()

        self.root.after(0, mark_started)

    def _start_tunnel_failed(self, detail):
        self._tunnel_starting = False
        self._set_button_state(self.start_tunnel_btn, 'normal')
        self._set_button_state(self.stop_tunnel_btn, 'disabled')
        messagebox.showerror('Could not start tunnel', detail, parent=self.root)
        self._clear_dialog_focus()

    def _stop_tunnel_server(self, server=None):
        target = server if server is not None else self.tunnel_server
        if server is None:
            self.tunnel_server = None
            self.tunnel_thread = None
        if target is None:
            return
        try:
            target.shutdown()
            target.server_close()
        except OSError:
            pass

    def stop_tunnel(self):
        process = self.tunnel_process
        server = self.tunnel_server
        self.tunnel_process = None
        self.tunnel_url = None
        self.tunnel_server = None
        self.tunnel_thread = None
        self._tunnel_starting = False
        self._set_button_state(self.start_tunnel_btn, 'normal')
        self._set_button_state(self.stop_tunnel_btn, 'disabled')
        self.add_status('Cloudflare tunnel stopped; local network QR restored.')
        # Defer QR rebuild so the button click can finish painting on macOS.
        self.root.after(1, self.update_qr)

        def cleanup():
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._stop_tunnel_server(server)

        # Never block the Tk UI thread on process/server shutdown (freezes all buttons on macOS).
        threading.Thread(target=cleanup, daemon=True).start()

    def close(self):
        self.stop_tunnel()
        self.root.destroy()

    def change_folder(self):
        global transfer_folder
        new = filedialog.askdirectory(initialdir=transfer_folder, parent=self.root)
        self._clear_dialog_focus()
        if new:
            transfer_folder = new
            self.add_status(f'Transfer folder changed: {transfer_folder}')

    def choose_outgoing_files(self):
        paths = filedialog.askopenfilenames(
            title='Choose files to make available to phone',
            parent=self.root)
        self._clear_dialog_focus()
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
            self.add_status(f'Made {len(paths)} file(s) available to phone')

    def remove_outgoing_files(self):
        selected = self.outgoing_box.curselection()
        for index in reversed(selected):
            file_id = self.outgoing_file_ids.pop(index)
            with OUTGOING_FILES_LOCK:
                OUTGOING_FILES.pop(file_id, None)
            self.outgoing_box.delete(index)

    def open_folder(self):
        folder = transfer_folder
        os.makedirs(folder, exist_ok=True)
        self.add_status(f'Opening folder: {folder}')
        if os.name == 'nt':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])

    def add_status(self, text):
        self.status_box.insert(0, text)
        while self.status_box.size() > STATUS_LIST_LIMIT:
            self.status_box.delete(STATUS_LIST_LIMIT)

    def poll_queue(self):
        # Drain quickly without spinning on empty(); Empty avoids race with producers.
        while True:
            try:
                ev = UPLOAD_QUEUE.get_nowait()
            except Empty:
                break
            self.add_status(f"Received: {ev['filename']} -> {ev['path']}")
        while True:
            try:
                event = TUNNEL_EVENTS.get_nowait()
            except Empty:
                break
            if event['process'] is not self.tunnel_process:
                continue
            if event['type'] == 'ready':
                self.tunnel_url = event['url'] + '/'
                self.update_qr()
                self.add_status('Secure cellular tunnel ready; scan the updated QR code.')
            else:
                detail = event['output'] or f"cloudflared exited with code {event['returncode']}"
                code = event['returncode']
                self.stop_tunnel()
                if code != 0:
                    def show_tunnel_error(message=detail):
                        try:
                            self.root.lift()
                            self.root.attributes('-topmost', True)
                            self.root.attributes('-topmost', False)
                        except tk.TclError:
                            pass
                        messagebox.showerror(
                            'Cloudflare tunnel stopped', message, parent=self.root)
                    self.root.after(0, show_tunnel_error)
        self.root.after(250, self.poll_queue)

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
    macos_transform_to_foreground()
    chosen_port = get_available_port(port)
    print(f'Using port {chosen_port} for server')
    print(f'Open this address from your phone: http://{get_local_ip()}:{chosen_port}/')
    flask_thread = threading.Thread(target=run_flask, kwargs={'port': chosen_port}, daemon=True)
    flask_thread.start()
    ui = QRApp(port=chosen_port)
    ui.run()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '0'))
    start(port=port)
