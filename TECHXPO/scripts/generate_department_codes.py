#!/usr/bin/env python
"""Generate stable short codes for each department in departments_index.json and rewrite file with structure:
{
  "BV_CODE": [
     {"code": "<CODE>", "name": "Department Name"}, ...
  ],
  ...
}
Rules:
- Base code: uppercase alnum derived from name (remove accents) using first letters of significant words.
- Significant words: keep words with length>=2 and not in stop list (VI stop typical: KHOA, TRUNG, TAM, BENH, BENHVIEN, PHONG, PHUC, HOI, CHUC, NANG, TAI, MUI, HONG) except if all filtered then fall back to all words.
- Deduplicate within same hospital: if collision append numeric suffix _2, _3...
- Deterministic: same input gives same output ordering preserved.
Backup original file as departments_index.original.json once (if not exists).

Usage:
  python scripts/generate_department_codes.py --path Booking_Data/departments_index.json
"""
from __future__ import annotations
import argparse, json, unicodedata, re, os, sys
from typing import List, Dict, Any

STOP_WORDS = {"khoa","trung","tam","benh","benhvien","phong","phuc","hoi","chuc","nang","tai","mui","hong","so","noi","ngoai","tong","hop","da","lieu","mat","tiet","niem","nam","chan","thuong","chanthuong","cap","cuu","capcuu"}
WORD_RE = re.compile(r"[A-Za-zÀ-ỹ0-9]+", re.UNICODE)


def strip_accents(s: str) -> str:
    s_norm = unicodedata.normalize('NFD', s)
    return ''.join(ch for ch in s_norm if unicodedata.category(ch) != 'Mn')


def make_base_code(name: str) -> str:
    raw = strip_accents(name).upper()
    words = [w for w in WORD_RE.findall(raw)]
    if not words:
        return 'DEPT'
    # filter stop words
    sig = [w for w in words if w.lower() not in STOP_WORDS and len(w) >= 2]
    if not sig:
        sig = words
    # take first letters; ensure at least 3 chars by extending
    letters = ''.join(w[0] for w in sig)[:6]
    if len(letters) < 3:
        letters = (letters + ''.join(sig))[:3]
    return letters or 'DEPT'


def generate_codes_for_hospital(deps: List[str]) -> List[Dict[str,str]]:
    result = []
    used = {}
    for name in deps:
        base = make_base_code(name)
        code = base
        i = 2
        while code in used:
            code = f"{base}{i}"
            i += 1
        used[code] = name
        result.append({"code": code, "name": name})
    return result


def transform(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for hosp, deps in data.items():
        if isinstance(deps, list):
            out[hosp] = generate_codes_for_hospital([str(d) for d in deps])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', required=True, help='Path to departments_index.json')
    ap.add_argument('--inplace', action='store_true', help='Rewrite file in-place (default true)')
    args = ap.parse_args()
    path = args.path
    if not os.path.isfile(path):
        print('File not found:', path)
        return 1
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    new_data = transform(data)
    backup = os.path.join(os.path.dirname(path), 'departments_index.original.json')
    if not os.path.exists(backup):
        with open(backup, 'w', encoding='utf-8') as bf:
            json.dump(data, bf, ensure_ascii=False, indent=2)
            print('Backup written:', backup)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
        print('Updated file with codes:', path)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
