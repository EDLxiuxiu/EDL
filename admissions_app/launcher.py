"""本机启动辅助：确认存储可用，服务就绪后再打开浏览器。"""
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from .repository import MemoryRepository


def server_matches(url, storage):
    try:
        with urllib.request.urlopen(url + "/health", timeout=1) as response:
            data = json.load(response)
        return isinstance(data, dict) and data.get("application") == "admissionsim-course-design" and data.get("storage") == storage
    except (OSError, ValueError, urllib.error.URLError):
        return False


def open_when_ready(url, storage):
    for _ in range(40):
        if server_matches(url, storage):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print("自动打开浏览器未完成，请手动访问：", url, flush=True)


def open_existing(port, storage):
    url = f"http://127.0.0.1:{port}"
    if server_matches(url, storage):
        print("系统已经在运行：", url, flush=True)
        webbrowser.open(url)
        return True
    return False


def serve(repository, *, port=5000, open_browser=False):
    if not 1 <= port <= 65535:
        raise ValueError("网页端口须在 1 至 65535 之间")
    url = f"http://127.0.0.1:{port}"
    storage = "memory" if isinstance(repository, MemoryRepository) else "mysql"
    with socket.socket() as probe:
        probe.settimeout(0.5)
        occupied = probe.connect_ex(("127.0.0.1", port)) == 0
    if occupied:
        if server_matches(url, storage):
            print("系统已经在运行：", url, flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        raise SystemExit(f"端口 {port} 已被其他程序或旧版服务占用。请关闭旧窗口，或用 manage.py web --port 5001 启动。")
    try:
        repository.runs()  # 启动前确认账号、主库与表可访问；不写数据库。
    except Exception as exc:
        raise SystemExit("数据库连接检查失败。请确认 MySQL84 服务正在运行，本机应用密码和主库已准备好。\n"
                         f"错误类型：{type(exc).__name__}；{str(exc)[:220]}") from None
    from .web import create_app
    print("\n中考录取模拟系统\n存储模式：", repository.mode, flush=True)
    print("打开地址：", url, "\n运行期间请保留此窗口；停止系统请按 Ctrl+C。\n", flush=True)
    if open_browser:
        threading.Thread(target=open_when_ready, args=(url, storage), daemon=True).start()
    create_app(repository).run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
