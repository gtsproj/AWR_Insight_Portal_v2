# Run from C:\AWR_Insight_Portal_v2\
# py fix_sar_master_hostname.py
# Fixes the regex bug causing UNKNOWN_HOST for 2-digit year SAR files

import re, os

filepath = os.path.join("modules", "sar", "sar_master_parser.py")
with open(filepath, encoding="utf-8") as f:
    content = f.read()

print("BEFORE - DATE_PATTERNS section:")
for line in content.split('\n'):
    if '_DATE_PATTERNS' in line or 'compile' in line:
        print(f"  {line}")

# Fix 1: replace broken 2-digit year pattern (double-escaped \\b)
content = content.replace(
    r're.compile(r"\\(([^)]+)\\)\\s+(\\d{2}/\\d{2}/\\d{2})\\\\b")',
    r're.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\b")'
)

# Fix 2: also ensure all three patterns are correct
old_patterns = '''_DATE_PATTERNS = [
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{2}/\\d{2}/\\d{4})"),
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{4}-\\d{2}-\\d{2})"),
    re.compile(r"\\(([^)]+)\\)\\s+(\\d{2}/\\d{2}/\\d{2})\\\\b"),
]'''

new_patterns = r"""_DATE_PATTERNS = [
    re.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{4})"),
    re.compile(r"\(([^)]+)\)\s+(\d{4}-\d{2}-\d{2})"),
    re.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\b"),
]"""

if old_patterns in content:
    content = content.replace(old_patterns, new_patterns)
    print("\nFixed _DATE_PATTERNS block")
else:
    # Try individual fix
    content = content.replace(
        r're.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\\b")',
        r're.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\b")'
    )
    print("\nApplied individual pattern fix")

# Verify patterns work
import re as _re
test_headers = [
    "Linux 5.4.17 (TCLFSLPRDDB1) \t12/01/23 \t_x86_64_",   # 2-digit year
    "Linux 5.4.17 (TCLFSLPRDDB1) \t12/02/2023 \t_x86_64_", # 4-digit year
    "Linux 5.4.17 (TCLFSLPRDDB1) \t2023-12-02 \t_x86_64_", # ISO date
]
patterns = [
    _re.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{4})"),
    _re.compile(r"\(([^)]+)\)\s+(\d{4}-\d{2}-\d{2})"),
    _re.compile(r"\(([^)]+)\)\s+(\d{2}/\d{2}/\d{2})\b"),
]
print("\nPattern validation:")
for h in test_headers:
    matched = False
    for pat in patterns:
        m = pat.search(h)
        if m:
            print(f"  OK: '{h[-20:].strip()}' -> host={m.group(1)} date={m.group(2)}")
            matched = True
            break
    if not matched:
        print(f"  FAIL: '{h[-20:].strip()}'")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\nFile saved: {os.path.abspath(filepath)}")
print("Now:")
print("  1. Run: py clear_cache.py")
print("  2. Restart portal")
print("  3. Upload sa02_text.txt with hostname TCLFSLPRDDB1")
