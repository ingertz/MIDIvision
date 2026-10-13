import collections
import re
import zlib
from fractions import Fraction

import Convert_Glyphs

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
# MuseScore 처럼 기호를 윤곽선이 아니라 악보 글꼴의 글자로 찍고 오선·기둥을 선으로 긋는 PDF도 읽습니다.
# 글자를 기호로 바꾸는 것은 Convert_Glyphs 가 하고, 그 뒤(보표 찾기, 음표로 엮기)는 같은 코드를 씁니다.
#
# 길이 단위: 기호 크기는 오선 간격(u)의 배수로 비교해서 악보가 크게/작게 인쇄되어도 통하게 합니다.

# ---------------------------------------------------------------------------------------------
# PDF에서 채워진 도형 꺼내기
# ---------------------------------------------------------------------------------------------
TOKEN = re.compile(rb'[-+]?\d*\.?\d+|[A-Za-z*\'"]+|/[^\s/\[\]<>()]+|\[|\]|\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>')

def load_pages(pdf_path):
    """
    쪽마다 (그리기 명령, 쪽 높이, 글꼴 표). 글꼴 표는 Convert_Glyphs.font_tables 형식 (글자로 그린 악보용).
    pypdf가 없으면 단순한 구조의 PDF만 읽는 임시 방식으로 대신하고 글꼴 표는 비워 둠
    """
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
                pages.append((stream, 842.0, {}))
        return pages
    reader = pypdf.PdfReader(pdf_path)
    if reader.is_encrypted and not reader.decrypt(''):
        raise RuntimeError("비밀번호가 걸린 PDF입니다. 비밀번호를 푼 PDF로 다시 시도해 주세요.")
    pages = []
    cache = {}
    for page in reader.pages:
        contents = page.get_contents()
        try:
            fonts = Convert_Glyphs.font_tables(page, cache)
        except Exception:
            fonts = {}
        pages.append((contents.get_data() if contents is not None else b'', float(page.mediabox.height), fonts))
    return pages

def page_contents(pdf_path):
    """쪽마다 (그리기 명령, 쪽 높이)"""
    return [(stream, height) for stream, height, _ in load_pages(pdf_path)]

PAGE_WIDTH = 595.0   # 보표 줄을 고르는 기준 너비 (A4). 줄은 쪽 너비의 30% 이상인 가는 직사각형

def parse_shapes(stream, height, glyphs=None):
    """
    채워진 도형과 그은 선 목록. 좌표는 왼쪽 위가 원점(pt).
    {'rect': 직사각형인지, 'box': (x0, y0, x1, y1), 'subpaths': [[(x, y), ...]]}
    가로·세로로 곧게 그은 선은 굵기만큼의 직사각형으로 넣고(오선, 기둥, 세로줄을 선으로 긋는 PDF),
    두세 번 꺾인 선은 'stroke': True 인 도형으로 넣음 (잇단음표 괄호, 1번·2번 괄호)
    glyphs 에 목록을 주면 글자도 모음: (BT 블록 번호, 글꼴 이름, 문자열 토큰들, x, y, 글자 크기)
    """
    ctm = [1, 0, 0, 1, 0, 0]
    white = False      # 지금 칠하는 색이 흰색인지 (숫자나 글자 뒤를 지우는 흰 배경은 기호가 아님)
    white_line = False # 지금 긋는 선의 색이 흰색인지
    width = 1.0        # 선 굵기
    stack = []
    nums, names, strings = [], [], []
    shapes = []
    subpaths = []
    current = []
    rects = []
    tm = [1, 0, 0, 1, 0, 0]   # 글자 행렬
    font, font_size, block = None, 0.0, 0

    def tr(x, y):
        a, b, c, d, e, f = ctm
        return (a * x + c * y + e, height - (b * x + d * y + f))

    def scale(m):
        return abs(m[0] * m[3] - m[1] * m[2]) ** 0.5

    for token in TOKEN.findall(stream):
        first = token[:1]
        if first in b'0123456789.-+':
            nums.append(float(token))
            continue
        if first == b'/':
            names.append(token)
            continue
        if first in (b'(', b'<'):
            strings.append(token)
            continue
        if first in (b'[', b']'):
            continue
        op = token
        if op == b'cm' and len(nums) >= 6:
            a, b, c, d, e, f = nums[-6:]
            A, B, C, D, E, F = ctm
            ctm = [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]
        elif op == b'q':
            stack.append((list(ctm), white, white_line, width))
        elif op == b'Q':
            if stack:
                ctm, white, white_line, width = stack.pop()
        elif op in (b'rg', b'sc', b'scn') and len(nums) >= 3:
            white = min(nums[-3:]) > 0.95
        elif op in (b'g', b'sc', b'scn') and len(nums) == 1:
            white = nums[-1] > 0.95
        elif op == b'k' and len(nums) >= 4:
            white = max(nums[-4:]) < 0.05
        elif op in (b'RG', b'SC', b'SCN') and len(nums) >= 3:
            white_line = min(nums[-3:]) > 0.95
        elif op in (b'G', b'SC', b'SCN') and len(nums) == 1:
            white_line = nums[-1] > 0.95
        elif op == b'K' and len(nums) >= 4:
            white_line = max(nums[-4:]) < 0.05
        elif op == b'w' and nums:
            width = nums[-1]
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
        elif op in (b'S', b's'):
            if current:
                subpaths.append(current)
                current = []
            half = max(width * scale(ctm), 0.05) / 2
            for sp in ([] if white_line else subpaths):
                if len(sp) == 2:
                    (xa, ya), (xb, yb) = sp
                    if abs(ya - yb) < 0.05 < abs(xa - xb):       # 가로선
                        shapes.append({'rect': True, 'box': (min(xa, xb), ya - half, max(xa, xb), ya + half), 'subpaths': []})
                    elif abs(xa - xb) < 0.05 < abs(ya - yb):     # 세로선
                        shapes.append({'rect': True, 'box': (xa - half, min(ya, yb), xa + half, max(ya, yb)), 'subpaths': []})
                elif len(sp) in (3, 4):
                    xs, ys = [p[0] for p in sp], [p[1] for p in sp]
                    shapes.append({'rect': False, 'stroke': True, 'box': (min(xs), min(ys), max(xs), max(ys)), 'subpaths': [sp]})
            subpaths, rects = [], []
        elif op == b'n':
            subpaths, rects, current = [], [], []
        elif op == b'BT':
            tm = [1, 0, 0, 1, 0, 0]
            block += 1
        elif op == b'Tf' and names and nums:
            font, font_size = names[-1], nums[-1]
        elif op == b'Tm' and len(nums) >= 6:
            tm = nums[-6:]
        elif op in (b'Td', b'TD') and len(nums) >= 2:
            tx, ty = nums[-2:]
            tm = tm[:4] + [tx * tm[0] + ty * tm[2] + tm[4], tx * tm[1] + ty * tm[3] + tm[5]]
        elif op in (b'Tj', b'TJ', b"'", b'"'):
            if glyphs is not None and strings and font is not None:
                x, y = tr(tm[4], tm[5])
                glyphs.append((block, font, tuple(strings), x, y, abs(font_size) * scale(tm) * scale(ctm)))
        nums, names, strings = [], [], []
    return shapes

