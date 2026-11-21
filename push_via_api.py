# -*- coding: utf-8 -*-
"""
push_via_api.py — 用 GitHub Contents API 推送文件（git push 网络不稳时的备用通道）
逐个文件 PUT，走 HTTPS API，比 git 协议稳。
"""
import os, sys, json, base64, urllib.request, urllib.error

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

REPO = 'yjj798/a-share-3stage-picker'
BRANCH = 'main'
ROOT = os.path.dirname(os.path.abspath(__file__))


def load_token():
    t = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if t:
        return t
    for p in [r'E:\AI\14-账号记忆\SECRETS.md']:
        if os.path.isfile(p):
            import re
            with open(p, encoding='utf-8', errors='ignore') as f:
                m = re.findall(r'ghp_[A-Za-z0-9]{36}', f.read())
                if m:
                    return m[0]
    return None


def api(method, path, token, data=None):
    url = f'https://api.github.com/repos/{REPO}/contents/{path}'
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('Authorization', f'token {token}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'stockpool-push')
    if body:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        msg = e.read().decode('utf-8', 'ignore')[:200]
        return {'_error': e.code, '_msg': msg}
    except Exception as e:
        return {'_error': 'net', '_msg': str(e)[:150]}


def get_sha(path, token):
    r = api('GET', f'{path}?ref={BRANCH}', token)
    return r.get('sha') if isinstance(r, dict) and 'sha' in r else None


def push_file(rel, token, msg):
    full = os.path.join(ROOT, rel)
    if not os.path.isfile(full):
        return f'跳过(不存在): {rel}'
    with open(full, 'rb') as f:
        content = base64.b64encode(f.read()).decode()
    payload = {'message': msg, 'content': content, 'branch': BRANCH}
    sha = get_sha(rel, token)
    if sha:
        payload['sha'] = sha
    r = api('PUT', rel, token, payload)
    if '_error' in r:
        return f'失败 {rel}: {r["_error"]} {r.get("_msg","")[:80]}'
    return f'OK {rel}'


def main():
    token = load_token()
    if not token:
        sys.exit('无 token')
    files = sys.argv[1:]
    if not files:
        sys.exit('用法: python push_via_api.py <文件> ...')
    msg = 'feat: 补齐配置模板/CHANGELOG/单元测试，修复三处缺陷'
    ok = fail = 0
    for f in files:
        r = push_file(f.replace('\\', '/'), token, msg)
        print(' ', r)
        if r.startswith('OK'):
            ok += 1
        elif r.startswith('失败'):
            fail += 1
    print(f'\n完成: 成功{ok} 失败{fail}')


if __name__ == '__main__':
    main()
