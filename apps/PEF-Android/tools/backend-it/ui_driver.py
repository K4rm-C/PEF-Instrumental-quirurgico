"""Minimal UI driver for the emulator: dump the view tree, find nodes by text/content-desc, tap, type, screenshot.
Usage examples:
  python ui.py texts                      -> list visible texts / content descriptions
  python ui.py tap "Sign In"              -> tap first node whose text or desc contains the string
  python ui.py type "abc"                 -> type text into the focused field
  python ui.py shot name                  -> screenshot to scratchpad/shots/name.png
  python ui.py key BACK
  python ui.py swipe up
Secrets: `typeseed <email>` types that seed account's password without printing it."""
import os, re, subprocess, sys, time, xml.etree.ElementTree as ET

ADB = os.path.join(os.environ['LOCALAPPDATA'], 'Android', 'Sdk', 'platform-tools', 'adb.exe')
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.environ.get('PEF_SHOTS_DIR', os.path.join(HERE, 'shots')); os.makedirs(SHOTS, exist_ok=True)
SEED = os.environ.get('PEF_SEED_FILE', os.path.join('..', 'PEF-Instrumental-quirurgico', 'data', 'seeds', '003_seed_demo.sql'))

def adb(*a, capture=True):
    return subprocess.run([ADB, *a], capture_output=capture, text=False).stdout

def nodes():
    for _ in range(3):
        adb('shell', 'uiautomator', 'dump', '/sdcard/ui.xml')
        raw = adb('exec-out', 'cat', '/sdcard/ui.xml')
        try:
            root = ET.fromstring(raw)
            break
        except ET.ParseError:
            time.sleep(1)
    out = []
    for n in root.iter('node'):
        b = [int(x) for x in re.findall(r'\d+', n.get('bounds', '[0,0][0,0]'))]
        out.append({'text': n.get('text', ''), 'desc': n.get('content-desc', ''), 'click': n.get('clickable') == 'true',
                    'bounds': b, 'focused': n.get('focused') == 'true', 'enabled': n.get('enabled') == 'true',
                    'checked': n.get('checked') == 'true', 'selected': n.get('selected') == 'true'})
    return out

def find(label, index=0):
    hits = [n for n in nodes() if label.lower() in (n['text'] + ' ' + n['desc']).lower() and n['bounds'][2] > 0]
    exact = [n for n in hits if n['text'].lower() == label.lower() or n['desc'].lower() == label.lower()]
    hits = exact or hits
    return hits[index] if len(hits) > index else None

def tap_node(n):
    x = (n['bounds'][0] + n['bounds'][2]) // 2; y = (n['bounds'][1] + n['bounds'][3]) // 2
    adb('shell', 'input', 'tap', str(x), str(y))

def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'texts':
        for n in nodes():
            t = (n['text'] or n['desc']).replace('\n', ' ')
            if t:
                flags = ''.join(c for c, f in (('C', n['click']), ('F', n['focused']), ('X', n['checked']), ('S', n['selected']), ('D', not n['enabled'])) if f)
                print(f"{flags:3} {n['bounds']} {t}")
    elif cmd == 'tap':
        n = find(args[0], int(args[1]) if len(args) > 1 else 0)
        if not n: sys.exit(f'NOT FOUND: {args[0]}')
        tap_node(n); time.sleep(float(os.environ.get('WAIT', '1.5')))
    elif cmd == 'tapxy':
        adb('shell', 'input', 'tap', args[0], args[1]); time.sleep(1.5)
    elif cmd == 'type':
        adb('shell', 'input', 'text', args[0].replace(' ', '%s')); time.sleep(0.5)
    elif cmd == 'typeseed':
        creds = dict(re.findall(r'--\s*(\S+@instrumed\.com)\s*/\s*(\S+)', open(SEED, encoding='utf-8').read()))
        pw = creds[args[0]].replace('!', '\\!').replace('$', '\\$')
        subprocess.run([ADB, 'shell', 'input', 'text', pw], capture_output=True); time.sleep(0.5)
        print('typed password for', args[0])
    elif cmd == 'clear':
        adb('shell', 'input', 'keyevent', 'KEYCODE_MOVE_END')
        for _ in range(int(args[0]) if args else 40): adb('shell', 'input', 'keyevent', 'KEYCODE_DEL')
    elif cmd == 'key':
        adb('shell', 'input', 'keyevent', 'KEYCODE_' + args[0]); time.sleep(1)
    elif cmd == 'swipe':
        if args[0] == 'up': adb('shell', 'input', 'swipe', '540', '1800', '540', '700', '300')
        else: adb('shell', 'input', 'swipe', '540', '700', '540', '1800', '300')
        time.sleep(1)
    elif cmd == 'shot':
        png = adb('exec-out', 'screencap', '-p')
        p = os.path.join(SHOTS, args[0] + '.png'); open(p, 'wb').write(png); print(p)

main()
