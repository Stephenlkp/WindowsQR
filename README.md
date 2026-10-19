# QR AirDrop

Small Python app that runs a local web server and shows a QR code in a desktop window. Scan with an iPhone camera to open the Web UI and upload files directly to the configured transfer folder (default: Downloads).

Quick start

1. Create a Python environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Run the app (on the machine that will receive files):

```bash
python app.py
```

3. Scan the QR using an iPhone camera — the page lets you choose a file and upload. The upload progress is shown in Safari and the file is saved to your `Downloads` folder by default. The desktop window shows recent transfers.

Packaging to a Windows `.exe`

Build on a Windows machine (recommended) using PyInstaller:

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "templates;templates" app.py
```

Notes

- The app selects an available port automatically. The selected port is shown in the QR code and window. Set the `PORT` environment variable to request a specific port; if it is unavailable, the app selects another free port.

Clipboard images and files

- Copy an image or file on iOS, then use Paste in the web page's **Send images or files (iOS Clipboard) to Windows Clipboard** box and press **Send to Windows Clipboard**. Paste on Windows with Ctrl+V. Single supported images include a bitmap clipboard representation; all files include file-copy clipboard entries for File Explorer.
- Safari may not expose copied files from some iOS apps. The page includes a Shortcut fallback: **Get Clipboard**, **Scan QR or Barcode**, **Text** containing the scanned URL immediately followed by `clipboard/files`, then **Get Contents of URL** with POST, Form, a `file` field of type File, and the **Clipboard** variable as its value. A shared file can instead use **Shortcut Input**. Do not repeat individual requests for multiple files: each request replaces the Windows clipboard.
- The HTTP page cannot automatically read the iOS clipboard. It requires an explicit Paste action or the Shortcut. Clipboard file data is staged in `QR-Airdrop-clipboard-*` directories in the Windows temporary folder, not Downloads. These files must remain available while the clipboard references them; Windows temporary-file cleanup may eventually remove them.
- Text clipboard transfer remains available in its own section. Clipboard file transfers require Windows.
- Use the desktop `Change Folder` button to set a different transfer folder.
- The web UI uses XHR upload progress (Safari shows upload progress while sending the file).
- In Parallels Desktop, use **Bridged Network** so the VM receives an address on the same LAN as the iPhone. A `10.211.x.x` Shared Network address is usually only reachable from the Mac host. The app prints the address used in the QR code when it starts.
- If the VM has multiple network adapters, set the reachable VM address explicitly in PowerShell before starting: `$env:QR_HOST = '192.168.x.x'`. The iPhone page provides file selection and upload; folder changes remain desktop controls because the destination folder is on the VM.

Windows to iPhone REST API

To make files from anywhere on the PC available for transfer, click **Choose Files for iPhone** in the WindowsQR desktop window and select them. Only files you choose this way are exposed to this transfer list. Use **Remove Selected** to stop offering a file. The selected files are not copied; their original paths must remain accessible until the iPhone downloads them.

Create or update the **Windows to iPhone Transfer** Shortcut with these actions:

1. **Get Contents of URL**: `http://<PC-IP>:<port>/api/iphone-files` (use the URL and port shown in the QR AirDrop window).
2. **Get Dictionary Value**: get `files` from the result.
3. **Choose from List**: use the `files` list to pick a file.
4. **Get Dictionary Value**: get `download_url` from the chosen file.
5. **Get Contents of URL**: use that download URL. The response is the file.
6. **Save File**: save the result to the chosen location in Files.

The `GET /api/iphone-files` response includes each selected file's `id`, `name`, `size` in bytes, and absolute `download_url`. The download URL is available only for files explicitly selected in the Windows desktop app. The existing `GET /clipboard` endpoint still returns the current Windows text clipboard as `{"text":"..."}`; use **Get Contents of URL**, get the `text` dictionary value, then **Copy to Clipboard** or **Show Result**. `GET /api/files` and `GET /download/<filename>` continue to list and download files in the transfer folder.

These endpoints are available to devices that can reach the Windows server on the local network. They do not provide authentication; use them only on a trusted network.
