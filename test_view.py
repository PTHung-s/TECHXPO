#!/usr/bin/env python3
"""
Test view database functions without interactive menu
"""

import sqlite3
import json
import os

DB_PATH = "kiosk.db"

def test_view_customers():
    print("\n" + "="*60)
    print("📋 DANH SÁCH KHÁCH HÀNG")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, c.phone, c.facts, c.last_summary,
               COUNT(v.visit_id) as visit_count
        FROM customers c 
        LEFT JOIN visits v ON v.customer_id = c.id 
        GROUP BY c.id 
        ORDER BY visit_count DESC, c.name
    """)
    
    customers = cursor.fetchall()
    print(f"{'ID':<12} {'Tên':<20} {'SĐT':<12} {'Số lượt':<8} {'Có Facts':<10} {'Có Summary':<12}")
    print("-" * 80)
    
    for cid, name, phone, facts, summary, count in customers:
        has_facts = "✅" if facts else "❌"
        has_summary = "✅" if summary else "❌"
        print(f"{cid:<12} {name[:18]:<20} {phone:<12} {count:<8} {has_facts:<10} {has_summary:<12}")
    
    conn.close()
    return customers

def test_view_visits():
    print("\n" + "="*60)
    print("📋 TẤT CẢ LƯỢT KHÁM")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT visit_id, customer_id, created_at, payload_json, summary, facts_extracted
        FROM visits 
        ORDER BY created_at DESC LIMIT 10
    """)
    
    visits = cursor.fetchall()
    
    for i, (vid, cid, created_at, payload_json, summary, facts) in enumerate(visits, 1):
        print(f"\n--- LƯỢT KHÁM {i} ---")
        print(f"🆔 Visit ID: {vid}")
        print(f"👤 Customer: {cid}")
        print(f"📅 Thời gian: {created_at}")
        
        if summary:
            print(f"📝 Tóm tắt: {summary}")
        else:
            print(f"📝 Tóm tắt: Chưa có")
        
        if facts:
            print(f"🔍 Facts: {facts}")
        else:
            print(f"🔍 Facts: Chưa có")
        
        try:
            payload = json.loads(payload_json)
            print(f"📱 Số điện thoại: {payload.get('phone', 'N/A')}")
            print(f"👤 Tên bệnh nhân: {payload.get('patient_name', 'N/A')}")
            print(f"⏰ Lịch hẹn: {payload.get('appointment_time', 'N/A')}")
            
            symptoms = payload.get('symptoms', [])
            if symptoms:
                print("🩺 Triệu chứng:")
                for s in symptoms:
                    name = s.get('name', 'N/A')
                    severity = s.get('severity', 'N/A')
                    duration = s.get('duration', 'N/A')
                    print(f"   - {name} (mức độ: {severity}, thời gian: {duration})")
            
            diagnoses = payload.get('tentative_diagnoses', [])
            if diagnoses:
                print(f"🔬 Chẩn đoán: {', '.join(diagnoses)}")
                
        except json.JSONDecodeError:
            print(f"📄 Raw data: {payload_json[:100]}...")
        
        print("-" * 40)
    
    conn.close()

if __name__ == "__main__":
    print("🚀 Testing Database Viewer Functions...")
    test_view_customers()
    test_view_visits()
    print("\n✅ Test completed!")
