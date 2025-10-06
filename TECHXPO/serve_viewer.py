#!/usr/bin/env python3
"""
Simple HTTP server để phục vụ db_viewer.html
Chạy: python serve_viewer.py
Sau đó mở: http://localhost:8080/db_viewer.html
"""

import http.server
import socketserver
import os
import webbrowser
from threading import Timer

PORT = 8080

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Add CORS headers to allow loading kiosk.db
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

def open_browser():
    webbrowser.open(f'http://localhost:{PORT}/db_viewer.html')

if __name__ == "__main__":
    if not os.path.exists("kiosk.db"):
        print("❌ File kiosk.db không tồn tại!")
        print("💡 Hãy chạy agent ít nhất 1 lần để tạo database.")
        exit(1)
    
    print(f"🚀 Starting HTTP server on port {PORT}...")
    print(f"📂 Serving files from: {os.getcwd()}")
    print(f"🌐 URL: http://localhost:{PORT}/db_viewer.html")
    print("⚠️  Chỉ dùng cho development - không dùng trong production!")
    
    # Auto-open browser after 1 second
    Timer(1.0, open_browser).start()
    
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Shutting down server...")
            httpd.shutdown()