# ---------------------------------------------------------------------------------------------
# 기호 알아보기
# ---------------------------------------------------------------------------------------------
# 음표 머리. slash 는 기타의 리듬 표시(음높이 없는 사선 머리: 잡고 있는 코드를 그 리듬으로 침),
# mute 는 보표 높이만 한 큰 X (줄을 막고 긁는 소리)로, 박을 세는 데만 쓰고 음은 내지 않음
HEADS = ('black', 'half', 'whole', 'x', 'slash', 'mute')

def classify(shape, u, stretch=1.0):
    """
    도형 하나가 무슨 기호인지 (모르면 None). u = 오선 간격
    stretch: 악보 기호 글꼴의 가로 비율 (glyph_stretch). 직사각형(기둥, 빔)에는 적용하지 않음
    """
    x0, y0, x1, y1 = shape['box']
    w, h = (x1 - x0) / u, (y1 - y0) / u
    if not shape['rect']:
        w /= stretch
    if shape.get('stroke'):
        # 꺾인 선: 짧은 세로 갈고리 + 긴 가로선. (크레셴도 기호처럼 갈고리가 없는 것은 아님)
        points = shape['subpaths'][0]
        pieces = list(zip(points, points[1:]))
        hook = any(abs(a[0] - b[0]) < 0.25 * abs(a[1] - b[1]) and abs(a[1] - b[1]) > 0.4 * u for a, b in pieces)
        longest = max(pieces, key=lambda piece: abs(piece[0][0] - piece[1][0]))
        level = abs(longest[0][1] - longest[1][1]) < 0.3 * abs(longest[0][0] - longest[1][0])
        if not hook or not level:
            return None
        if w > 8 and 1.5 < h < 3.5:
            return 'volta'
        return 'bracket' if (w > 0.6 and h < 2.5) else None
    if shape['rect']:
        if w < 0.4 and h > 1.1:
            return 'vertical'                      # 기둥 / 세로줄
        if 0.45 < w < 0.95 and h > 3.0:
            return 'thick'                         # 굵은 세로줄 (도돌이표, 끝세로줄)
        if 0.35 < h < 0.68 and w > 0.8:
            return 'beam'                          # 수평 빔. 짧은 것은 온쉼표/2분쉼표일 수도 있음
        if h < 0.3 and 1.1 < w < 3.6:
            return 'ledger'                        # 덧줄
        if h < 0.3 and 3.6 <= w < 60:
            return 'line'                          # 가는 가로선 (○ 뒤에 붙어 "여기까지 열어 둠"을 나타내는 선 등)
        return None
    n = sum(len(sp) for sp in shape['subpaths'])
    subs = len(shape['subpaths'])
    if n == 5 and subs == 1 and shape['subpaths'][0][0] == shape['subpaths'][0][-1]:
        n = 4                                      # 사각형을 그리면서 첫 점으로 되돌아온 것
    if n == 6 and subs == 1 and w > 2.5:
        return 'volta' if (w > 15 and 2.0 < h < 3.4) else 'bracket'   # 갈고리 달린 선
    if n == 8 and subs == 1 and w > 2.5 and h < 3:
        return 'bracket2'                          # 양쪽에 갈고리가 달린 선 (8va 범위 등)
    if 3.0 < w < 3.6 and 3.8 < h < 4.4 and subs == 3:
        return 'segno'
    if 1.9 < w < 2.4 and 2.3 < h < 2.7 and subs == 3 and n > 150:
        return 'segno'                             # 작게 찍힌 세뇨
    if 2.0 < w < 3.2 and 2.8 < h < 3.7 and subs == 5 and n > 100:
        return 'coda'
    if 2.8 < w < 3.4 and 1.4 < h < 1.9 and subs == 6:
        return 'ottava'                            # "8va" 글자
    if 0.5 < w < 1.8 and 4.6 < h < 6.2 and subs == 1 and 25 < n < 80:
        return 'paren'                             # 여러 음표를 묶는 큰 괄호
    if n == 4 and subs == 1:
        if w > 2.3 and 0.4 < h < 2.6:
            return 'beam'                          # 기울어진 빔 (사각형을 점 네 개로 그린 수평 빔도 여기)
        if 0.7 < w <= 2.3 and 0.4 < h < 1.5:
            return 'beam'                          # 기울어진 짧은 빔 (16분음표 하나에만 걸린 토막)
        if 1.0 < w < 1.4 and 1.8 < h < 2.3:
            return 'slash'                         # 기타의 리듬 표시(음높이 없는 사선 머리)
        if 1.4 <= w < 2.2 and 2.6 < h < 3.4:
            return 'slash'                         # 가는 사선 하나로 적는 악보도 있음
        return None
    if 1.4 <= w < 2.2 and 2.6 < h < 3.4 and n == 12 and subs == 1:
        return 'mute'
    if 0.95 < w < 1.2 and 1.0 < h < 1.3 and n == 12 and subs == 1:
        return 'x'
    if 1.2 <= w < 1.6 and 1.25 < h < 1.6 and n == 12 and subs == 1:
        return 'times'                             # "2×" 의 곱셈표 (X 머리보다 큼)
    if 1.1 < w < 1.27 and 1.03 < h < 1.23 and n > 20:
        return 'black' if subs == 1 else 'half' if subs == 2 else None
    if 1.33 < w < 1.75 and 0.98 < h < 1.2 and subs == 2:
        return 'whole'
    if 0.68 < w < 0.86 and 2.15 < h < 2.6 and (n > 80 or (subs == 2 and n > 45)):
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
    # 윤곽선의 점 개수는 같은 기호라도 PDF를 만든 프로그램에 따라 다름 (4분쉼표 108~165개, 꼬리 44~91개)
    if 1.15 < w < 1.38 and 2.72 < h < 2.95 and subs == 1 and n > 80:
        return 'rest16'
    if 0.93 < w < 1.1 and 2.95 < h < 3.26 and subs == 1 and n > 100:
        return 'rest4'
    if 1.07 < w < 1.27 and 2.9 < h < 3.26 and subs == 1 and n < 100:
        return 'flag'
    if 1.07 < w < 1.27 and 3.05 < h < 3.45 and subs == 2:
        return 'flag2'
    if 0.77 < w < 1.1 and 1.3 < h < 1.75 and subs == 1 and n > 90:
        return 'tuplet3'
    if 0.77 < w < 1.15 and 1.3 < h < 1.75 and subs in (1, 2) and n > 50:
        return 'tupletN'                           # 잇단음표의 다른 숫자 (5, 6 ...). 몇 잇단인지는 묶인 음표 수로 정함
    if 2.2 < w < 2.55 and 6.4 < h < 7.1:
        return 'clefG'
    if 2.1 < w < 2.6 and 7.4 < h < 8.2 and subs >= 5:
        return 'clefG8'                            # 아래에 8이 붙은 높은음자리표: 적힌 것보다 한 옥타브 낮게 소리 남
    if 2.2 < w < 2.85 and 3.0 < h < 3.45 and subs == 3:
        return 'clefF'
    if 2.2 < w < 2.55 and 1.9 < h < 2.15 and subs == 3:
        return 'simile1'                           # % : 앞 마디 반복
    if 3.0 < w < 3.3 and 1.9 < h < 2.15 and subs == 4:
        return 'simile2'                           # 앞 두 마디 반복
    if 0.68 < w < 0.92 and 0.72 < h < 0.95 and subs == 2:
        return 'ring'                              # 음표 위의 작은 ○ (오픈 하이햇)
    if subs == 1 and 9 <= n <= 12 and w > 1.0 and h < max(0.8, 0.45 * w):
        return 'arc'                               # 붙임줄 / 이음줄
    return None

