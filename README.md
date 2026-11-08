# QR AirDrop

Small Python app that runs a local web server and shows a QR code in a desktop window. Scan with a phone camera (iPhone or Android) to open the Web UI and upload files directly to the configured transfer folder (default: Downloads).

Runs on **Windows** (arm/x86) and **macOS**. Linux can run the server and file upload flow; native file-clipboard paste falls back to saving into the transfer folder.

Quick start

1. Create a Python environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. Run the app (on the machine that will receive files):

```bash
python app.py
```

3. Scan the QR with your phone camera — the page lets you choose a file and upload. Upload progress is shown in the browser and the file is saved to your `Downloads` folder by default. The desktop window shows recent transfers.

Packaging to a Windows `.exe`

Build on a Windows machine (recommended) using PyInstaller:

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "templates;templates" app.py
```

On macOS you can package similarly with PyInstaller (`:` as the data separator) if you need a `.app` bundle.

Notes

- The app selects an available port automatically. The selected port is shown in the QR code and window. Set the `PORT` environment variable to request a specific port; if it is unavailable, the app selects another free port.

Clipboard images and files

- Copy an image or file on the phone, then paste in the web page's **Send images or files** box and press **Send**. On Windows and Mac, supported items are placed on the OS clipboard (paste with Ctrl+V / Cmd+V). If native clipboard transfer is unavailable, files are saved to the transfer folder instead.
- Safari may not expose copied files from some iOS apps. The page includes a Shortcut fallback: **Get Clipboard**, **Scan QR or Barcode**, **Text** containing the scanned URL immediately followed by `clipboard/files`, then **Get Contents of URL** with POST, Form, a `file` field of type File, and the **Clipboard** variable as its value. A shared file can instead use **Shortcut Input**. Do not repeat individual requests for multiple files: each request replaces the clipboard.
- The HTTP page cannot automatically read the phone clipboard. It requires an explicit Paste action or the Shortcut. Clipboard file data is staged in `QR-Airdrop-clipboard-*` directories in the OS temporary folder, not Downloads. These files must remain available while the clipboard references them.
- Text clipboard transfer remains available in its own section and works via `pyperclip` on Windows and Mac.
- Use the desktop `Change Folder` button to set a different transfer folder.
- The web UI uses XHR upload progress.
- In Parallels Desktop, use **Bridged Network** so the VM receives an address on the same LAN as the phone. A `10.211.x.x` Shared Network address is usually only reachable from the Mac host. The app prints the address used in the QR code when it starts.
- If the host has multiple network adapters, set the reachable address explicitly before starting: `export QR_HOST=192.168.x.x` (PowerShell: `$env:QR_HOST = '192.168.x.x'`).

Remote access from cellular with Cloudflare Quick Tunnel

- Install `cloudflared` on the computer ([Cloudflare downloads](https://developers.cloudflare.com/tunnel/downloads/) or `brew install cloudflared` on Mac). It is not bundled with QR AirDrop.
- Click **Start Secure Cellular Tunnel**. Prefer **No** when asked about email PIN protection — Cloudflare’s Quick Tunnel email gate currently returns **401 Unauthorized** on file uploads after login (known `cloudflared` bug). The random `trycloudflare.com` URL is still hard to guess. If you choose **Yes**, enter allowed email(s), complete the PIN in the phone browser, and expect uploads may fail until Cloudflare fixes the session handling.
- Quick Tunnels provide a free, temporary HTTPS URL without a Cloudflare account or domain. The URL changes each time and stops working when the tunnel or app closes; Cloudflare does not guarantee uptime. Keep the app running while transferring.
- Use this only if your company permits Cloudflare Tunnel. Traffic is relayed through Cloudflare. The tunnel requires email authentication; the existing local-network address remains unauthenticated and should only be used on a trusted network.
- Cloudflare's email sign-in requires an interactive browser. Shortcut-based API requests may not authenticate through a Quick Tunnel; use the web page for cellular transfers.

Computer to phone REST API

To make files from anywhere on the computer available for transfer, click **Choose Files for Phone** in the desktop window and select them. Only files you choose this way are exposed to this transfer list. Use **Remove Selected** to stop offering a file. The selected files are not copied; their original paths must remain accessible until the phone downloads them.

Create or update a phone transfer Shortcut / script with these actions:

1. **Get Contents of URL**: `http://<HOST-IP>:<port>/api/phone-files` (older clients may use `/api/iphone-files`).
2. **Get Dictionary Value**: get `files` from the result.
3. **Choose from List**: use the `files` list to pick a file.
4. **Get Dictionary Value**: get `download_url` from the chosen file.
5. **Get Contents of URL**: use that download URL. The response is the file.
6. **Save File**: save the result to the chosen location.

The `GET /api/phone-files` response includes each selected file's `id`, `name`, `size` in bytes, and absolute `download_url`. The download URL is available only for files explicitly selected in the desktop app. The existing `GET /clipboard` endpoint still returns the current text clipboard as `{"text":"..."}`. `GET /api/files` and `GET /download/<filename>` continue to list and download files in the transfer folder.

These endpoints are available to devices that can reach the server on the local network. They do not provide authentication; use them only on a trusted network.
