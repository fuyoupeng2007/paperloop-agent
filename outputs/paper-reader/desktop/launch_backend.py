"""Start the local worker independently of the desktop window."""
import os
from pathlib import Path
import sys
import threading

FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent
if FROZEN:
    os.environ['PAPERLOOP_DISTRIBUTED'] = '1'
default_data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PaperLoop' / 'data' if FROZEN else ROOT / 'data'
DATA = Path(os.environ.get('PAPERLOOP_DATA', str(default_data))).resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ['PAPERLOOP_DATA'] = str(DATA)
DATA.mkdir(parents=True, exist_ok=True)
# Do not attach the server's output to the window: closing it must not break jobs.
log = open(DATA / 'desktop-backend.log', 'a', encoding='utf-8', buffering=1)
sys.stdout = log
sys.stderr = log

def watch_parent(server, parent_pid):
    """Installed windows own their worker so upgrades can replace its binaries."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, parent_pid)
    if handle:
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(handle)
    server.should_exit = True

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import uvicorn
    server = uvicorn.Server(uvicorn.Config('backend.app:app', host='127.0.0.1',
        port=int(os.environ.get('PAPERLOOP_PORT', '8766' if FROZEN else '8765')),
        workers=1, access_log=False, timeout_graceful_shutdown=5))
    parent_pid = os.environ.get('PAPERLOOP_PARENT_PID')
    if parent_pid and os.name == 'nt':
        threading.Thread(target=watch_parent, args=(server, int(parent_pid)), daemon=True).start()
    server.run()
