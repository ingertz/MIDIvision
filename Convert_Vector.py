import collections
import re
import zlib
from fractions import Fraction

# PDF의 도형 데이터에서 악보를 직접 읽기
#
# 악보 프로그램에서 만든 PDF는 그림이 아니라 도형으로 되어 있습니다. 오선·기둥·세로줄은 직사각형,
# 음표 머리·쉼표·임시표·음자리표는 윤곽선이고, 같은 기호는 똑같은 윤곽선으로 반복해서 나옵니다.
# 그래서 그림을 보고 추측하는 악보 인식(Audiveris) 없이, 어떤 기호가 어느 좌표에 있는지를 그대로 읽습니다.
# 점음표의 점, 조표, X 모양 음표처럼 작은 기호도 놓치지 않고, 20쪽을 몇 초에 읽습니다.
#
# 스캔본이나 사진으로 된 PDF에는 도형이 없으므로 이 방법을 쓸 수 없습니다 (read_pdf가 None을 반환).
# 기호를 알아보는 기준(크기, 윤곽선의 점 개수)은 at-elise 밴드스코어의 글꼴에 맞춘 것입니다.
#
# 길이 단위: 기호 크기는 오선 간격(u)의 배수로 비교해서 악보가 크게/작게 인쇄되어도 통하게 합니다.

# ---------------------------------------------------------------------------------------------
# PDF에서 채워진 도형 꺼내기
# ---------------------------------------------------------------------------------------------
TOKEN = re.compile(rb'[-+]?\d*\.?\d+|[A-Za-z*\'"]+|/[^\s/\[\]<>()]+|\[|\]|\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>')

def page_contents(pdf_path):
    """쪽마다 (그리기 명령, 쪽 높이). pypdf가 없으면 단순한 구조의 PDF만 읽는 임시 방식으로 대신함"""
    try:
        import pypdf
    except ImportError:
        data = open(pdf_path, 'rb').read()
        if b'/Encrypt' in data:
            raise RuntimeError("비밀번호가 걸린 PDF입니다. 비밀번호를 푼 PDF로 다시 시도해 주세요.")
        pages = []
        for m in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream', data, re.S):
            try:
                stream = zlib.decompress(m.group(1))
            except zlib.error:
                continue
            if len(stream) > 20000 and re.search(rb'\d m\b', stream):
                pages.append((stream, 842.0))
        return pages
    reader = pypdf.PdfReader(pdf_path)
    if reader.is_encrypted and not reader.decrypt(''):
        raise RuntimeError("비밀번호가 걸린 PDF입니다. 비밀번호를 푼 PDF로 다시 시도해 주세요.")
    pages = []
    for page in reader.pages:
        contents = page.get_contents()
        pages.append((contents.get_data() if contents is not None else b'', float(page.mediabox.height)))
    return pages

PAGE_WIDTH = 595.0   # 보표 줄을 고르는 기준 너비 (A4). 줄은 쪽 너비의 30% 이상인 가는 직사각형

def parse_shapes(stream, height):
    """
    채워진 도형 목록. 좌표는 왼쪽 위가 원점(pt).
    {'rect': 직사각형인지, 'box': (x0, y0, x1, y1), 'subpaths': [[(x, y), ...]]}
    """
    ctm = [1, 0, 0, 1, 0, 0]
    stack = []
    nums = []
    shapes = []
    subpaths = []
    current = []
    rects = []

    def tr(x, y):
        a, b, c, d, e, f = ctm
        return (a * x + c * y + e, height - (b * x + d * y + f))

    for token in TOKEN.findall(stream):
        if token[:1] in b'0123456789.-+':
            nums.append(float(token))
            continue
        op = token
        if op == b'cm' and len(nums) >= 6:
            a, b, c, d, e, f = nums[-6:]
            A, B, C, D, E, F = ctm
            ctm = [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]
        elif op == b'q':
            stack.append(list(ctm))
        elif op == b'Q':
            if stack:
                ctm = stack.pop()
        elif op == b'm' and len(nums) >= 2:
            if current:
                subpaths.append(current)
            current = [tr(*nums[-2:])]
        elif op == b'l' and len(nums) >= 2:
            current.append(tr(*nums[-2:]))
        elif op == b'c' and len(nums) >= 6:
            # 곡선은 네 토막으로 나눈 점으로 바꿔 둠
            p = nums[-6:]
            p1, p2, p3 = tr(p[0], p[1]), tr(p[2], p[3]), tr(p[4], p[5])
            p0 = current[-1] if current else p1
            for k in (1, 2, 3, 4):
                t = k / 4
                u = 1 - t
                current.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
        elif op == b'h':
            if current:
                subpaths.append(current)
                current = []
        elif op == b're' and len(nums) >= 4:
            x, y, w, h = nums[-4:]
            rects.append([tr(x, y), tr(x + w, y + h)])
        elif op in (b'f', b'f*', b'F', b'B', b'b', b'B*', b'b*'):
            if current:
                subpaths.append(current)
                current = []
            for (xa, ya), (xb, yb) in rects:
                shapes.append({'rect': True, 'box': (min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)), 'subpaths': []})
            if subpaths:
                xs = [p[0] for sp in subpaths for p in sp]
                ys = [p[1] for sp in subpaths for p in sp]
                shapes.append({'rect': False, 'box': (min(xs), min(ys), max(xs), max(ys)), 'subpaths': subpaths})
            subpaths, rects = [], []
        elif op in (b'n', b'S', b's'):
            subpaths, rects, current = [], [], []
        nums = []
    return shapes

