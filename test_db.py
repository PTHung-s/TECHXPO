#!/usr/bin/env python3
"""
Quick test để xem có dữ liệu trong database không
"""

import sqlite3
import os

DB_PATH = "kiosk.db"

if not os.path.exists(DB_PATH):
    print(f"❌ File {DB_PATH} không tồn tại!")
    exit(1)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check customers
cursor.execute("SELECT COUNT(*) FROM customers")
customer_count = cursor.fetchone()[0]
print(f"👥 Số khách hàng: {customer_count}")

# Check visits  
cursor.execute("SELECT COUNT(*) FROM visits")
visit_count = cursor.fetchone()[0]
print(f"🏥 Số lượt khám: {visit_count}")

# Check schema
cursor.execute("PRAGMA table_info(customers)")
columns = [col[1] for col in cursor.fetchall()]
print(f"📋 Cột trong bảng customers: {columns}")

cursor.execute("PRAGMA table_info(visits)")
visit_columns = [col[1] for col in cursor.fetchall()]
print(f"📋 Cột trong bảng visits: {visit_columns}")

if customer_count > 0:
    print("\n📊 Sample customers:")
    cursor.execute("SELECT id, name, phone FROM customers LIMIT 3")
    for row in cursor.fetchall():
        print(f"  - {row}")

conn.close()
print("\n✅ Database test completed!")
