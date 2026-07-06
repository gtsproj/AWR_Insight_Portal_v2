import sys

# Check sa02_text.txt content
filepath = r'C:\AWR_Insight_Portal_v2\sar_reports\TCLFSLPRDDB1\sa02_text.txt'
lines = open(filepath, errors='replace').readlines()

print('TOTAL LINES:', len(lines))
print()
print('FIRST 5 LINES:')
for i, l in enumerate(lines[:5]):
    print(f'  [{i}] {repr(l[:120])}')

print()
print('CPU SECTION SEARCH:')
for i, l in enumerate(lines):
    if 'CPU' in l and ('%usr' in l or '%user' in l):
        print(f'  Line {i}: {repr(l[:120])}')
        if i+1 < len(lines):
            print(f'  Line {i+1}: {repr(lines[i+1][:80])}')
        break
else:
    print('  NOT FOUND - no line with CPU and %usr/%user')
    print()
    print('  Lines containing %usr:')
    for i, l in enumerate(lines):
        if '%usr' in l or '%user' in l:
            print(f'    Line {i}: {repr(l[:100])}')
            if i > 5:
                break

print()
print('PARSER VERSION CHECK:')
sys.path.insert(0, r'C:\AWR_Insight_Portal_v2\common')
sys.path.insert(0, r'C:\AWR_Insight_Portal_v2\modules\sar')
import sar_cpu_parser
print(f'  Loaded from: {sar_cpu_parser.__file__}')
src = open(sar_cpu_parser.__file__, errors='replace').read()
print(f'  Version v3: {"CPU parser" + " — first 20 lines" in src}')
print(f'  Has _strip_time_prefix: {"_strip_time_prefix" in src}')