# ---------------------------------------------------------------------------------------------
# 기호 알아보기
# ---------------------------------------------------------------------------------------------
# 음표 머리. slash 는 기타의 리듬 표시(음높이 없는 사선 머리)로, 박을 세는 데만 쓰고 음은 내지 않음
HEADS = ('black', 'half', 'whole', 'x', 'slash')

def classify(shape, u):
    """도형 하나가 무슨 기호인지 (모르면 None). u = 오선 간격"""
    x0, y0, x1, y1 = shape['box']
    w, h = (x1 - x0) / u, (y1 - y0) / u
    if shape['rect']:
        if w < 0.4 and h > 1.1:
            return 'vertical'                      # 기둥 / 세로줄
        if 0.45 < w < 0.95 and h > 3.0:
            return 'thick'                         # 굵은 세로줄 (도돌이표, 끝세로줄)
        if 0.35 < h < 0.68 and w > 0.8:
            return 'beam'                          # 수평 빔. 짧은 것은 온쉼표/2분쉼표일 수도 있음
        if h < 0.3 and 1.1 < w < 3.6:
            return 'ledger'                        # 덧줄
        return None
    n = sum(len(sp) for sp in shape['subpaths'])
    subs = len(shape['subpaths'])
    if n == 6 and subs == 1 and w > 2.5:
        return 'volta' if (w > 15 and 2.0 < h < 3.4) else 'bracket'   # 갈고리 달린 선
    if n == 4 and subs == 1:
        if w > 2.3 and 0.5 < h < 2.6:
            return 'beam'                          # 기울어진 빔
        if 1.0 < w < 1.4 and 1.8 < h < 2.3:
            return 'slash'                         # 기타의 리듬 표시(음높이 없는 사선 머리)
        return None
    if 1.03 < w < 1.2 and 1.1 < h < 1.3 and n == 12 and subs == 1:
        return 'x'
    if 1.1 < w < 1.27 and 1.03 < h < 1.23 and n > 20:
        return 'black' if subs == 1 else 'half' if subs == 2 else None
    if 1.33 < w < 1.5 and 0.98 < h < 1.16 and subs == 2:
        return 'whole'
    if 0.7 < w < 0.85 and 2.15 < h < 2.42 and n > 80:
        return 'flat'
    if 1.26 < w < 1.5 and 2.15 < h < 2.42 and subs == 3:
        return 'dflat'
    if 0.52 < w < 0.7 and 2.7 < h < 3.0 and subs == 2:
        return 'natural'
    if 0.78 < w < 0.94 and 2.85 < h < 3.12 and subs == 2:
        return 'sharp'
    if 0.33 < w < 0.54 and 0.33 < h < 0.54:
        return 'dot'
    if 0.95 < w < 1.12 and 1.75 < h < 1.92 and subs == 1:
        return 'rest8'
    if 1.15 < w < 1.38 and 2.72 < h < 2.95 and subs == 1 and n > 120:
        return 'rest16'
    if 0.95 < w < 1.12 and 2.95 < h < 3.26 and subs == 1 and n > 140:
        return 'rest4'
    if 1.07 < w < 1.23 and 2.95 < h < 3.26 and subs == 1 and n < 110:
        return 'flag'
    if 1.07 < w < 1.27 and 3.18 < h < 3.45 and subs == 2:
        return 'flag2'
    if 0.77 < w < 0.96 and 1.3 < h < 1.5 and subs == 1 and n > 90:
        return 'tuplet3'
    if 2.2 < w < 2.55 and 6.4 < h < 7.1:
        return 'clefG'
    if 2.2 < w < 2.55 and 3.0 < h < 3.35 and subs == 3:
        return 'clefF'
    if 2.2 < w < 2.55 and 1.9 < h < 2.15 and subs == 3:
        return 'simile1'                           # % : 앞 마디 반복
    if 3.0 < w < 3.3 and 1.9 < h < 2.15 and subs == 4:
        return 'simile2'                           # 앞 두 마디 반복
    if 0.68 < w < 0.92 and 0.72 < h < 0.95 and subs == 2:
        return 'ring'                              # 음표 위의 작은 ○ (오픈 하이햇)
    if subs == 1 and 9 <= n <= 12 and w > 1.3 and h < max(0.8, 0.45 * w):
        return 'arc'                               # 붙임줄 / 이음줄
    return None

