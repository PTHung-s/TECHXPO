#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Utility: inject stable department codes into hospital JSON schedule files.

Goal:
  - Each hospital JSON (e.g. Booking_data/BV_ABC.json) currently has structure similar to:
        {
          "hospital_name": "...",
          "departments": {
              "Khám Bệnh": ["BS A", "BS B"],
              "Ngoại Tiết Niệu": [...]
          }
        }
  - We generate a deterministic code per department (e.g. KBENH, NGTN, etc.) and rewrite to:
        {
          "hospital_name": "...",
          "departments": {
              "KBENH": {
                  "name": "Khám Bệnh",
                  "doctors": ["BS A", "BS B"]
              },
              "NGTN": {"name": "Ngoại Tiết Niệu", "doctors": [...]}
          }
        }
  - Existing plain format (string list) converted into dict form.
  - If file already converted (detect value is dict with 'name' key) we skip / preserve existing code.
  - A companion mapping file departments_index.generated.json will be emitted summarizing all codes.

Deterministic code algorithm:
  1. Remove accents (Vietnamese), uppercase
  2. Keep alphanumerics only -> tokens
  3. If token list >= 2 take first char of each token concatenated then pad with next letters until length >= 3 (max 6)
  4. If single token: take first 6 letters
  5. Ensure uniqueness per hospital: if collision append incremental digit.

Usage:
  python scripts/add_department_codes.py --dir Booking_data

Idempotent: running again won't duplicate; preserves existing structure.
"""
from __future__ import annotations
import os, json, argparse, unicodedata, re, sys
from typing import Dict, List, Tuple

ACC_RE = re.compile(r"[^A-Z0-9]+")

def strip_accents(s: str) -> str:
    n = unicodedata.normalize('NFD', s or '')
    return ''.join(ch for ch in n if unicodedata.category(ch) != 'Mn')

def gen_code(name: str, used: set) -> str:
    base = strip_accents(name).upper()
    tokens = [t for t in re.split(r"[^A-Z0-9]+", base) if t]
    if not tokens:
        tokens = ["DEPT"]
    if len(tokens) == 1:
        code = tokens[0][:6]
    else:
        # first letters then extend if too short
        code = ''.join(t[0] for t in tokens)[:6]
        if len(code) < 3:
            code = (code + ''.join(tokens))[:6]
    if len(code) < 3:
        code = (code + 'DEPT')[:3]
    orig = code
    i = 1
    while code in used:
        suffix = str(i)
        code = (orig[: 6 - len(suffix)] + suffix)
        i += 1
    used.add(code)
    return code

def convert_file(path: str) -> Tuple[bool, Dict[str, Dict]]:
    """Return (changed, new_data)."""
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"[skip] {os.path.basename(path)} parse error: {e}")
            return False, {}
    if not isinstance(data, dict):
        return False, {}
    deps = data.get('departments')
    if not isinstance(deps, dict):
        return False, {}
    # detect already converted (values are dict with 'name')
    already = all(isinstance(v, dict) and 'name' in v for v in deps.values())
    if already:
        # still build mapping (reuse existing codes)
        mapping = {code: {'name': v.get('name'), 'doctors': v.get('doctors', []) if isinstance(v, dict) else []} for code, v in deps.items() if isinstance(v, dict)}
        return False, mapping
    used = set()
    new_dep: Dict[str, Dict] = {}
    for name, doctors in deps.items():
        if not isinstance(doctors, list):
            continue
        code = gen_code(name, used)
        new_dep[code] = {
            'name': name,
            'doctors': doctors,
        }
    data['departments'] = new_dep
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True, {c: {'name': v['name'], 'doctors': v['doctors']} for c, v in new_dep.items()}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='Booking_data', help='Folder containing hospital JSON files')
    ap.add_argument('--out-index', default='departments_index.generated.json', help='Output aggregated index file')
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        print(f"Directory not found: {root}")
        return 1

    aggregate: Dict[str, List[Dict[str, str]]] = {}
    changed_any = False
    for fname in os.listdir(root):
        if not fname.lower().endswith('.json'):
            continue
        if fname.lower().startswith('departments_index'):
            continue
        fpath = os.path.join(root, fname)
        hosp_code = os.path.splitext(fname)[0]
        changed, mapping = convert_file(fpath)
        if mapping:
            aggregate[hosp_code] = [ {'code': c, 'name': v.get('name')} for c, v in mapping.items() ]
        if changed:
            print(f"[updated] {fname}")
            changed_any = True
        else:
            print(f"[ok] {fname} (no structural change)")

    out_path = os.path.join(root, args.out_index)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2)
    print(f"Wrote aggregate index -> {out_path} ({len(aggregate)} hospitals)")
    if not changed_any:
        print("No files modified.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
