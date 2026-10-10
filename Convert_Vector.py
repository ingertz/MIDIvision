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
    white = False      # 지금 칠하는 색이 흰색인지 (숫자나 글자 뒤를 지우는 흰 배경은 기호가 아님)
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
            stack.append((list(ctm), white))
        elif op == b'Q':
            if stack:
                ctm, white = stack.pop()
        elif op == b'rg' and len(nums) >= 3:
            white = min(nums[-3:]) > 0.95
        elif op == b'g' and nums:
            white = nums[-1] > 0.95
        elif op == b'k' and len(nums) >= 4:
            white = max(nums[-4:]) < 0.05
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
            if white:
                subpaths, rects = [], []
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
    if n == 8 and subs == 1 and w > 2.5 and h < 3:
        return 'bracket2'                          # 양쪽에 갈고리가 달린 선 (8va 범위 등)
    if 3.0 < w < 3.6 and 3.8 < h < 4.4 and subs == 3:
        return 'segno'
    if 2.6 < w < 3.2 and 3.0 < h < 3.7 and subs == 5:
        return 'coda'
    if 2.8 < w < 3.4 and 1.4 < h < 1.9 and subs == 6:
        return 'ottava'                            # "8va" 글자
    if 1.2 < w < 1.8 and 4.6 < h < 6.0 and subs == 1 and 30 < n < 80:
        return 'paren'                             # 여러 음표를 묶는 큰 괄호
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
            if kind == 'slash':
                # 사선 머리는 길쭉해서 기둥이 위쪽 오른 끝이나 아래쪽 왼 끝 모서리에 닿음
                if stem[3] >= b[1] - 0.3 * u and stem[1] <= b[3] + 0.3 * u                         and min(abs(sx - b[2]), abs(sx - b[0])) < 0.4 * u:
                    chord.append(i)
                continue
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
# TAB 줄의 개방현 음 (맨 윗줄 = 1번 줄 = 가장 높은 줄). 숫자는 누르는 프렛, 0은 개방현
OPEN_STRINGS = {'guitar': [64, 59, 55, 50, 45, 40], 'bass': [43, 38, 33, 28]}
# 기타와 베이스는 실제 소리보다 한 옥타브 높게 적는 악기. MIDI와 같은 실제 음높이로 내보냄
WRITTEN_OCTAVE = {'guitar': -12, 'bass': -12}

class Box(tuple):
    """(x0, y0, x1, y1) 에 기호별 부가 정보를 덧붙인 것"""
    left_hook = False     # 갈고리 달린 선: 갈고리가 왼쪽 끝에 있는지 (= 여기서 시작하는 괄호)
    open_paren = False    # 큰 괄호: 여는 괄호인지
    sig = None            # 윤곽선의 생김새 (너비, 높이, 점 개수, 조각 수). TAB 숫자를 구분하는 데 씀

def tab_digits(tab, kinds, u):
    """TAB 줄 위의 숫자 모양들: [(가운데 x, 줄 순번, box)]"""
    out = []
    for kind, b in kinds:
        if b.sig is None or kind in ('x', 'paren', 'arc'):
            continue
        cx, cy = center(b)
        if not tab.top - 0.6 * tab.space <= cy <= tab.bottom + 0.6 * tab.space:
            continue
        if 1.2 * u < b[3] - b[1] < 1.95 * u and b[2] - b[0] < 1.5 * u:
            string = min(range(len(tab.lines)), key=lambda k: abs(tab.lines[k] - cy))
            out.append((cx, string, b))
    return sorted(out)