class Staff:
    def __init__(self, lines, left, right):
        self.lines = lines                    # 줄의 가운데 y (위에서 아래로)
        self.left, self.right = left, right
        self.top, self.bottom = lines[0], lines[-1]
        self.space = (lines[-1] - lines[0]) / (len(lines) - 1)
        self.tab = len(lines) != 5

    def position(self, y):
        """맨 윗줄을 0으로, 아래로 갈수록 커지는 반 칸 단위 위치"""
        return int(round((y - self.top) / (self.space / 2)))

def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

def read_layout(shapes, page_width):
    """
    반환: (오선 간격 u, [시스템]). 시스템 = 위에서 아래로 놓인 Staff 목록 (TAB 포함).
    악보가 없는 쪽이면 (None, [])
    """
    lines = sorted((s['box'][1], s['box'][3], s['box'][0], s['box'][2]) for s in shapes
                   if s['rect'] and s['box'][2] - s['box'][0] > page_width * 0.3 and s['box'][3] - s['box'][1] < 1.2)
    if len(lines) < 5:
        return None, []
    gaps = sorted(b[0] - a[0] for a, b in zip(lines, lines[1:]) if b[0] - a[0] > 0.5)
    if not gaps:
        return None, []
    typical = gaps[len(gaps) // 4]     # 줄 사이 간격 (보표 사이의 큰 간격이 섞여 있으므로 작은 쪽에서 고름)
    groups = []
    for y0, y1, x0, x1 in lines:
        mid = (y0 + y1) / 2
        if groups and mid - groups[-1][-1][0] < typical * 1.6:
            groups[-1].append((mid, x0, x1))
        else:
            groups.append([(mid, x0, x1)])
    staves = [Staff([g[0] for g in group], min(g[1] for g in group), max(g[2] for g in group))
              for group in groups if len(group) in (4, 5, 6)]
    fives = [st.space for st in staves if not st.tab]
    if not fives:
        return None, []
    u = sorted(fives)[len(fives) // 2]
    # 시스템: 왼쪽 끝의 긴 세로선 하나가 한 시스템을 묶음. 없으면 쪽 전체를 한 시스템으로
    talls = sorted((s['box'][1], s['box'][3]) for s in shapes
                   if s['rect'] and s['box'][2] - s['box'][0] < 0.4 * u and s['box'][3] - s['box'][1] > 20 * u)
    systems = []
    taken = set()
    for y0, y1 in sorted(talls, key=lambda t: t[0] - t[1]):      # 긴 선부터: 기타 보표와 TAB만 묶는 짧은 선은 시스템이 아님
        members = [st for st in staves if st.top >= y0 - u and st.bottom <= y1 + u]
        if members and not any(id(st) in taken for st in members):
            systems.append(members)
            taken |= {id(st) for st in members}
    systems.sort(key=lambda members: members[0].top)
    return u, systems or [staves]

def staff_roles(system):
    """
    시스템 안의 5선 보표마다 역할을 붙임: [(Staff, 역할)].
    밴드스코어 배치: 6줄 TAB 바로 위 = 기타, 4줄 TAB 바로 위 = 베이스, TAB들보다 아래의 맨 끝 보표 = 드럼,
    맨 위 = 보컬, 나머지 = 건반. TAB이 없는 악보는 전부 건반
    """
    roles = []
    seen_tab = False
    for i, staff in enumerate(system):
        if staff.tab:
            seen_tab = True
            continue
        below = system[i + 1] if i + 1 < len(system) else None
        if below is not None and below.tab:
            roles.append((staff, 'guitar' if len(below.lines) == 6 else 'bass'))
        elif seen_tab and all(st.tab for st in system[i + 1:]):
            roles.append((staff, 'drums'))
        else:
            roles.append((staff, 'keyboard'))
    if seen_tab and roles and roles[0][1] == 'keyboard':
        roles[0] = (roles[0][0], 'vocal')
    return roles

# ---------------------------------------------------------------------------------------------
# 한 보표 읽기
# ---------------------------------------------------------------------------------------------
FLATS = 'BEADGCF'
SHARPS = 'FCGDAEB'
STEP_SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
TOP_LINE = {'F': 3 * 7 + 5, 'G': 5 * 7 + 3}   # 맨 윗줄의 음이름 순번: 낮은음자리표 A3, 높은음자리표 F5
ACCIDENTALS = {'flat': -1, 'dflat': -2, 'natural': 0, 'sharp': 1}

def staff_zones(system, u):
    """
    보표마다 그 보표의 기호로 볼 세로 구역 {Staff: (위, 아래)}.
    보표 사이의 덧줄 음표가 위아래 보표에 겹쳐 잡히지 않도록, 이웃한 보표(TAB 포함)와의 한가운데에서 나눔
    """
    zones = {}
    for i, staff in enumerate(system):
        above = (system[i - 1].bottom + staff.top) / 2 if i else staff.top - 7.5 * u
        below = (staff.bottom + system[i + 1].top) / 2 if i + 1 < len(system) else staff.bottom + 5.5 * u
        zones[staff] = (max(above, staff.top - 9 * u), min(below, staff.bottom + 9 * u))
    return zones

def staff_items(zone, kinds):
    """보표 구역 안의 기호 [(종류, box)]. kinds = 쪽 전체의 [(종류, box)]"""
    return [(k, b) for k, b in kinds if zone[0] <= (b[1] + b[3]) / 2 < zone[1]]

def staff_barlines(staff, kinds, u):
    """보표를 위에서 아래까지 가로지르는 세로줄의 x (마디 경계)"""
    xs = sorted(b[0] for k, b in kinds if k in ('vertical', 'thick')
                and b[1] <= staff.top + 0.25 * u and b[3] >= staff.bottom - 0.25 * u and b[3] - b[1] < 20 * u)
    out = []
    for x in xs:
        if not out or x - out[-1] > 1.6 * u:
            out.append(x)
    return out

def read_events(staff, items, bars, u):
    """
    보표의 음표/쉼표를 사건 목록으로: {'x', 'sx', 'voice', 'dur', 'heads': [(종류, box)], 'rest'}
    길이는 머리 모양, 기둥에 걸린 빔/꼬리 수, 점, 셋잇단 표시로 정함 (4분음표 = 1)
    """
    heads = [(k, b) for k, b in items if k in HEADS]
    stems = [b for k, b in items if k == 'vertical' and b[3] - b[1] < 11 * u
             and not (b[1] <= staff.top + 0.25 * u and b[3] >= staff.bottom - 0.25 * u
                      and bars and min(abs(b[0] - x) for x in bars) < 0.6 * u)]
    beams = [b for k, b in items if k == 'beam']
    flags = [(1 if k == 'flag' else 2, b) for k, b in items if k in ('flag', 'flag2')]
    dots = [b for k, b in items if k == 'dot']
    hlines = list(staff.lines) + [center(b)[1] for k, b in items if k == 'ledger']

    def dotted(box):
        cy = center(box)[1]
        return any(0.1 * u < d[0] - box[2] < 1.5 * u and abs(center(d)[1] - cy) <= 0.75 * u for d in dots)

    events = []
    used = set()
    for stem in stems:
        sx = (stem[0] + stem[2]) / 2
        chord = []
        for i, (kind, b) in enumerate(heads):
            cx, cy = center(b)
            if not stem[1] - 0.46 * u <= cy <= stem[3] + 0.46 * u:
                continue
            if kind == 'x':     # X 머리는 기둥이 가운데나 양 끝을 지남
                attached = min(abs(sx - cx), abs(sx - b[2]), abs(sx - b[0])) < 0.3 * u
            else:               # 둥근 머리는 기둥이 오른쪽 끝(위로 뻗을 때)이나 왼쪽 끝(아래로 뻗을 때)에 붙음
                attached = min(abs(sx - (b[2] - 0.12 * u)), abs(sx - (b[0] + 0.1 * u))) < 0.3 * u
            if attached:
                chord.append(i)
        if not chord:
            continue
        ys = [center(heads[i][1])[1] for i in chord]
        up = (min(ys) - stem[1]) > (stem[3] - max(ys))
        count = sum(1 for b in beams if b[0] - 0.15 * u <= sx <= b[2] + 0.15 * u
                    and b[1] <= stem[3] + 0.4 * u and b[3] >= stem[1] - 0.4 * u)
        tip = stem[1] if up else stem[3]
        count += sum(n for n, f in flags if abs(f[0] - stem[0]) < 0.46 * u and f[1] - 0.6 * u <= tip <= f[3] + 0.6 * u)
        dur = Fraction(2) if any(heads[i][0] == 'half' for i in chord) else Fraction(1) / (2 ** count)
        if any(dotted(heads[i][1]) for i in chord):
            dur *= Fraction(3, 2)
        events.append({'x': min(heads[i][1][0] for i in chord), 'sx': sx, 'voice': 'up' if up else 'down',
                       'dur': dur, 'heads': [heads[i] for i in chord], 'rest': False})
        used |= set(chord)
    # 기둥 없는 음표(온음표). 같은 자리에 쌓인 머리들은 한 화음. 기둥 없는 X나 사선은 "2×" 같은 글자이므로 제외
    loose = sorted(((kind, b) for i, (kind, b) in enumerate(heads) if i not in used and kind in ('whole', 'half', 'black')),
                   key=lambda h: h[1][0])
    for kind, b in loose:
        last = events[-1] if events and events[-1].get('stemless') else None
        if last is not None and b[0] - last['x'] < 1.5 * u:
            last['heads'].append((kind, b))
            continue
        dur = (Fraction(4) if kind == 'whole' else Fraction(1)) * (Fraction(3, 2) if dotted(b) else 1)
        events.append({'x': b[0], 'sx': center(b)[0], 'voice': 'up', 'dur': dur, 'heads': [(kind, b)], 'rest': False,
                       'stemless': True})

    middle = staff.top + 2 * staff.space
    for kind, b in items:
        cx, cy = center(b)
        if kind in ('rest16', 'rest8', 'rest4'):
            dur = {'rest16': Fraction(1, 4), 'rest8': Fraction(1, 2), 'rest4': Fraction(1)}[kind]
        elif kind == 'beam' and 1.0 * u < b[2] - b[0] < 1.75 * u and b[3] - b[1] < 0.7 * u \
                and not any(min(abs(s[0] - b[0]), abs(s[0] - b[2])) < 0.3 * u for s in stems if s[1] - 0.4 * u <= cy <= s[3] + 0.4 * u):
            # 온쉼표(줄에 매달림) / 2분쉼표(줄 위에 얹힘). 기둥에 붙은 것은 짧은 빔이므로 제외
            hanging = any(abs(b[1] - y) < 0.27 * u for y in hlines)
            sitting = any(abs(b[3] - y) < 0.27 * u for y in hlines)
            if not hanging and not sitting:
                continue
            dur = Fraction(4) if hanging else Fraction(2)
        else:
            continue
        if dotted(b):
            dur *= Fraction(3, 2)
        events.append({'x': b[0], 'sx': cx, 'voice': 'down' if cy > middle + 0.2 * u else 'up',
                       'dur': dur, 'heads': [], 'rest': True})

    # 셋잇단음표: 작은 3의 양옆 괄호(없으면 그 위아래의 빔)가 덮는 범위의 음표/쉼표는 길이의 2/3
    brackets = [b for k, b in items if k == 'bracket']
    for kind, b in items:
        if kind != 'tuplet3':
            continue
        cx, cy = center(b)
        near = [br for br in brackets if abs(center(br)[1] - cy) < 1.5 * u and (0 <= b[0] - br[2] < 1.2 * u or 0 <= br[0] - b[2] < 1.2 * u)]
        if near:
            lo, hi = min(br[0] for br in near), max(br[2] for br in near)
        else:
            over = [bm for bm in beams if bm[0] - 0.4 * u <= cx <= bm[2] + 0.4 * u and abs(center(bm)[1] - cy) < 3.5 * u]
            if not over:
                continue
            lo, hi = min(bm[0] for bm in over), max(bm[2] for bm in over)
        voice = 'up' if cy < middle else 'down'
        for e in events:
            if lo - 0.6 * u <= e['sx'] <= hi + 0.6 * u and e['voice'] == voice and not e.get('tuplet'):
                e['dur'] *= Fraction(2, 3)
                e['tuplet'] = True
    return events

def voices_of(events, u, nominal=Fraction(4)):
    """한 마디의 사건을 성부별로 나눠 시작 시간을 누적. 반환: [(사건 목록, 길이 합계), ...] (사건마다 'at')"""
    inside = sorted(events, key=lambda e: e['x'])
    ups = [e for e in inside if e['voice'] == 'up']
    downs = [e for e in inside if e['voice'] == 'down']
    # 기둥이 위로 뻗은 음표와 아래로 뻗은 음표가 같은 자리에 겹쳐 있으면 두 성부
    two = any(abs(a['x'] - b['x']) < 0.6 * u for a in ups for b in downs if not a['rest'] and not b['rest'])
    if not two and ups and downs and sum(e['dur'] for e in inside) != nominal \
            and nominal in (sum(e['dur'] for e in ups), sum(e['dur'] for e in downs)):
        two = True   # 겹치지 않는 두 성부: 한 줄로 이으면 박이 안 맞고, 나누면 한쪽이 딱 맞음
    voices = []
    for voice in ([ups, downs] if two else [inside]):
        at = Fraction(0)
        for e in voice:
            e['at'] = at
            at += e['dur']
        if voice:
            voices.append((voice, at))
    return voices

# ---------------------------------------------------------------------------------------------
# 음높이
# ---------------------------------------------------------------------------------------------
def sign_position(staff, kind, box):
    """임시표가 가리키는 줄/칸 위치 (플랫은 아래쪽 불룩한 부분이 기준)"""
    return staff.position(box[1] + (0.72 if kind in ('flat', 'dflat') else 0.5) * (box[3] - box[1]))

def key_of(signs):
    """조표 기호들 -> 조 (플랫이면 음수, 샤프면 양수)"""
    flats = sum(1 for k, _ in signs if k == 'flat')
    sharps = sum(1 for k, _ in signs if k == 'sharp')
    return -flats if flats else sharps

def pitch(staff, box, clef, key, altered):
    pos = staff.position(center(box)[1])
    index = TOP_LINE[clef] - pos
    step, octave = 'CDEFGAB'[index % 7], index // 7
    in_key = -1 if step in FLATS[:max(0, -key)] else 1 if step in SHARPS[:max(0, key)] else 0
    return 12 * (octave + 1) + STEP_SEMITONES[step] + altered.get(pos, in_key)

# ---------------------------------------------------------------------------------------------
# 한 시스템 읽기
# ---------------------------------------------------------------------------------------------
ROLE_NAMES = {'vocal': 'Vocal', 'keyboard': '', 'guitar': 'Guitar', 'bass': 'Bass', 'drums': 'Drums'}

def read_system(system, kinds, u, state, cs):
    """
    한 시스템(악보 한 줄)을 읽습니다.
    state: 보표별로 이어지는 정보 {(역할, 순번): {'clef', 'key'}} 와 'nominal'
    반환: (마디 수, {(역할, 순번): [마디...]}, [마디별 표시 {'forward', 'backward', 'ending_open'}])
    마디 = Convert_Score.empty_measure() 형식
    """
    roles = staff_roles(system)
    if not roles:
        return 0, {}, []
    last = roles[-1][0]
    bars = staff_barlines(last, kinds, u)
    edges = ([last.left] + bars) if bars and bars[0] - last.left > 8 * u else bars
    if len(edges) < 2:
        return 0, {}, []
    count = len(edges) - 1

    def measure_of(x):
        for j in range(count):
            if edges[j] - 0.2 * u <= x < edges[j + 1] - 0.2 * u:
                return j
        return None

    # --- 도돌이표: 보표 안에 세로로 나란한 점 두 개가 세로줄 옆에 있음 (맨 위 보표로 판단)
    marks = [{'forward': False, 'backward': 0, 'ending_open': False} for _ in range(count)]
    top = roles[0][0]
    dots = [center(b) for k, b in kinds if k == 'dot' and top.top < center(b)[1] < top.bottom]
    for x, y in dots:
        if not any(abs(x - x2) < 0.2 * u and 0.6 * u < abs(y - y2) < 1.4 * u for x2, y2 in dots):
            continue
        for j, edge in enumerate(edges):
            if 0 < x - edge < 3 * u and j < count:
                marks[j]['forward'] = True          # 세로줄 오른쪽의 점: 여기서부터 반복
            elif 0 < edge - x < 3 * u and j > 0:
                marks[j - 1]['backward'] = 2        # 세로줄 왼쪽의 점: 여기까지 반복
    # --- 괄호(1. / 2.): 맨 위 보표 위쪽의 갈고리 달린 긴 선. 왼쪽에 갈고리가 있으면 거기서 시작
    for k, b in kinds:
        if k == 'volta' and top.top - 14 * u < b[3] < top.top:
            j = measure_of(b[0] + u)
            if j is not None and b.left_hook:
                marks[j]['ending_open'] = True

    # --- 보표별 읽기
    per_staff = []
    counters = collections.Counter()
    zones = staff_zones(system, u)
    for staff, role in roles:
        key_id = (role, counters[role])
        counters[role] += 1
        info = state.setdefault(key_id, {'clef': 'F' if role == 'bass' else 'G', 'key': 0})
        # 보표 왼쪽 바깥은 세로로 쓴 파트 이름: 글자 'e', 'o' 가 온음표와 똑같이 생겨서 음표로 잡히므로 제외
        items = [(k, b) for k, b in staff_items(zones[staff], kinds) if b[0] > staff.left - 0.5 * u]
        events = read_events(staff, items, bars, u)
        measures_events = [[] for _ in range(count)]
        for e in events:
            j = measure_of(e['x'])
            if j is not None:
                measures_events[j].append(e)

        # 음자리표와 조표 (보표 첫머리)
        first_x = min((e['x'] for e in events), default=staff.right)
        for k, b in items:
            if k in ('clefG', 'clefF') and b[0] < staff.left + 6 * u:
                info['clef'] = k[-1]
        signs = [(k, b) for k, b in items if k in ACCIDENTALS and staff.top - 2 * u < center(b)[1] < staff.bottom + 2 * u]
        heads = [h for e in events for h in e['heads']]

        def attached(sign):
            kind, b = sign
            pos = sign_position(staff, kind, b)
            return any(0 <= hb[0] - b[2] < 2.1 * u and staff.position(center(hb)[1]) == pos for _, hb in heads)

        header = [s for s in signs if s[1][2] < first_x - 0.8 * u and s[1][0] < edges[0] + 14 * u and not attached(s)]
        if role != 'drums':
            info['key'] = key_of(header)

        # 붙임줄: 같은 높이의 두 음표 머리를 잇는 호. 줄 끝/줄 처음에서 끊긴 것은 다음/이전 시스템과 이어짐
        tie_start, tie_stop = set(), set()
        if role != 'drums':
            for k, arc in items:
                if k != 'arc':
                    continue
                ay = center(arc)[1]
                a = [hb for _, hb in heads if arc[0] - 1.6 * u <= hb[2] <= arc[0] + 0.8 * u and abs(center(hb)[1] - ay) < 2.2 * u]
                b = [hb for _, hb in heads if arc[2] - 0.8 * u <= hb[0] <= arc[2] + 1.6 * u and abs(center(hb)[1] - ay) < 2.2 * u]
                pairs = [(ha, hb) for ha in a for hb in b if staff.position(center(ha)[1]) == staff.position(center(hb)[1])]
                if pairs:
                    tie_start.add(pairs[0][0])
                    tie_stop.add(pairs[0][1])
                elif a and not b and arc[2] > edges[-1] - 1.5 * u:
                    tie_start.add(a[0])
                elif b and not a and arc[0] < first_x - 0.3 * u:
                    tie_stop.add(b[0])

        rings = [center(b) for k, b in items if k == 'ring']
        similes = [(k, center(b)[0]) for k, b in items if k in ('simile1', 'simile2') and staff.top - u < center(b)[1] < staff.bottom + u]

        staff_measures = []
        for j in range(count):
            left, right = edges[j], edges[j + 1]
            # 줄 중간의 조바뀜: 마디 첫머리에 음표에 붙지 않은 임시표들이 모여 있음
            if j > 0 and role != 'drums':
                first = min((e['x'] for e in measures_events[j]), default=right)
                change = [s for s in signs if left - 0.5 * u <= s[1][0] < min(first - 0.8 * u, left + 12 * u) and not attached(s)]
                if change:
                    info['key'] = key_of(change)
            altered = {}
            voices = []
            for voice, total in voices_of(measures_events[j], u, state['nominal']):
                notes, xs = [], []
                for e in voice:
                    x = (e['x'] - left) / u * 10
                    if e['rest']:
                        notes.append((e['at'], e['dur'], None, False, False, False))
                        xs.append(x)
                        continue
                    if all(kind == 'slash' for kind, _ in e['heads']):
                        notes.append((e['at'], e['dur'], None, False, False, False))   # 박을 맞추는 기준으로만 사용
                        xs.append(x)
                        continue
                    for kind, hb in sorted(e['heads'], key=lambda h: h[1][0]):
                        if kind == 'slash':
                            continue
                        if role == 'drums':
                            pos = staff.position(center(hb)[1])
                            index = TOP_LINE['G'] - pos
                            head = 'normal'
                            if kind == 'x':
                                cx, cy = center(hb)
                                head = 'circle-x' if any(abs(rx - cx) < 0.9 * u and 0 < cy - ry < 5 * u for rx, ry in rings) else 'x'
                            notes.append((e['at'], e['dur'], cs.drum_note_from_position('CDEFGAB'[index % 7], index // 7, head),
                                          True, False, False))
                        else:
                            pos = staff.position(center(hb)[1])
                            mine = [s for s in signs if 0 <= hb[0] - s[1][2] < 2.1 * u and sign_position(staff, *s) == pos]
                            if mine:
                                altered[pos] = min(ACCIDENTALS[k] for k, _ in mine) if all(k != 'natural' for k, _ in mine) else 0
                            midi = pitch(staff, hb, info['clef'], info['key'], altered)
                            notes.append((e['at'], e['dur'], max(0, min(127, midi)), False, hb in tie_start, hb in tie_stop))
                        xs.append(x)
                voices.append({'notes': notes, 'xs': xs, 'extent': total, 'loose': None, 'width': (right - left) / u * 10,
                               'rest_only': all(e['rest'] for e in voice)})
            simile = 0
            if not measures_events[j]:
                if any(k == 'simile1' and left < x < right for k, x in similes):
                    simile = 1
                elif any(k == 'simile2' and (abs(x - right) < 1.5 * u or abs(x - left) < 1.5 * u) for k, x in similes):
                    simile = 2
            staff_measures.append({'voices': voices, 'simile': simile})
        per_staff.append((key_id, staff_measures))

    # --- 마디 길이와 리듬 보정
    result = {key_id: [] for key_id, _ in per_staff}
    for j in range(count):
        totals = collections.Counter(v['extent'] for _, ms in per_staff for v in ms[j]['voices'] if not v['rest_only'])
        nominal = state['nominal']
        if totals:
            best, votes = totals.most_common(1)[0]
            # 대부분의 성부가 같은 길이면 그것이 이 마디의 길이 (2/4 마디, 못갖춘마디 등). 애매하면 앞 마디의 박자 유지
            if totals[nominal] * 2 < votes:
                nominal = best
        state['nominal'] = nominal
        pseudo = [v for _, ms in per_staff for v in ms[j]['voices']]
        retimed = sum(1 for v in pseudo if v['extent'] != nominal and not v['rest_only'])
        cs.retime_measure(pseudo, nominal)
        state['voices'] += sum(1 for v in pseudo if not v['rest_only'])
        state['retimed'] += retimed
        for key_id, ms in per_staff:
            m = cs.empty_measure()
            for v in ms[j]['voices']:
                for note, x in zip(v['notes'], v['xs']):
                    if note[2] is not None:
                        m['notes'].append(note)
                        m['xs'].append(x)
            m.update(extent=nominal, nominal=nominal, width=(edges[j + 1] - edges[j]) / u * 10,
                     simile=ms[j]['simile'], forward=marks[j]['forward'], backward=marks[j]['backward'],
                     ending_open=marks[j]['ending_open'])
            result[key_id].append(m)
    return count, result, marks

# ---------------------------------------------------------------------------------------------
# 문서 전체
# ---------------------------------------------------------------------------------------------
def left_hooked(shape):
    """갈고리 달린 선에서 갈고리(아래로 꺾인 부분)가 왼쪽 끝에 있는지"""
    x0, y0, x1, y1 = shape['box']
    low = [x for sp in shape['subpaths'] for x, y in sp if y > (y0 + y1) / 2]
    return bool(low) and sum(low) / len(low) < (x0 + x1) / 2

class Box(tuple):
    """(x0, y0, x1, y1) 에 괄호의 갈고리 방향을 덧붙인 것"""
    left_hook = False

def read_pdf(pdf_path, log=print):
    """
    PDF의 도형에서 악보를 읽어 Convert_Score.expand_score 가 받는 파트 목록으로 반환합니다.
    도형으로 된 악보가 아니면(스캔본 등) None
    반환: (파트 목록, 악보가 시작되는 쪽 번호)
    """
    import Convert_Score as cs

    state = {'nominal': Fraction(4), 'voices': 0, 'retimed': 0}
    tracks = {}       # (역할, 순번) -> 마디 목록
    order = []
    total = 0
    first_page = None
    try:
        contents = page_contents(pdf_path)
    except RuntimeError:
        raise                      # 비밀번호가 걸린 PDF 등, 사용자에게 알려야 하는 오류
    except Exception:
        return None                # PDF 구조를 읽지 못함 -> 그림 인식으로 넘김
    for number, (stream, height) in enumerate(contents, 1):
        shapes = parse_shapes(stream, height)
        u, systems = read_layout(shapes, PAGE_WIDTH)
        if not systems:
            continue
        kinds = []
        for s in shapes:
            kind = classify(s, u)
            if kind is None:
                continue
            box = Box(s['box'])
            if kind == 'volta':
                box.left_hook = left_hooked(s)
            kinds.append((kind, box))
        for system in systems:
            count, measures, _ = read_system(system, kinds, u, state, cs)
            if not count:
                continue
            first_page = first_page or number
            for key_id in measures:
                if key_id not in tracks:
                    tracks[key_id] = [cs.empty_measure() for _ in range(total)]
                    order.append(key_id)
            for key_id in order:
                tracks[key_id] += measures.get(key_id) or [cs.empty_measure() for _ in range(count)]
            total += count
    if not total or not any(m['notes'] for ms in tracks.values() for m in ms):
        return None

    parts = []
    for key_id in order:
        measures = tracks[key_id]
        # 마디 반복 기호: 앞 마디(들)의 내용을 그대로 가져옴
        for i, m in enumerate(measures):
            k = m['simile']
            if k and i >= k and not m['notes']:
                m['notes'] = list(measures[i - k]['notes'])
                m['xs'] = list(measures[i - k]['xs'])
        parts.append({'name': ROLE_NAMES[key_id[0]], 'program': None, 'role': key_id[0], 'measures': measures})
    log(f"   PDF의 도형 데이터에서 악보를 직접 읽었습니다: {total}마디, 파트 {len(parts)}개")
    if state['retimed']:
        log(f"   박이 맞지 않게 읽힌 성부 {state['retimed']}개(전체 {state['voices']}개)는 다른 파트의 음표 위치를 기준으로 보정했습니다.")
    return parts, first_page
