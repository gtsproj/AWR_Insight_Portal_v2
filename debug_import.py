import sys
sys.path.insert(0, 'C:\\AWR_Insight_Portal_v2')
sys.path.insert(0, 'C:\\AWR_Insight_Portal_v2\\common')
try:
    import portal.app
    print('OK')
except Exception as e:
    import traceback
    traceback.print_exc()
