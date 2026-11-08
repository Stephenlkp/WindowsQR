"""Copy file paths onto the native OS clipboard when the platform supports it."""
import os
import subprocess
import sys


def host_label():
    if os.name == 'nt':
        return 'Windows'
    if sys.platform == 'darwin':
        return 'Mac'
    return 'computer'


def paste_shortcut():
    if os.name == 'nt':
        return 'Ctrl+V'
    if sys.platform == 'darwin':
        return 'Cmd+V'
    return 'Paste'


def supports_file_clipboard():
    return os.name == 'nt' or sys.platform == 'darwin'


def copy_files(paths):
    paths = [os.path.abspath(path) for path in paths]
    if not paths:
        raise ValueError('No files to copy')
    if os.name == 'nt':
        from windows_clipboard import copy_files as windows_copy_files
        windows_copy_files(paths)
        return
    if sys.platform == 'darwin':
        _copy_files_mac(paths)
        return
    raise OSError('File clipboard transfer is not supported on this platform')


def _copy_files_mac(paths):
    script = '''
use framework "AppKit"
use framework "Foundation"
on run argv
    set pb to current application's NSPasteboard's generalPasteboard()
    pb's clearContents()
    set objects to current application's NSMutableArray's array()
    repeat with p in argv
        set posixPath to p as text
        set theURL to current application's NSURL's fileURLWithPath:posixPath
        objects's addObject:theURL
        set img to current application's NSImage's alloc()'s initWithContentsOfFile:posixPath
        if img is not missing value then
            objects's addObject:img
        end if
    end repeat
    if (pb's writeObjects:objects) is false then
        error "Could not write files to the Mac clipboard"
    end if
end run
'''
    result = subprocess.run(
        ['osascript', '-', *paths],
        input=script,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise OSError(detail or 'Mac clipboard transfer failed')
