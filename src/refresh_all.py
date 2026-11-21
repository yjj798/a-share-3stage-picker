# -*- coding: utf-8 -*-
"""
refresh_all.py — 跨平台刷新入口（替代Windows run_simple.bat）
串起：①热度 ②概念榜快照 ③评分重算。供 serve_cloud.py 调用，也可手动跑。
安全节流：2小时内不重复跑（文件时间戳）。
"""
import sys, os, time, subprocess, datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

STAMP = os.path.join(paths.OUT, '_last_refresh.txt')
REFRESH_SEC = int(os.environ.get('SP_REFRESH_SEC', '7200'))  # 默认2小时


def should_refresh():
    if not os.path.isfile(STAMP):
        return True
    return (time.time() - os.path.getmtime(STAMP)) >= REFRESH_SEC


def mark_refreshed():
    os.makedirs(paths.OUT, exist_ok=True)
    with open(STAMP, 'w') as f:
        f.write(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


def run():
    py = sys.executable
    steps = [
        ('热度补全', os.path.join(HERE, 'heat_backfill.py')),      # 新入池票补90天历史
        ('热度更新', os.path.join(HERE, 'heat_quick_update.py')),   # 全池当日排名
        ('概念补全', os.path.join(HERE, 'concept_backfill.py')),    # 新入池票补F10概念
        ('概念榜', os.path.join(HERE, 'ths_concept_snap.py')),      # 官方概念热度快照
        ('评分', os.path.join(HERE, 'simple_pool.py')),            # 技术+热度+概念 → 页面
    ]
    for name, script in steps:
        if not os.path.isfile(script):
            print(f'[refresh] 跳过{name}: 无脚本')
            continue
        print(f'[refresh] 跑{name}...', flush=True)
        try:
            r = subprocess.run([py, script], cwd=HERE, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=600,
                               env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
            tail = (r.stdout or '').strip().splitlines()[-2:]
            print(f'  rc={r.returncode} ' + ' | '.join(tail), flush=True)
            if r.returncode != 0 and r.stderr:
                print('  err: ' + r.stderr.strip()[-200:], flush=True)
        except Exception as e:
            print(f'  {name}失败: {e}', flush=True)


def main():
    force = '--force' in sys.argv
    if not force and not should_refresh():
        print(f'[refresh] 跳过（{REFRESH_SEC//3600}小时内已刷新）')
        return
    run()
    mark_refreshed()
    print(f'[refresh] 完成 {datetime.datetime.now():%H:%M:%S}')


if __name__ == '__main__':
    main()
