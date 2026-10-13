# 開発用のサーバー。ブラウザにキャッシュさせないので、ファイルを直したら再読み込みだけで反映される。
# プロジェクトのフォルダで実行して、http://localhost:8765/03_work/web/ を開く
#
#   python3 03_work/serve.py

import http.server
import os

PORT = 8765


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
print(f"http://localhost:{PORT}/03_work/web/")
http.server.ThreadingHTTPServer(("", PORT), NoCacheHandler).serve_forever()