def tab_column(digits, x, table, u):
    """가로 위치 x 에 세로로 놓인 TAB 숫자들 -> {줄 순번: 프렛}. 모르는 모양이 섞인 줄은 뺌"""
    frets = {}
    near = [d for d in digits if abs(d[0] - x) < 1.3 * u]
    for string in sorted({d[1] for d in near}):
        row = sorted(d for d in near if d[1] == string)
        # 두 자리 프렛(10 이상)은 숫자 두 개가 붙어 있음. 음표 자리에 가장 가까운 숫자에서 시작해 이어 붙임
        start = min(range(len(row)), key=lambda i: abs(row[i][0] - x))
        if abs(row[start][0] - x) > 0.9 * u:
            continue
        group = [row[start]]
        for d in row[start + 1:]:
            if d[2][0] - group[-1][2][2] < 0.35 * u:
                group.append(d)
        for d in reversed(row[:start]):
            if group[0][2][0] - d[2][2] < 0.35 * u:
                group.insert(0, d)
        values = [table.get(d[2].sig) for d in group]
        if None not in values and len(values) <= 2:
            frets[string] = int(''.join(map(str, values)))
    return frets

def read_system(system, kinds, u, state, cs):
    """
    한 시스템(악보 한 줄)을 읽습니다.
    state: 보표별로 이어지는 정보 {(역할, 순번): {'clef', 'key'}}, 'nominal', TAB 숫자표 등
    반환: (마디 수, {(역할, 순번): [마디...]})   마디 = Convert_Score.empty_measure() 형식
    """
    roles = staff_roles(system)
    if not roles:
        return 0, {}
    last = roles[-1][0]
    bars = staff_barlines(last, kinds, u)
    edges = ([last.left] + bars) if bars and bars[0] - last.left > 8 * u else bars
    if len(edges) < 2:
        return 0, {}
    count = len(edges) - 1

    def measure_of(x):
        for j in range(count):
            if edges[j] - 0.2 * u <= x < edges[j + 1] - 0.2 * u:
                return j
        return None

    # --- 도돌이표: 보표 안에 세로로 나란한 점 두 개가 세로줄 옆에 있음 (맨 위 보표로 판단)
    marks = [{'forward': False, 'backward': 0, 'ending_open': False, 'segno': False, 'coda_sign': False} for _ in range(count)]
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
    for k, b in kinds:
        if not top.top - 16 * u < b[1] or not b[3] < top.top + 0.5 * u:
            continue
        j = measure_of(b[0] + u)
        if j is None:
            j = 0 if b[0] < edges[0] else count - 1
        if k == 'volta' and b.left_hook:
            marks[j]['ending_open'] = True          # 1. / 2. 괄호가 여기서 시작
        elif k == 'segno':
            marks[j]['segno'] = True
        elif k == 'coda':
            marks[j]['coda_sign'] = True

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
        shift = WRITTEN_OCTAVE.get(role, 0)
        tab = system[system.index(staff) + 1] if role in OPEN_STRINGS else None
        digits = tab_digits(tab, kinds, u) if tab is not None else []

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

        # 8va / 8vb: 표시 옆의 갈고리 선이 덮는 범위의 음을 한 옥타브 올림(보표 위) / 내림(보표 아래)
        octaves = []
        lines = [b for k, b in items if k in ('bracket', 'bracket2')]
        for k, b in items:
            if k != 'ottava':
                continue
            cx, cy = center(b)
            near = [ln for ln in lines if abs(center(ln)[1] - cy) < 1.8 * u and ln[0] - 1.5 * u <= cx <= ln[2] + 1.5 * u]
            lo, hi = (min(ln[0] for ln in near), max(ln[2] for ln in near)) if near else (b[0], b[2] + 6 * u)
            octaves.append((lo - 0.5 * u, hi + 0.5 * u, 12 if cy < staff.top + 2 * staff.space else -12))

        # "2×": 큰 괄호로 묶인 음표들은 두 번째 연주 때 치는 것. 같은 마디의 괄호 밖 음표는 첫 번째 때만 침
        in_chords = {hb for _, hb in heads}
        labels = [center(b) for k, b in items if k == 'x' and b not in in_chords]
        parens = sorted((b for k, b in items if k == 'paren' and staff.top - 3 * u < center(b)[1] < staff.bottom + 3 * u),
                        key=lambda b: b[0])
        for paren in parens:
            if not paren.open_paren or not any(-7 * u < paren[0] - lx < 2 * u for lx, _ in labels):
                continue
            close = next((p for p in parens if not p.open_paren and p[0] > paren[0]), None)
            for e in events:
                if paren[2] - 0.5 * u <= e['x'] <= (close[0] if close is not None else staff.right):
                    e['only'] = 2

        rings = [center(b) for k, b in items if k == 'ring']
        similes = [(k, center(b)[0]) for k, b in items if k in ('simile1', 'simile2') and staff.top - u < center(b)[1] < staff.bottom + u]
        measures_events = [[] for _ in range(count)]
        for e in events:
            j = measure_of(e['x'])
            if j is not None:
                measures_events[j].append(e)

        staff_measures = []
        for j in range(count):
            left, right = edges[j], edges[j + 1]
            inside = measures_events[j]
            # 줄 중간의 조바뀜: 마디 첫머리에 음표에 붙지 않은 임시표들이 모여 있음
            if j > 0 and role != 'drums':
                first = min((e['x'] for e in inside), default=right)
                change = [s for s in signs if left - 0.5 * u <= s[1][0] < min(first - 0.8 * u, left + 12 * u) and not attached(s)]
                if change:
                    info['key'] = key_of(change)
            alternatives = any(e.get('only') for e in inside)
            if alternatives:
                for e in inside:
                    e.setdefault('only', 1)
            groups = [[e for e in inside if e.get('only') != 2], [e for e in inside if e.get('only') == 2]] if alternatives else [inside]
            altered = {}
            voices = []
            for group in groups:
                for voice, total in voices_of(group, u, state['nominal']):
                    notes, xs, only = [], [], []
                    for e in voice:
                        # 같은 마디를 두 가지로 적은 곳은 가로 위치가 박과 맞지 않으므로 위치 기준 보정에서 뺌
                        x = None if alternatives else (e['x'] - left) / u * 10
                        made = []
                        if e['rest']:
                            made.append((e['at'], e['dur'], None, False, False, False))
                        elif all(kind == 'slash' for kind, _ in e['heads']):
                            # 기타의 리듬 표시(사선 머리): 이 리듬으로 코드를 친다는 뜻.
                            # 바로 아래 TAB에 숫자(줄 + 프렛)가 있으면 그 음, 없으면 직전에 잡은 코드를 그대로 침
                            frets = tab_column(digits, e['x'] + 0.6 * u, state['digit_table'], u) if state['digit_table'] else {}
                            chord = [OPEN_STRINGS[role][string] + fret for string, fret in sorted(frets.items(), reverse=True)]                                 or info.get('chord') or []
                            made += [(e['at'], e['dur'], midi, False, False, False) for midi in chord]
                            if chord:
                                info['chord'] = chord
                            else:
                                made.append((e['at'], e['dur'], None, False, False, False))   # 박을 맞추는 기준으로만
                        else:
                            for kind, hb in sorted(e['heads'], key=lambda h: h[1][0]):
                                if kind == 'slash':
                                    continue
                                pos = staff.position(center(hb)[1])
                                if role == 'drums':
                                    index = TOP_LINE['G'] - pos
                                    head = 'normal'
                                    if kind == 'x':
                                        cx, cy = center(hb)
                                        head = 'circle-x' if any(abs(rx - cx) < 0.9 * u and 0 < cy - ry < 5 * u for rx, ry in rings) else 'x'
                                    made.append((e['at'], e['dur'], cs.drum_note_from_position('CDEFGAB'[index % 7], index // 7, head),
                                                 True, False, False))
                                    continue
                                mine = [s for s in signs if 0 <= hb[0] - s[1][2] < 2.1 * u and sign_position(staff, *s) == pos]
                                if mine:
                                    altered[pos] = min(ACCIDENTALS[k] for k, _ in mine) if all(k != 'natural' for k, _ in mine) else 0
                                written = pitch(staff, hb, info['clef'], info['key'], altered)
                                sounding = written + shift + sum(o for lo, hi, o in octaves if lo <= hb[0] <= hi)
                                made.append((e['at'], e['dur'], max(0, min(127, sounding)), False, hb in tie_start, hb in tie_stop))
                                # TAB 숫자 익히기: 음표 하나에 숫자 하나가 대응하는 자리에서, 그 숫자 모양이 몇 프렛인지 알아냄
                                if tab is not None and len(e['heads']) == 1 and state['digit_table'] is None:
                                    cx = center(hb)[0]
                                    column = [d for d in digits if abs(d[0] - cx) < 1.5 * u]
                                    if len(column) == 1:
                                        fret = written + shift - OPEN_STRINGS[role][column[0][1]]
                                        if 0 <= fret <= 9:
                                            state['digit_votes'][column[0][2].sig][fret] += 1
                        if not e['rest'] and made and made[0][2] is not None and role != 'drums':
                            info['chord'] = [note[2] for note in made]
                        notes += made
                        xs += [x] * len(made)
                        only += [e.get('only')] * len(made)
                    voices.append({'notes': notes, 'xs': xs, 'only': only, 'extent': total, 'loose': None,
                                   'width': (right - left) / u * 10, 'rest_only': all(e['rest'] for e in voice)})
            simile = 0
            if not inside:
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
        state['voices'] += sum(1 for v in pseudo if not v['rest_only'])
        state['retimed'] += sum(1 for v in pseudo if v['extent'] != nominal and not v['rest_only'])
        cs.retime_measure(pseudo, nominal)
        for key_id, ms in per_staff:
            m = cs.empty_measure()
            for v in ms[j]['voices']:
                for note, x, when in zip(v['notes'], v['xs'], v['only']):
                    if note[2] is not None:
                        m['notes'].append(note)
                        m['xs'].append(x)
                        m['only'].append(when)
            m.update(extent=nominal, nominal=nominal, width=(edges[j + 1] - edges[j]) / u * 10,
                     simile=ms[j]['simile'], forward=marks[j]['forward'], backward=marks[j]['backward'],
                     ending_open=marks[j]['ending_open'], segno=marks[j]['segno'], coda_sign=marks[j]['coda_sign'])
            result[key_id].append(m)
    return count, result

# ---------------------------------------------------------------------------------------------
# 문서 전체
# ---------------------------------------------------------------------------------------------
def page_kinds(shapes, u):
    """쪽의 도형들을 [(종류, Box)] 로. 무슨 기호인지 모르는 윤곽선도 TAB 숫자일 수 있으므로 'glyph' 로 남김"""
    kinds = []
    for s in shapes:
        kind = classify(s, u)
        box = Box(s['box'])
        if not s['rect']:
            x0, y0, x1, y1 = s['box']
            points = [p for sp in s['subpaths'] for p in sp]
            box.sig = (round((x1 - x0) / u, 1), round((y1 - y0) / u, 1), len(points), len(s['subpaths']))
            if kind == 'volta':
                # 갈고리(아래로 꺾인 부분)가 왼쪽 끝에 있으면 여기서 시작하는 괄호
                low = [x for x, y in points if y > (y0 + y1) / 2]
                box.left_hook = bool(low) and sum(low) / len(low) < (x0 + x1) / 2
            elif kind == 'paren':
                # 여는 괄호 '(' 는 가운데가 왼쪽으로 불룩함
                cy, h = (y0 + y1) / 2, y1 - y0
                middle = [x for x, y in points if abs(y - cy) < h * 0.15]
                ends = [x for x, y in points if abs(y - cy) > h * 0.4]
                box.open_paren = bool(middle and ends) and sum(middle) / len(middle) < sum(ends) / len(ends)
        elif kind is None:
            continue
        kinds.append((kind or 'glyph', box))
    return kinds

def read_pdf(pdf_path, log=print):
    """
    PDF의 도형에서 악보를 읽어 Convert_Score.expand_score 가 받는 파트 목록으로 반환합니다.
    도형으로 된 악보가 아니면(스캔본 등) None
    반환: (파트 목록, 악보가 시작되는 쪽 번호)
    """
    import Convert_Score as cs

    try:
        contents = page_contents(pdf_path)
    except RuntimeError:
        raise                      # 비밀번호가 걸린 PDF 등, 사용자에게 알려야 하는 오류
    except Exception:
        return None                # PDF 구조를 읽지 못함 -> 그림 인식으로 넘김
    pages = []
    for number, (stream, height) in enumerate(contents, 1):
        shapes = parse_shapes(stream, height)
        u, systems = read_layout(shapes, PAGE_WIDTH)
        if systems:
            pages.append((number, u, systems, page_kinds(shapes, u)))
    if not pages:
        return None

    def read_all(digit_table, votes):
        state = {'nominal': Fraction(4), 'voices': 0, 'retimed': 0, 'digit_table': digit_table, 'digit_votes': votes}
        tracks, order, total = {}, [], 0
        for _, u, systems, kinds in pages:
            for system in systems:
                count, measures = read_system(system, kinds, u, state, cs)
                if not count:
                    continue
                for key_id in measures:
                    if key_id not in tracks:
                        tracks[key_id] = [cs.empty_measure() for _ in range(total)]
                        order.append(key_id)
                for key_id in order:
                    tracks[key_id] += measures.get(key_id) or [cs.empty_measure() for _ in range(count)]
                total += count
        return state, tracks, order, total

    # 1차: TAB 숫자 익히기. 베이스/기타 보표의 음표와 바로 아래 TAB 숫자를 짝지어, 숫자 모양마다 몇 프렛인지 알아냄
    # (글꼴이 달라도 그 악보 안에서 스스로 익히므로 숫자 모양을 미리 정해 둘 필요가 없음)
    votes = collections.defaultdict(collections.Counter)
    read_all(None, votes)
    table = {}
    for sig, counter in votes.items():
        fret, seen = counter.most_common(1)[0]
        if seen >= 2 and seen * 10 >= sum(counter.values()) * 7:
            table[sig] = fret
    # 2차: 실제 읽기
    state, tracks, order, total = read_all(table, votes)
    if not total or not any(m['notes'] for ms in tracks.values() for m in ms):
        return None

    # 코다 기호는 두 번 나옴: 처음 것이 "To Coda"(여기서 코다로 건너뜀), 두 번째가 코다의 시작
    signs = [i for i in range(total) if any(ms[i].get('coda_sign') for ms in tracks.values())]
    parts = []
    for key_id in order:
        measures = tracks[key_id]
        for i, m in enumerate(measures):
            m['tocoda'] = len(signs) >= 2 and i == signs[0]
            m['coda'] = len(signs) >= 2 and i == signs[1]
            # 마디 반복 기호: 앞 마디(들)의 내용을 그대로 가져옴
            k = m['simile']
            if k and i >= k and not m['notes']:
                for name in ('notes', 'xs', 'only'):
                    m[name] = list(measures[i - k][name])
        parts.append({'name': ROLE_NAMES[key_id[0]], 'program': None, 'role': key_id[0], 'measures': measures})
    log(f"   PDF의 도형 데이터에서 악보를 직접 읽었습니다: {total}마디, 파트 {len(parts)}개")
    if state['retimed']:
        log(f"   박이 맞지 않게 읽힌 성부 {state['retimed']}개(전체 {state['voices']}개)는 다른 파트의 음표 위치를 기준으로 보정했습니다.")
    if len(signs) >= 2:
        log("   D.S. al Coda: 코다 앞까지 연주한 뒤 세뇨로 돌아가 To Coda에서 코다로 건너뜁니다.")
    return parts, pages[0][0]
