#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Catalog builder: chuyển raw doctor list trong folder Data (mỗi file 1 bệnh viện) ->
1) departments_index.json : liệt kê danh sách khoa của từng bệnh viện
2) <hospital_code>.grouped.json : group bác sĩ theo khoa, kèm thông tin gốc

Cấu trúc input: mỗi file JSON là 1 list các object bác sĩ. Ví dụ fields phổ biến:
  name, specialty, role, profile_link, extra_info / info (tùy bệnh viện)

Usage:
  python catalog_builder.py --input AI-Doctor/TECHXPO/TECHXPO/Data --out AI-Doctor/TECHXPO/TECHXPO/catalog

Thiết kế:
- Chuẩn hoá tên khoa: strip, thay nhiều khoảng trắng bằng 1, unify dash variants, title-case một phần giữ nguyên chữ đặc biệt.
- Bỏ bác sĩ không có specialty rõ (None, '', 'N/A'), hoặc name rỗng.
- Loại bỏ các bản ghi noise (ví dụ name chứa 'Đội ngũ bác sĩ' và profile_link 'N/A').
- Dedup theo (normalized_name, normalized_specialty) giữ bản ghi đầu tiên.
- Giữ nguyên các field còn lại để LLM có thêm context nếu cần.

Output grouped file structure:
{
  "hospital_code": "BV_BINHDAN",
  "departments": {
     "Ngoại tổng quát": [ {doctor_obj}, ... ],
     "Ngoại tiết niệu": [...]
  },
  "stats": {"total_doctors": X, "unique_departments": Y}
}

departments_index.json structure:
{
  "BV_BINHDAN": ["Ngoại tổng quát", "Ngoại tiết niệu", ...],
  "BV_NAMSAIGON": [...]
}
"""
from __future__ import annotations
import argparse
import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Set

# ---------------- Normalization helpers ----------------
MULTI_SPACE_RE = re.compile(r"\s+")
DASH_VARIANTS = re.compile(r"[–—−]")  # en dash / em dash / minus

NOISE_NAME_PATTERNS = [
    "đội ngũ bác sĩ chất lượng",  # trong file NAMSAIGON
]

SKIP_SPECIALTY_VALUES = {None, "", "n/a", "na", "null"}

# Tokens giữ nguyên upper (acronyms) – phải ở dạng upper sau chuẩn hóa
ACRONYM_TOKENS = {"ICU", "CKI", "CKII", "PGS", "TS", "BS", "GS"}


def norm_space(s: str) -> str:
    return MULTI_SPACE_RE.sub(" ", s.strip())


def norm_specialty(raw: str | None) -> str | None:
    """Chuẩn hóa tên khoa nhất quán, tránh chữ HOÁN VỊ HOA THƯỜNG lộn xộn.

    Steps:
      1. Thay dash variants thành '-' rồi chuẩn hóa khoảng trắng.
      2. Lowercase toàn bộ để có nền tảng đồng nhất.
      3. Split theo khoảng trắng; với mỗi token:
         - Nếu là acronyms (ICU, CKI...) giữ uppercase.
         - Nếu dài > 1: capitalize (chữ đầu viết hoa, còn lại thường) – giữ nguyên dấu.
      4. Ghép lại rồi chuẩn hóa các cụm đặc biệt ("Tp.", v.v. nếu cần mở rộng sau).
      5. Sửa 1 số pattern gộp sai như ' - ' (bảo đảm có khoảng trắng quanh '-').
    """
    if raw is None:
        return None
    s = norm_space(DASH_VARIANTS.sub("-", raw))
    s = s.strip()
    if not s:
        return None
    low_base = s.lower()
    if low_base in SKIP_SPECIALTY_VALUES:
        return None
    # Chuẩn hoá khoảng trắng quanh '-'
    s = re.sub(r"\s*-\s*", " - ", s)
    # Lower để xử lý token uniformly
    tokens = s.lower().split()
    norm_tokens: List[str] = []
    for t in tokens:
        t_clean = t.strip()
        if not t_clean:
            continue
        up = t_clean.upper()
        if up in ACRONYM_TOKENS:
            norm_tokens.append(up)
        else:
            # Viết hoa chữ cái đầu, còn lại giữ nguyên thường (đã lower)
            norm_tokens.append(t_clean.capitalize())
    s2 = " ".join(norm_tokens)
    # Ghép lại các pattern đặc thù về chữ cái: ví dụ 'Icu' -> 'ICU'
    s2 = re.sub(r"\bIcu\b", "ICU", s2)
    # Loại bỏ double spaces phát sinh (phòng xa)
    s2 = norm_space(s2)
    return s2


def is_noise_name(name: str) -> bool:
    low = name.lower()
    return any(pat in low for pat in NOISE_NAME_PATTERNS)


def norm_name(name: str) -> str:
    return norm_space(name)

# ---------------- Core processing ----------------

def process_hospital_file(path: str) -> Tuple[str, Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    base = os.path.basename(path)
    hospital_code = os.path.splitext(base)[0]
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            raise RuntimeError(f"JSON lỗi ở {path}: {e}")
    if not isinstance(data, list):
        raise ValueError(f"File {path} không phải list JSON")

    dept_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen: Set[Tuple[str, str]] = set()
    total_raw = 0
    total_keep = 0
    for item in data:
        total_raw += 1
        if not isinstance(item, dict):
            continue
        name = norm_name(str(item.get("name", "")).strip())
        spec_raw = item.get("specialty")
        specialty = norm_specialty(spec_raw if isinstance(spec_raw, str) else None)
        if not name or not specialty:
            continue
        if is_noise_name(name):
            continue
        key = (name.lower(), specialty.lower())
        if key in seen:
            continue
        seen.add(key)
        total_keep += 1
        dept_map[specialty].append(item)

    stats = {
        "hospital_code": hospital_code,
        "input_records": total_raw,
        "kept_records": total_keep,
        "unique_departments": len(dept_map),
    }
    return hospital_code, dept_map, stats


def build_catalog(input_dir: str, out_dir: str) -> Dict[str, Any]:
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Input folder không tồn tại: {input_dir}")
    os.makedirs(out_dir, exist_ok=True)

    departments_index: Dict[str, List[str]] = {}
    summary: Dict[str, Any] = {"hospitals": []}

    for fname in sorted(os.listdir(input_dir)):
        if not fname.lower().endswith('.json'):
            continue
        path = os.path.join(input_dir, fname)
        hospital_code, dept_map, stats = process_hospital_file(path)
        # write grouped file
        grouped_obj = {
            "hospital_code": hospital_code,
            "departments": dept_map,  # mapping specialty -> list doctors
            "stats": stats,
        }
        grouped_path = os.path.join(out_dir, f"{hospital_code}.grouped.json")
        with open(grouped_path, 'w', encoding='utf-8') as gf:
            json.dump(grouped_obj, gf, ensure_ascii=False, indent=2)
        departments_index[hospital_code] = sorted(dept_map.keys())
        summary["hospitals"].append({"hospital_code": hospital_code, **stats})

    # write departments_index
    with open(os.path.join(out_dir, 'departments_index.json'), 'w', encoding='utf-8') as f:
        json.dump(departments_index, f, ensure_ascii=False, indent=2)

    # write summary
    with open(os.path.join(out_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return {"departments_index": departments_index, "summary": summary}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='Folder chứa raw hospital JSON (Data)')
    ap.add_argument('--out', required=True, help='Folder đầu ra')
    args = ap.parse_args()
    res = build_catalog(args.input, args.out)
    print("Đã build catalog. Hospitals:", len(res['departments_index']))


if __name__ == '__main__':
    main()