# 높은음자리표의 가로 폭 (오선 간격 단위). 기호 글꼴의 가로 비율을 재는 기준
CLEF_G_WIDTH = 2.36

def glyph_stretch(shapes, u):
    """
    악보 기호 글꼴의 가로 비율. 같은 출판사 악보라도 기호가 가로로 10~15% 넓게 찍힌 것이 있어서
    (음표 머리, 샤프가 기준보다 넓어서 하나도 못 알아봄) 높은음자리표 폭으로 재서 보정함. 못 재면 None
    """
    widths = sorted((s['box'][2] - s['box'][0]) / u for s in shapes
                    if not s['rect'] and len(s['subpaths']) >= 3
                    and 6.3 < (s['box'][3] - s['box'][1]) / u < 7.2 and 1.9 < (s['box'][2] - s['box'][0]) / u < 3.0)
    if not widths:
        return None
    return max(0.85, min(1.3, widths[len(widths) // 2] / CLEF_G_WIDTH))

class Staff:
    def __init__(self, lines, left, right):
        self.lines = lines                    # 줄의 가운데 y (위에서 아래로)
        self.left, self.right = left, right
        self.top, self.bottom = lines[0], lines[-1]
        self.space = (lines[-1] - lines[0]) / (len(lines) - 1)
        self.tab = len(lines) != 5
        self.limit_above = self.limit_below = None   # 위/아래 시스템과의 경계 (시스템의 맨 위/맨 아래 보표에만)

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
    # 오선을 마디마다 끊어서 그리는 PDF가 있으므로, 같은 높이에서 맞닿은 가는 가로선은 하나로 이음
    thin = sorted(((s['box'][1] + s['box'][3]) / 2, s['box'][0], s['box'][2], s['box'][1], s['box'][3]) for s in shapes
                  if s['rect'] and s['box'][2] - s['box'][0] > 15 and s['box'][3] - s['box'][1] < 1.2)
    joined = []
    for mid, x0, x1, y0, y1 in thin:
        last = joined[-1] if joined else None
        if last is not None and abs(mid - last[0]) < 0.15 and x0 <= last[2] + 0.3 and abs((y1 - y0) - (last[4] - last[3])) < 0.05:
            last[2] = max(last[2], x1)
        else:
            joined.append([mid, x0, x1, y0, y1])
    lines = sorted((y0, y1, x0, x1) for mid, x0, x1, y0, y1 in joined if x1 - x0 > page_width * 0.3)
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
    # 5현 베이스의 TAB은 5줄이라 오선과 줄 수가 같음. TAB은 줄 간격이 오선보다 넓은 것으로 구분
    for st in staves:
        if st.space > 1.15 * u:
            st.tab = True
    fives = [st.space for st in staves if not st.tab]
    u = sorted(fives)[len(fives) // 2]
    # 시스템: 보표 둘 이상을 잇는 세로선 하나가 한 시스템을 묶음. 그런 선이 없으면 보표 하나가 한 시스템 (파트보)
    talls = sorted((s['box'][1], s['box'][3]) for s in shapes
                   if s['rect'] and s['box'][2] - s['box'][0] < 0.4 * u and s['box'][3] - s['box'][1] > 9 * u)
    systems = []
    taken = set()
    for y0, y1 in sorted(talls, key=lambda t: t[0] - t[1]):      # 긴 선부터: 기타 보표와 TAB만 묶는 짧은 선은 시스템이 아님
        members = [st for st in staves if st.top >= y0 - u and st.bottom <= y1 + u]
        if len(members) >= 2 and not any(id(st) in taken for st in members):
            systems.append(members)
            taken |= {id(st) for st in members}
    systems.sort(key=lambda members: members[0].top)
    systems = systems or [[st] for st in staves]
    # 줄 간격이 좁은 악보는 위 시스템의 아래쪽 기호와 아래 시스템의 위쪽 기호가 가까움: 그 사이에서 나눔.
    # 보표 위쪽에 놓이는 기호(하이햇, 빔, ○ 표시)가 아래쪽 것보다 멀리까지 가므로 아래 시스템에 더 넓게 줌
    for upper, lower in zip(systems, systems[1:]):
        upper[-1].limit_below = lower[0].limit_above = upper[-1].bottom + 0.4 * (lower[0].top - upper[-1].bottom)
    return u, systems

def staff_roles(system):
    """
    시스템 안의 5선 보표마다 역할을 붙임: [(Staff, 역할)].
    밴드스코어 배치: 6줄 TAB 바로 위 = 기타, 4/5줄 TAB(4현/5현 베이스) 바로 위 = 베이스,
    TAB들보다 아래의 맨 끝 보표 = 드럼, 그 사이의 보표 = 보조 타악기(Perc.: 탬버린, 셰이커 등),
    맨 위 = 보컬, 나머지 = 건반. TAB이 없는 악보는 전부 건반
    """
    roles = []
    seen_tab = False
    last_staff = max((i for i, st in enumerate(system) if not st.tab), default=-1)
    for i, staff in enumerate(system):
        if staff.tab:
            seen_tab = True
            continue
        below = system[i + 1] if i + 1 < len(system) else None
        if below is not None and below.tab:
            roles.append((staff, 'guitar' if len(below.lines) >= 6 else 'bass'))
        elif seen_tab and not any(st.tab for st in system[i + 1:]):
            roles.append((staff, 'drums' if i == last_staff else 'perc'))
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
    보표마다 그 보표의 기호로 볼 세로 구역 {Staff: (위, 아래, 넘지 못하는 위 경계, 넘지 못하는 아래 경계)}.
    보표 사이의 덧줄 음표가 위아래 보표에 겹쳐 잡히지 않도록, 이웃한 보표(TAB 포함)와의 한가운데에서 나눔
    """
    zones = {}
    for i, staff in enumerate(system):
        above = (system[i - 1].bottom + staff.top) / 2 if i else staff.top - 7.5 * u
        below = (staff.bottom + system[i + 1].top) / 2 if i + 1 < len(system) else staff.bottom + 5.5 * u
        above, below = max(above, staff.top - 9 * u), min(below, staff.bottom + 9 * u)
        wall_above = staff.limit_above if staff.limit_above is not None else float('-inf')
        wall_below = staff.limit_below if staff.limit_below is not None else float('inf')
        zones[staff] = (max(above, wall_above), min(below, wall_below), wall_above, wall_below)
    return zones

def staff_items(zone, kinds, u):
    """
    보표 구역 안의 기호 [(종류, box)]. kinds = 쪽 전체의 [(종류, box)]
    보표 사이가 좁으면 기둥과 빔이 구역 밖으로 나가므로(드럼의 하이햇 등), 구역에 걸친 기둥과
    그 기둥에 닿은 빔·꼬리, 구역 바로 바깥의 잇단음표 숫자와 "2×" 표시도 함께 넣음
    """
    def height(b):
        return b.anchor if b.anchor is not None else (b[1] + b[3]) / 2

    def inside(b):
        return zone[0] <= height(b) < zone[1]

    def nearby(b, reach):
        # 구역 바로 바깥. 단, 이웃 시스템과의 경계는 넘지 않음
        return max(zone[0] - reach, zone[2]) <= height(b) < min(zone[1] + reach, zone[3]) and not inside(b)

    items = [(k, b) for k, b in kinds if inside(b)]
    stems = [b for k, b in kinds if k == 'vertical' and b[3] - b[1] < 11 * u and b[3] > zone[0] and b[1] < zone[1]]
    items += [('vertical', b) for b in stems if not inside(b)]
    items += [(k, b) for k, b in kinds if (k in FLAGS or k == 'beam') and nearby(b, 8 * u)
              and any(b[0] - 0.5 * u <= (s[0] + s[2]) / 2 <= b[2] + 0.5 * u and b[1] <= s[3] + 0.6 * u and b[3] >= s[1] - 0.6 * u
                      for s in stems)]
    items += [(k, b) for k, b in kinds if k in ('tuplet3', 'tupletN', 'times') and nearby(b, 3.5 * u)]
    return items

def staff_barlines(staff, kinds, u):
    """보표를 위에서 아래까지 가로지르는 세로줄의 x (마디 경계)"""
    xs = sorted(b[0] for k, b in kinds if k in ('vertical', 'thick')
                and b[1] <= staff.top + 0.25 * u and b[3] >= staff.bottom - 0.25 * u and b[3] - b[1] < 20 * u)
    out = []
    for x in xs:
        if not out or x - out[-1] > 1.6 * u:
            out.append(x)
    return out

FLAGS = {'flag': 1, 'flag2': 2, 'flag3': 3}
RESTS = {'rest32': Fraction(1, 8), 'rest16': Fraction(1, 4), 'rest8': Fraction(1, 2), 'rest4': Fraction(1),
         'rest2': Fraction(2), 'rest1': Fraction(4)}
# 잇단음표 숫자를 아는 경우의 길이 비율 (n개를 몇 개 분량에 넣는지)
TUPLETS = {2: Fraction(3, 2), 3: Fraction(2, 3), 4: Fraction(3, 4), 5: Fraction(4, 5), 6: Fraction(2, 3), 7: Fraction(4, 7), 9: Fraction(8, 9)}

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
    flags = [(FLAGS[k], b) for k, b in items if k in FLAGS]
    dots = [b for k, b in items if k == 'dot']
    hlines = list(staff.lines) + [center(b)[1] for k, b in items if k == 'ledger']

    def dotted(box):
        """점음표/점쉼표의 길이 배율: 점이 없으면 1, 하나면 3/2, 둘이면 7/4, 셋이면 15/8"""
        cy = center(box)[1]
        near = sorted((d for d in dots if 0.1 * u < d[0] - box[2] < 1.5 * u and abs(center(d)[1] - cy) <= 0.75 * u),
                      key=lambda d: d[0])
        if not near:
            return Fraction(1)
        count, last = 1, near[0]
        for d in sorted(dots, key=lambda d: d[0]):
            if 0 < d[0] - last[2] < 0.9 * u and abs(center(d)[1] - center(last)[1]) < 0.25 * u:
                count, last = count + 1, d
        return 2 - Fraction(1, 2 ** min(count, 3))

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
            if kind == 'mute':
                # 큰 X 는 기둥이 한가운데를 지남
                if stem[3] >= b[1] - 0.3 * u and stem[1] <= b[3] + 0.3 * u and abs(sx - cx) < 0.4 * u:
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
        # 음표가 촘촘하면 기둥이 옆 음표 머리의 끝에도 닿음. 위로 뻗은 기둥의 머리는 기둥 왼쪽에, 아래로 뻗은
        # 기둥의 머리는 오른쪽에 있으므로, 같은 높이에 제자리 머리가 있으면 반대쪽 것은 옆 음표의 머리
        def proper(i):
            cx = center(heads[i][1])[0]
            return cx < sx + 0.2 * u if up else cx > sx - 0.2 * u
        chord = [i for i in chord if proper(i) or not any(
            proper(j) and abs(center(heads[i][1])[1] - center(heads[j][1])[1]) < 0.3 * u for j in chord)]
        ys = [center(heads[i][1])[1] for i in chord]
        count = sum(1 for b in beams if b[0] - 0.15 * u <= sx <= b[2] + 0.15 * u
                    and b[1] <= stem[3] + 0.4 * u and b[3] >= stem[1] - 0.4 * u)
        tip = stem[1] if up else stem[3]
        count += sum(n for n, f in flags if abs(f[0] - stem[0]) < 0.46 * u and f[1] - 0.6 * u <= tip <= f[3] + 0.6 * u)
        dur = Fraction(2) if any(heads[i][0] == 'half' for i in chord) else Fraction(1) / (2 ** count)
        dur *= max(dotted(heads[i][1]) for i in chord)
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
        dur = (Fraction(4) if kind == 'whole' else Fraction(1)) * dotted(b)
        events.append({'x': b[0], 'sx': center(b)[0], 'voice': 'up', 'dur': dur, 'heads': [(kind, b)], 'rest': False,
                       'stemless': True})

    middle = staff.top + 2 * staff.space
    for kind, b in items:
        cx, cy = center(b)
        if kind in RESTS:
            # 보표에서 멀리 떨어진 것은 쉼표가 아니라 가사 글자 ('く' 가 8분쉼표와 닮음)
            if not staff.top - 3 * u <= cy <= staff.bottom + 3 * u:
                continue
            dur = RESTS[kind]
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
        dur *= dotted(b)
        events.append({'x': b[0], 'sx': cx, 'voice': 'down' if cy > middle + 0.2 * u else 'up',
                       'dur': dur, 'heads': [], 'rest': True})

    # 잇단음표: 작은 숫자의 양옆 괄호(없으면 그 위아래의 빔)가 덮는 범위의 음표/쉼표.
    # 셋잇단은 길이의 2/3. 다른 숫자(5, 6, 7)는 묶인 음표 수로 알아냄: 가장 짧은 음표 n개 분량이 4개 분량으로 줄어듦
    brackets = [b for k, b in items if k == 'bracket']
    for kind, b in items:
        if kind not in ('tuplet3', 'tupletN'):
            continue
        cx, cy = center(b)
        near = [br for br in brackets if abs(center(br)[1] - cy) < 1.5 * u and (0 <= b[0] - br[2] < 1.2 * u or 0 <= br[0] - b[2] < 1.2 * u)]
        if near:
            spans = [(min(br[0] for br in near), max(br[2] for br in near))]
        else:
            over = [bm for bm in beams if bm[0] - 0.4 * u <= cx <= bm[2] + 0.4 * u and abs(center(bm)[1] - cy) < 3.5 * u]
            if not over:
                continue
            # 빔이 여러 겹이면 짧은 빔이 잇단음표만 묶은 것일 수 있음 (8분음표 + 16분 셋잇단). 짧은 것부터 맞춰 봄
            spans = sorted({(bm[0], bm[2]) for bm in over}, key=lambda sp: sp[1] - sp[0])
            spans.append((min(bm[0] for bm in over), max(bm[2] for bm in over)))
        voice = 'up' if cy < middle else 'down'
        known = TUPLETS.get(b.value)               # 글자로 그린 악보는 숫자를 앎
        wanted = (b.value, 2 * b.value) if known else (3, 6, 12) if kind == 'tuplet3' else (5, 6, 7)
        chosen = None
        for lo, hi in spans:
            covered = [e for e in events if lo - 0.6 * u <= e['sx'] <= hi + 0.6 * u and e['voice'] == voice and not e.get('tuplet')]
            units = sum(e['dur'] for e in covered) / min(e['dur'] for e in covered) if covered else 0
            if units in wanted:
                chosen = (covered, known or (Fraction(2, 3) if kind == 'tuplet3' else Fraction(4) / units))
                break
        if chosen is None:
            if kind != 'tuplet3' and not known:
                continue
            chosen = (covered, known or Fraction(2, 3))   # 길이가 섞인 잇단음표 (4분 + 8분 등): 가장 넓은 범위
        for e in chosen[0]:
            e['dur'] *= chosen[1]
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
ROLE_NAMES = {'vocal': 'Vocal', 'keyboard': '', 'guitar': 'Guitar', 'bass': 'Bass', 'drums': 'Drums', 'perc': 'Percussion'}
# 음높이 대신 줄 위치로 악기를 적는 보표. perc(탬버린, 셰이커 등 보조 타악기)는 박을 맞추는 데만 쓰고 내보내지 않음
DRUM_ROLES = ('drums', 'perc')
# TAB 줄의 개방현 음 (맨 윗줄 = 1번 줄 = 가장 높은 줄). 숫자는 누르는 프렛, 0은 개방현
# TAB 줄 수로 현 수를 알 수 있음: 5현 베이스는 맨 아래에 Low B, 7현 기타는 맨 아래에 Low B
OPEN_STRINGS = {'guitar': [64, 59, 55, 50, 45, 40, 35], 'bass': [43, 38, 33, 28, 23]}

def open_strings(role, tab):
    """TAB 보표의 줄마다 개방현 음. 6현 베이스는 맨 위에 High C가 더 있음"""
    count = len(tab.lines)
    if role == 'bass' and count >= 6:
        return [48] + OPEN_STRINGS['bass']
    return OPEN_STRINGS[role][:count]
# 기타와 베이스는 실제 소리보다 한 옥타브 높게 적는 악기. MIDI와 같은 실제 음높이로 내보냄
WRITTEN_OCTAVE = {'guitar': -12, 'bass': -12}

class Box(tuple):
    """(x0, y0, x1, y1) 에 기호별 부가 정보를 덧붙인 것"""
    left_hook = False     # 갈고리 달린 선: 갈고리가 왼쪽 끝에 있는지 (= 여기서 시작하는 괄호)
    open_paren = False    # 큰 괄호: 여는 괄호인지
    sig = None            # 윤곽선의 생김새 (너비, 높이, 점 개수, 조각 수). TAB 숫자를 구분하는 데 씀
    anchor = None         # 잇단음표 숫자/괄호: 가장 가까운 빔이나 기둥의 높이 (어느 보표의 것인지 정하는 기준)
    head = None           # 글자로 그린 악보: 음표 머리의 모양 ('x', 'circle-x', 'diamond', 'triangle')
    value = None          # 글자로 그린 악보: 박자표·잇단음표 숫자의 값

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
    # 타악기 음자리표가 있는 보표는 드럼 (TAB이 없어서 자리로는 알 수 없는 악보). 한 번 알아낸 것은 계속 유지
    percussion = state.setdefault('percussion', set())
    for i, (staff, role) in enumerate(roles):
        if any(k == 'clefP' and staff.top - u < center(b)[1] < staff.bottom + u and b[0] < staff.left + 8 * u for k, b in kinds):
            percussion.add((len(roles), i))
        if (len(roles), i) in percussion and role in ('keyboard', 'vocal'):
            roles[i] = (staff, 'drums')
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

    # --- 박자표 (글자로 그린 악보): 마디 첫머리에 위아래로 놓인 숫자, 또는 C
    declared = {}
    signs = [b for k, b in kinds if k == 'timesig' and top.top - u < center(b)[1] < top.bottom + u]
    for j in range(count):
        here = sorted((b for b in signs if edges[j] - 0.5 * u <= b[0] < edges[j + 1] - 0.5 * u), key=lambda b: b[0])
        if not here:
            continue
        middle = (top.top + top.bottom) / 2
        upper = [b.value for b in here if center(b)[1] < middle - 0.3 * u and isinstance(b.value, int)]
        lower = [b.value for b in here if center(b)[1] > middle + 0.3 * u and isinstance(b.value, int)]
        if upper and lower and int(''.join(map(str, lower))):
            declared[j] = Fraction(int(''.join(map(str, upper))) * 4, int(''.join(map(str, lower))))
        elif any(b.value in ('C', 'c') for b in here):
            declared[j] = Fraction(4)

    # --- 보표별 읽기
    per_staff = []
    counters = collections.Counter()
    zones = staff_zones(system, u)
    for staff, role in roles:
        key_id = (role, counters[role])
        counters[role] += 1
        info = state.setdefault(key_id, {'clef': 'F' if role == 'bass' else 'G', 'key': 0})
        # 보표 왼쪽 바깥은 세로로 쓴 파트 이름: 글자 'e', 'o' 가 온음표와 똑같이 생겨서 음표로 잡히므로 제외
        items = [(k, b) for k, b in staff_items(zones[staff], kinds, u) if b[0] > staff.left - 0.5 * u]
        events = read_events(staff, items, bars, u)
        shift = WRITTEN_OCTAVE.get(role, 0)
        tab = system[system.index(staff) + 1] if role in OPEN_STRINGS else None
        strings = open_strings(role, tab) if tab is not None else []
        digits = tab_digits(tab, kinds, u) if tab is not None else []

        # 음자리표와 조표 (보표 첫머리)
        first_x = min((e['x'] for e in events), default=staff.right)
        for k, b in items:
            if k in ('clefG', 'clefG8', 'clefF') and b[0] < staff.left + 6 * u:
                info['clef'] = k[4]
                info['octave'] = -12 if k == 'clefG8' else 0
        signs = [(k, b) for k, b in items if k in ACCIDENTALS and staff.top - 2 * u < center(b)[1] < staff.bottom + 2 * u]
        heads = [h for e in events for h in e['heads']]

        def attached(sign):
            kind, b = sign
            pos = sign_position(staff, kind, b)
            return any(0 <= hb[0] - b[2] < 2.1 * u and staff.position(center(hb)[1]) == pos for _, hb in heads)

        header = [s for s in signs if s[1][2] < first_x - 0.8 * u and s[1][0] < edges[0] + 14 * u and not attached(s)]
        if role not in DRUM_ROLES:
            info['key'] = key_of(header)
            info.setdefault('first_key', info['key'])

        # 붙임줄: 같은 높이의 두 음표 머리를 잇는 호. 줄 끝/줄 처음에서 끊긴 것은 다음/이전 시스템과 이어짐
        # 드럼은 같은 줄 안에서 심벌 두 개를 이은 것만 봄 (뒤의 것은 다시 치지 않음)
        tie_start, tie_stop = set(), set()
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
            elif role in DRUM_ROLES:
                continue
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
        labels = [center(b) for k, b in items if k == 'times' or (k == 'x' and b not in in_chords)]
        parens = sorted((b for k, b in items if k == 'paren' and staff.top - 3 * u < center(b)[1] < staff.bottom + 3 * u),
                        key=lambda b: b[0])
        for paren in parens:
            if not paren.open_paren or not any(-7 * u < paren[0] - lx < 2 * u for lx, _ in labels):
                continue
            close = next((p for p in parens if not p.open_paren and p[0] > paren[0]), None)
            for e in events:
                if paren[2] - 0.5 * u <= e['x'] <= (close[0] if close is not None else staff.right):
                    e['only'] = 2

        # 음표 위의 ○ (오픈 하이햇): (왼쪽 끝, 오른쪽 끝, 높이). ○ 뒤에 가로선이 이어지면 선이 끝나는 데까지 열어 둠
        rings = []
        for k, b in items:
            if k != 'ring':
                continue
            tails = [ln[2] for kk, ln in items if kk in ('line', 'ledger')
                     and -0.3 * u < ln[0] - b[2] < 4 * u and abs(center(ln)[1] - center(b)[1]) < 0.8 * u]
            rings.append((b[0] - 0.4 * u, max(tails + [b[2] + 0.4 * u]), center(b)[1]))
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
            if j > 0 and role not in DRUM_ROLES:
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
                        if e['rest'] or all(kind == 'mute' for kind, _ in e['heads']):
                            made.append((e['at'], e['dur'], None, False, False, False))
                        elif all(kind == 'slash' for kind, _ in e['heads']):
                            # 기타의 리듬 표시(사선 머리): 이 리듬으로 코드를 친다는 뜻.
                            # 바로 아래 TAB에 숫자(줄 + 프렛)가 있으면 그 음, 없으면 직전에 잡은 코드를 그대로 침
                            frets = tab_column(digits, e['x'] + 0.6 * u, state['digit_table'], u) if state['digit_table'] else {}
                            chord = [strings[string] + fret for string, fret in sorted(frets.items(), reverse=True) if string < len(strings)]                                 or info.get('chord') or []
                            made += [(e['at'], e['dur'], midi, False, False, False) for midi in chord]
                            if chord:
                                info['chord'] = chord
                            else:
                                made.append((e['at'], e['dur'], None, False, False, False))   # 박을 맞추는 기준으로만
                        else:
                            for kind, hb in sorted(e['heads'], key=lambda h: h[1][0]):
                                if kind in ('slash', 'mute'):
                                    continue
                                pos = staff.position(center(hb)[1])
                                if role in DRUM_ROLES:
                                    if hb in tie_stop:
                                        continue
                                    index = TOP_LINE['G'] - pos
                                    head = 'normal'
                                    if kind == 'x':
                                        cx, cy = center(hb)
                                        head = 'circle-x' if any(lo <= cx <= hi and 0 < cy - ry < 5 * u for lo, hi, ry in rings) else 'x'
                                    head = hb.head or head

                                    def usual(index=index, head=head):
                                        return cs.drum_note_from_position('CDEFGAB'[index % 7], index // 7, head)
                                    drum = state['drum_note'](pos, head, usual) if state.get('drum_note') else usual()
                                    made.append((e['at'], e['dur'], drum, True, False, False))
                                    continue
                                mine = [s for s in signs if 0 <= hb[0] - s[1][2] < 2.1 * u and sign_position(staff, *s) == pos]
                                if mine:
                                    altered[pos] = min(ACCIDENTALS[k] for k, _ in mine) if all(k != 'natural' for k, _ in mine) else 0
                                written = pitch(staff, hb, info['clef'], info['key'], altered)
                                sounding = written + shift + info.get('octave', 0) + sum(o for lo, hi, o in octaves if lo <= hb[0] <= hi)
                                made.append((e['at'], e['dur'], max(0, min(127, sounding)), False, hb in tie_start, hb in tie_stop))
                                # TAB 숫자 익히기: 음표 하나에 숫자 하나가 대응하는 자리에서, 그 숫자 모양이 몇 프렛인지 알아냄
                                if tab is not None and len(e['heads']) == 1 and state['digit_table'] is None:
                                    cx = center(hb)[0]
                                    column = [d for d in digits if abs(d[0] - cx) < 1.5 * u]
                                    if len(column) == 1:
                                        fret = written + shift - strings[column[0][1]]
                                        if 0 <= fret <= 9:
                                            state['digit_votes'][column[0][2].sig][fret] += 1
                        if not made:
                            made.append((e['at'], e['dur'], None, False, False, False))   # 붙임줄로 이어진 음: 박만 차지함
                        if not e['rest'] and made and made[0][2] is not None and role not in DRUM_ROLES:
                            info['chord'] = [note[2] for note in made]
                        notes += made
                        xs += [x] * len(made)
                        only += [e.get('only')] * len(made)
                    voices.append({'notes': notes, 'xs': xs, 'only': only, 'extent': total, 'loose': None, 'part': key_id,
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
        if j in declared:
            state['time'] = declared[j]
        if state.get('time'):
            nominal = state['time']            # 박자표를 읽은 악보는 그대로 따름
        elif totals:
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
def page_kinds(shapes, u, stretch=1.0):
    """쪽의 도형들을 [(종류, Box)] 로. 무슨 기호인지 모르는 윤곽선도 TAB 숫자일 수 있으므로 'glyph' 로 남김"""
    kinds = []
    for s in shapes:
        kind = classify(s, u, stretch)
        box = Box(s['box'])
        if not s['rect']:
            x0, y0, x1, y1 = s['box']
            points = [p for sp in s['subpaths'] for p in sp]
            box.sig = (round((x1 - x0) / u, 1), round((y1 - y0) / u, 1), len(points), len(s['subpaths']))
            if kind == 'volta':
                # 갈고리(아래로 꺾인 부분)가 왼쪽 끝에 있으면 여기서 시작하는 괄호
                low = [x for x, y in points if y > (y0 + y1) / 2]
                box.left_hook = bool(low) and sum(low) / len(low) < (x0 + x1) / 2
                if s.get('stroke'):
                    box.left_hook = bool(low) and min(low) < x0 + 0.1 * (x1 - x0)
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

def anchor_marks(kinds, u):
    """
    잇단음표 숫자와 괄호는 보표에서 멀리 떨어져 있어서, 줄 간격이 좁은 악보에서는 위아래 어느 보표의 것인지
    자리만으로는 알 수 없음. 그 기호에 가장 가까운 빔이나 기둥의 높이를 적어 두고 그것으로 판단함
    """
    ink = [b for k, b in kinds if k == 'beam' or (k == 'vertical' and b[3] - b[1] < 11 * u)]
    for kind, b in kinds:
        if kind not in ('tuplet3', 'tupletN', 'bracket'):
            continue
        cx, cy = center(b)
        best = None
        for other in ink:
            if not other[0] - u <= cx <= other[2] + u:
                continue
            nearest = min(max(cy, other[1]), other[3])       # 그 빔/기둥에서 이 기호에 가장 가까운 높이
            if abs(nearest - cy) < 6 * u and (best is None or abs(nearest - cy) < abs(best - cy)):
                best = nearest
        b.anchor = best

def read_tempo(system, kinds, u, digit_table):
    """
    첫 시스템 위의 메트로놈 표시(♩=150)를 읽습니다. 못 읽으면 None.
    숫자는 TAB 숫자와 같은 글꼴이므로 TAB에서 익힌 숫자표(digit_table)로 읽고,
    숫자 왼쪽의 음표 기호(기둥 달린 음표 하나)로 박 단위를 정함: 속이 찬 머리 = 4분음표, 빈 머리 = 2분음표
    """
    if not digit_table:
        return None
    top = system[0].top
    glyphs = [b for k, b in kinds if b.sig is not None and top - 14 * u < b[1] and b[3] < top - 0.3 * u]
    digits = sorted((b for b in glyphs if b.sig in digit_table), key=lambda b: b[0])
    groups = []
    for b in digits:
        last = groups[-1][-1] if groups else None
        if last is not None and 0 <= b[0] - last[2] < 0.7 * u and abs(b[3] - last[3]) < 0.4 * u:
            groups[-1].append(b)
        else:
            groups.append([b])
    for group in groups:
        if not 2 <= len(group) <= 3:
            continue
        x0, y0, y1 = group[0][0], min(b[1] for b in group), max(b[3] for b in group)
        # 숫자 바로 왼쪽(= 기호 너머)의 음표: 숫자보다 키가 큰 기둥 달린 음표 글자
        notes = [b for b in glyphs if 0 < x0 - b[2] < 6 * u and 2.6 * u < b[3] - b[1] < 4.5 * u
                 and 0.8 * u < b[2] - b[0] < 2.0 * u and b[1] < y1 and b[3] > y0]
        if not notes:
            continue
        note = max(notes, key=lambda b: b[2])
        bpm = int(''.join(str(digit_table[b.sig]) for b in group))
        bpm *= 2 if note.sig[3] == 2 else 1
        if 30 <= bpm <= 400:
            return bpm
    return None

# 보표 위 둘째 칸(덧줄 위)의 X: 보통은 크래시지만, 박을 계속 새기는 데 쓰면 라이드
HIGH_CYMBAL, RIDE = 57, 51

def ridden_cymbal(measures):
    """
    드럼 악보는 책마다 심벌 자리가 다름. 크래시 자리의 심벌을 한 마디에 네 번 이상 치는 마디가 많으면
    그 심벌로 박을 새기는 것(재즈풍 곡의 라이드)이므로, 이어서 치는 것들을 라이드로 바꿈.
    홀로 떨어진 한 방은 크래시로 둠. 바꿨으면 True
    """
    def hits(m):
        return sorted(n[0] for n in m['notes'] if n[2] == HIGH_CYMBAL and n[3])
    busy = sum(1 for m in measures if len(hits(m)) >= 4)
    played = sum(1 for m in measures if m['notes'])
    if busy < 8 or busy * 6 < played:
        return False
    for m in measures:
        times = hits(m)
        run = {t for t in times if any(0 < abs(t - other) <= 1 for other in times)}
        m['notes'] = [(n[0], n[1], RIDE, *n[3:]) if n[2] == HIGH_CYMBAL and n[3] and n[0] in run else n for n in m['notes']]
    return True

def page_ranges(numbers):
    """[19, 20, 21, 25] -> '19~21, 25'"""
    out = []
    for n in sorted(numbers):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ', '.join(str(a) if a == b else f'{a}~{b}' for a, b in out)

def score_pages(pdf_path):
    """
    악보가 있는 쪽들을 읽을 준비가 된 형태로: {'pages': [(쪽 번호, 오선 간격, [시스템], [(종류, Box)])],
    'skipped': 읽지 않은 쪽 번호들, 'lettered': 글자로 찍힌 기호 수, 'texts': 첫 쪽의 글, 'music': 첫 쪽의 악보 글자}
    악보가 없으면 None
    """
    layouts = []
    lettered = 0                   # 악보 글꼴의 글자로 찍힌 기호 수 (MuseScore 등에서 내보낸 PDF)
    for number, (stream, height, fonts) in enumerate(load_pages(pdf_path), 1):
        records = []
        shapes = parse_shapes(stream, height, records if fonts else None)
        u, systems = read_layout(shapes, PAGE_WIDTH)
        if systems:
            letters = Convert_Glyphs.read_glyphs(records, fonts)
            lettered += len(letters[0])
            layouts.append((number, u, systems, shapes, glyph_stretch(shapes, u), letters))
    if not layouts:
        return None
    # 밴드 스코어 뒤에 피아노 독주 편곡 같은 다른 악보가 붙어 있는 책이 있음: TAB이 있는 악보라면 TAB 없는 시스템은 읽지 않음
    skipped = []
    if any(st.tab for _, _, systems, *_ in layouts for system in systems for st in system):
        kept = []
        for number, u, systems, *rest in layouts:
            band = [system for system in systems if any(st.tab for st in system)]
            if band:
                kept.append((number, u, band, *rest))
            else:
                skipped.append(number)
        layouts = kept
    # 기호 글꼴의 가로 비율: 높은음자리표가 없는 쪽은 다른 쪽에서 잰 값을 씀
    measured = sorted(s for *_, s, _ in layouts if s is not None)
    default_stretch = measured[len(measured) // 2] if measured else 1.0
    pages = []
    for number, u, systems, shapes, stretch, (marks, texts, music) in layouts:
        kinds = page_kinds(shapes, u, stretch or default_stretch)
        for kind, box, extra in marks + Convert_Glyphs.tuplet_marks(texts, u):
            box = Box(box)
            for name, value in extra.items():
                setattr(box, name, value)
            kinds.append((kind, box))
        anchor_marks(kinds, u)
        pages.append((number, u, systems, kinds))
    return {'pages': pages, 'skipped': skipped, 'lettered': lettered, 'texts': layouts[0][-1][1], 'music': layouts[0][-1][2]}

def new_state(score, digit_table, votes):
    """read_system 이 시스템에서 시스템으로 넘겨받는 정보의 처음 상태"""
    return {'nominal': Fraction(4), 'voices': 0, 'retimed': 0, 'digit_table': digit_table, 'digit_votes': votes,
            'drum_note': Convert_Glyphs.drum_note if score['lettered'] else None}

def read_pdf(pdf_path, log=print):
    """
    PDF의 도형에서 악보를 읽어 Convert_Score.expand_score 가 받는 파트 목록으로 반환합니다.
    도형으로 된 악보가 아니면(스캔본 등) None
    반환: (파트 목록, 악보가 시작되는 쪽 번호)
    """
    import Convert_Score as cs

    try:
        score = score_pages(pdf_path)
    except RuntimeError:
        raise                      # 비밀번호가 걸린 PDF 등, 사용자에게 알려야 하는 오류
    except Exception:
        return None                # PDF 구조를 읽지 못함 -> 그림 인식으로 넘김
    if score is None:
        return None
    pages, skipped, lettered = score['pages'], score['skipped'], score['lettered']

    def read_all(digit_table, votes):
        state = new_state(score, digit_table, votes)
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
        if key_id[0] == 'perc':
            continue
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
    if not parts:
        return None
    if lettered:
        log(f"   PDF에 글자로 찍힌 악보 기호를 직접 읽었습니다: {total}마디, 파트 {len(parts)}개")
    else:
        log(f"   PDF의 도형 데이터에서 악보를 직접 읽었습니다: {total}마디, 파트 {len(parts)}개")
    if skipped:
        log(f"   {page_ranges(skipped)}쪽은 TAB이 없는 다른 편곡(피아노 악보 등)이어서 읽지 않았습니다.")
    # 반음 내림 튜닝이나 카포를 쓰는 기타/베이스는 잡는 자리대로 적혀 있어서 조표가 다른 파트와 다름: 실제 음높이로 옮김
    concert = collections.Counter(state[key_id]['first_key'] for key_id in order
                                  if key_id[0] in ('vocal', 'keyboard') and 'first_key' in state.get(key_id, {}))
    if concert:
        home = concert.most_common(1)[0][0]
        for part, key_id in zip(parts, [k for k in order if k[0] != 'perc']):
            written = state.get(key_id, {}).get('first_key')
            if key_id[0] not in OPEN_STRINGS or written is None or written == home:
                continue
            move = ((home - written) * 7) % 12
            move = move - 12 if move > 6 else move
            for m in part['measures']:
                m['notes'] = [(n[0], n[1], max(0, min(127, n[2] + move)), *n[3:]) for n in m['notes']]
            log(f"   {part['name']} 보표는 다른 파트와 조표가 달라서(튜닝/카포) 실제 음높이에 맞춰 {move:+d} 반음 옮겼습니다.")
    if any(key_id[0] == 'perc' for key_id in order):
        log("   보조 타악기(Perc.) 보표는 드럼 세트가 아니므로 내보내지 않습니다.")
    for part in parts:
        if part['role'] == 'drums' and ridden_cymbal(part['measures']):
            log("   드럼: 크래시 자리의 심벌로 박을 계속 새기는 악보라서, 이어 치는 부분은 라이드로 내보냅니다.")
    _, u, systems, kinds = pages[0]
    bpm = read_tempo(systems[0], kinds, u, table)
    if lettered:
        bpm = Convert_Glyphs.read_tempo(score['texts'], score['music'], u) or bpm
    if bpm:
        log(f"   악보의 템포 표시를 읽었습니다: {bpm:g} BPM")
        parts[0]['measures'][0]['tempos'].append((Fraction(0), bpm))
    if state['retimed']:
        log(f"   박이 맞지 않게 읽힌 성부 {state['retimed']}개(전체 {state['voices']}개)는 다른 파트의 음표 위치를 기준으로 보정했습니다.")
    if len(signs) >= 2:
        log("   D.S. al Coda: 코다 앞까지 연주한 뒤 세뇨로 돌아가 To Coda에서 코다로 건너뜁니다.")
    return parts, pages[0][0]
