import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction

from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo

# 악보 파일 -> MIDI 변환
#   PDF 악보  --(Audiveris: 무료 악보 인식 프로그램)-->  MusicXML  -->  MIDI
# 변환된 MIDI는 Convert_Midi.py의 악기 분리 로직이 그대로 처리합니다.

PDF_EXTS = ('.pdf',)
MUSICXML_EXTS = ('.mxl', '.musicxml')
SCORE_EXTS = PDF_EXTS + MUSICXML_EXTS

TICKS_PER_BEAT = 480
NOTE_VELOCITY = 80
DRUM_CHANNEL = 9
# 드럼 채널(10번)을 제외한 채널을 파트 순서대로 배정
MELODIC_CHANNELS = [ch for ch in range(16) if ch != DRUM_CHANNEL]

AUDIVERIS_DOWNLOAD_URL = 'https://github.com/Audiveris/audiveris/releases'

# 밴드스코어 인식에 필요한 Audiveris 옵션 (기본값은 모두 꺼져 있음)
# - drumNotation: 5선 드럼 보표(타악기 음자리표) 인식
# - 4/6줄 TAB: TAB 보표를 TAB으로 인식해서 건너뜀 (꺼져 있으면 TAB 숫자를 음표로 잘못 읽음)
AUDIVERIS_SWITCHES = ('drumNotation', 'fourStringTablatures', 'sixStringTablatures')

class AudiverisNotFoundError(RuntimeError):
    pass

def find_audiveris():
    """설치된 Audiveris 실행 파일 경로 (없으면 None). 환경 변수 AUDIVERIS_PATH로 직접 지정 가능"""
    candidates = [os.environ.get('AUDIVERIS_PATH'), shutil.which('Audiveris')]
    for var in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(var)
        if not base:
            continue
        for sub in ('', 'Programs'):
            candidates.append(os.path.join(base, sub, 'Audiveris', 'Audiveris.exe'))
            candidates.append(os.path.join(base, sub, 'Audiveris', 'bin', 'Audiveris.bat'))
    candidates.append(os.path.join(os.path.expanduser('~'), 'scoop', 'apps', 'audiveris', 'current', 'Audiveris.exe'))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None

def pdf_to_musicxml(pdf_path, work_dir):
    """
    Audiveris로 PDF 악보를 인식해서 MusicXML(.mxl)로 내보냅니다.
    악장이 나뉘어 인식되면 파일이 여러 개(mvt1, mvt2...) 나오므로 순서대로 정렬한 목록을 반환합니다.
    """
    exe = find_audiveris()
    if exe is None:
        raise AudiverisNotFoundError(
            "PDF 악보를 읽으려면 무료 악보 인식 프로그램 Audiveris가 필요합니다.\n"
            f"   설치 파일(.msi): {AUDIVERIS_DOWNLOAD_URL}\n"
            "   설치 후 다시 시도해 주세요. (다른 위치에 설치했다면 환경 변수 AUDIVERIS_PATH에 실행 파일 경로 지정)")

    # 한글/일본어 파일명이 명령줄에서 깨지지 않도록 영문 이름으로 복사해서 넘김
    src = os.path.join(work_dir, 'score.pdf')
    shutil.copyfile(pdf_path, src)
    out_dir = os.path.join(work_dir, 'out')
    os.makedirs(out_dir, exist_ok=True)

    cmd = [exe, '-batch', '-transcribe', '-export', '-output', out_dir]
    for switch in AUDIVERIS_SWITCHES:
        cmd += ['-constant', f'org.audiveris.omr.sheet.ProcessingSwitches.{switch}=true']
    cmd += ['--', src]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    found = []
    for root, _, filenames in os.walk(out_dir):
        found += [os.path.join(root, fn) for fn in filenames if fn.lower().endswith(MUSICXML_EXTS + ('.xml',))]
    if not found:
        tail = proc.stdout.decode('utf-8', errors='replace').strip()[-600:]
        raise RuntimeError(f"Audiveris가 악보를 인식하지 못했습니다 (종료 코드 {proc.returncode}).\n{tail}")
    # score.mvt2.mxl < score.mvt10.mxl 순서가 되도록 숫자 기준 정렬
    return sorted(found, key=lambda p: [int(s) if s.isdigit() else s for s in re.split(r'(\d+)', os.path.basename(p))])

def read_musicxml_root(path):
    """MusicXML(.musicxml) 또는 압축 MusicXML(.mxl) 읽기"""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = None
            if 'META-INF/container.xml' in z.namelist():
                rootfile = ET.fromstring(z.read('META-INF/container.xml')).find('.//{*}rootfile')
                if rootfile is not None:
                    name = rootfile.get('full-path')
            if name is None:
                name = next(n for n in z.namelist()
                            if n.lower().endswith(('.xml', '.musicxml')) and not n.startswith('META-INF'))
            data = z.read(name)
    else:
        with open(path, 'rb') as f:
            data = f.read()
    root = ET.fromstring(data)
    if root.tag != 'score-partwise':
        raise ValueError(f"지원하지 않는 MusicXML 형식입니다: {root.tag}")
    return root

STEP_SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}

# 5선 드럼 보표의 음표 위치(높은음자리표 기준 음이름) -> GM 드럼 음표
# 음표에 악기 정보(midi-unpitched)가 없을 때만 사용
DRUM_POSITION_NORMAL = {
    'D4': 36, 'E4': 36, 'F4': 36,   # 킥
    'G4': 41, 'A4': 43,             # 플로어 탐
    'B4': 45, 'D5': 47,             # 미드 탐
    'C5': 38,                       # 스네어
    'E5': 50, 'F5': 50,             # 하이 탐
}
# X 모양 머리 (심벌/하이햇/림)
DRUM_POSITION_CROSS = {
    'D4': 44,                       # 페달 하이햇
    'C5': 37,                       # 림(사이드 스틱)
    'E5': 42, 'G5': 42,             # 클로즈드 하이햇
    'F5': 51,                       # 라이드
    'A5': 49, 'B5': 57, 'C6': 52,   # 크래시 / 차이나
}

def drum_note_from_position(step, octave, notehead):
    pos = f"{step}{octave}"
    if notehead in ('x', 'cross', 'circle-x', 'diamond', 'triangle', 'inverted triangle'):
        if notehead == 'circle-x' and pos == 'G5':
            return 46  # 오픈 하이햇
        if notehead == 'diamond' and pos == 'F5':
            return 53  # 라이드 벨
        if pos in DRUM_POSITION_CROSS:
            return DRUM_POSITION_CROSS[pos]
        return 49 if (octave, STEP_SEMITONES.get(step, 0)) >= (5, 9) else 42
    return DRUM_POSITION_NORMAL.get(pos, 38)

def parse_part(part, drum_instruments):
    """
    한 파트를 마디 단위로 읽습니다. 위치/길이는 4분음표 단위(Fraction).
    drum_instruments: {score-instrument id: GM 드럼 음표}
    """
    signs = {c.findtext('sign') for c in part.iter('clef')}
    # 5선보 + TAB이 같이 있는 파트는 같은 음이 두 번 들어 있으므로 TAB 쪽을 버림
    skip_tab = 'TAB' in signs and len(signs - {'TAB'}) > 0

    divisions = 1
    transpose = 0
    clefs = {}
    nominal = None
    measures = []
    staff_numbers = {'1'}
    pitched = lyric_notes = 0

    for measure in part.findall('measure'):
        pos = extent = last_start = Fraction(0)
        m = {'notes': [], 'tempos': [], 'time': None, 'forward': False, 'backward': 0,
             'ending_start': None, 'ending_stop': False}
        for el in measure:
            if el.tag == 'attributes':
                if el.findtext('divisions'):
                    divisions = int(el.findtext('divisions')) or 1
                for clef in el.findall('clef'):
                    clefs[clef.get('number') or '1'] = clef.findtext('sign')
                tr = el.find('transpose')
                if tr is not None:
                    transpose = int(float(tr.findtext('chromatic') or 0)) + 12 * int(tr.findtext('octave-change') or 0)
                time = el.find('time')
                if time is not None and time.findtext('beats') and time.findtext('beat-type'):
                    beats = sum(int(b) for b in re.findall(r'\d+', time.findtext('beats')))
                    beat_type = int(time.findtext('beat-type'))
                    if beats and beat_type:
                        nominal = Fraction(beats * 4, beat_type)
                        m['time'] = (beats, beat_type)
            elif el.tag in ('backup', 'forward'):
                dur = Fraction(int(float(el.findtext('duration') or 0)), divisions)
                pos = pos - dur if el.tag == 'backup' else pos + dur
                extent = max(extent, pos)
            elif el.tag in ('direction', 'sound'):
                for sound in el.iter('sound'):
                    if sound.get('tempo'):
                        m['tempos'].append((pos, float(sound.get('tempo'))))
            elif el.tag == 'barline':
                repeat = el.find('repeat')
                if repeat is not None:
                    if repeat.get('direction') == 'forward':
                        m['forward'] = True
                    else:
                        m['backward'] = int(repeat.get('times') or 2)
                ending = el.find('ending')
                if ending is not None:
                    if ending.get('type') == 'start':
                        m['ending_start'] = {int(n) for n in re.findall(r'\d+', ending.get('number') or '')}
                    else:
                        m['ending_stop'] = True
            elif el.tag == 'note':
                if el.find('grace') is not None or el.find('cue') is not None:
                    continue
                dur = Fraction(int(float(el.findtext('duration') or 0)), divisions)
                if el.find('chord') is not None:
                    start = last_start
                else:
                    start = last_start = pos
                    pos += dur
                    extent = max(extent, pos)
                staff = el.findtext('staff') or '1'
                staff_numbers.add(staff)
                sign = clefs.get(staff, clefs.get('1'))
                if el.find('rest') is not None or (skip_tab and sign == 'TAB'):
                    continue
                pitch = el.find('pitch')
                unpitched = el.find('unpitched')
                if pitch is not None and sign != 'percussion':
                    note = (12 * (int(pitch.findtext('octave')) + 1) + STEP_SEMITONES[pitch.findtext('step')]
                            + int(round(float(pitch.findtext('alter') or 0))) + transpose)
                    is_drum = False
                    pitched += 1
                    lyric_notes += el.find('lyric') is not None
                else:
                    inst = el.find('instrument')
                    note = drum_instruments.get(inst.get('id')) if inst is not None else None
                    if note is None:
                        src = pitch if pitch is not None else unpitched
                        prefix = '' if pitch is not None else 'display-'
                        step = src.findtext(prefix + 'step') if src is not None else None
                        octave = src.findtext(prefix + 'octave') if src is not None else None
                        note = drum_note_from_position(step or 'C', int(octave or 5), el.findtext('notehead') or 'normal')
                    is_drum = True
                ties = {t.get('type') for t in el.findall('tie')}
                m['notes'].append((start, dur, max(0, min(127, note)), is_drum, 'start' in ties, 'stop' in ties))
        m['extent'] = extent
        m['nominal'] = nominal
        measures.append(m)

    return {
        'measures': measures,
        'pitched': pitched,
        'lyric_notes': lyric_notes,
        'single_bass_clef': len(staff_numbers) == 1 and signs == {'F'},
    }

def playback_order(marks):
    """
    도돌이표 / 1번·2번 괄호를 풀어서 실제 연주되는 마디 순서를 만듭니다.
    (D.S. / D.C. / Coda 점프는 처리하지 않고 적힌 순서대로 연주)
    """
    # 괄호가 걸친 모든 마디에 괄호 번호를 표시
    endings = []
    current = None
    for m in marks:
        if m['forward']:
            current = None  # 닫는 표시를 못 읽은 괄호가 다음 도돌이 구간까지 이어지지 않도록
        if m['ending_start']:
            current = m['ending_start']
        endings.append(current)
        if m['ending_stop']:
            current = None

    order = []
    i = start = 0
    pass_no = 1
    while i < len(marks) and len(order) < len(marks) * 20:
        m = marks[i]
        if endings[i] is None and i > 0 and endings[i - 1] is not None and pass_no > 1:
            start, pass_no = i, 1   # 마지막 괄호를 빠져나옴 -> 새 구간 시작
        if m['forward'] and start != i:
            start, pass_no = i, 1
        if endings[i] is not None and pass_no not in endings[i]:
            i += 1
            continue
        order.append(i)
        if m['backward']:
            times = max(m['backward'], max(endings[i]) + 1 if endings[i] else 0)
            if pass_no < times:
                pass_no += 1
                i = start
                continue
            if endings[i] is None:
                start, pass_no = i + 1, 1
        i += 1
    return order

# 파트 이름 약어 (점/숫자/공백을 뺀 이름 전체가 일치할 때만) -> 악기 분리 로직이 알아보는 이름
PART_NAME_ALIASES = {
    'Guitar': ('g', 'gt', 'gtr', 'eg', 'ag', 'egt', 'agt'),
    'Bass': ('b', 'ba', 'bs', 'eb'),
}
# Audiveris가 파트 이름을 못 읽었을 때 붙이는 기본 이름 (실제 이름이 아님)
AUDIVERIS_DEFAULT_NAMES = ('voice', 'noname')

def name_part(name, program, part, from_audiveris):
    """
    트랙 이름/악기 번호 결정. 스캔 악보는 파트 이름과 악기 정보가 없는 경우가 많아서
    이름이 없으면 가사(보컬)와 낮은음자리표(베이스)로 추정합니다.
    반환: (트랙 이름, GM 프로그램 번호 또는 None)
    """
    if from_audiveris and name.lower() in AUDIVERIS_DEFAULT_NAMES:
        name, program = '', None
    compact = re.sub(r'[^a-z]', '', name.lower())
    for canonical, aliases in PART_NAME_ALIASES.items():
        if compact in aliases:
            return f"{canonical} ({name})", program
    if name:
        return name, program
    if not part['pitched']:
        return 'Drums', None
    if part['lyric_notes'] * 2 >= part['pitched']:
        return 'Vocal', program
    if part['single_bass_clef'] and (program is None or not 24 <= program <= 39):
        return 'Bass', None
    return '', program

def load_musicxml(path):
    """
    MusicXML 한 파일 -> 파트별 음표 목록 (시간은 4분음표 단위)
    반환: {'parts': [{'name', 'program', 'notes': [[시작, 끝, 음, 드럼여부]]}], 'tempos', 'times', 'length'}
    """
    root = read_musicxml_root(path)
    from_audiveris = 'audiveris' in ' '.join(s.text or '' for s in root.iter('software')).lower()

    part_infos = {}
    for sp in root.iterfind('part-list/score-part'):
        drums = {}
        program = None
        for mi in sp.findall('midi-instrument'):
            if mi.findtext('midi-unpitched'):
                # MusicXML 규격은 (MIDI 음표 + 1)인데 Audiveris는 MIDI 음표를 그대로 기록
                drums[mi.get('id')] = int(mi.findtext('midi-unpitched')) - (0 if from_audiveris else 1)
            elif program is None and mi.findtext('midi-program'):
                program = int(mi.findtext('midi-program')) - 1
        part_infos[sp.get('id')] = ((sp.findtext('part-name') or '').strip(), program, drums)

    parsed = []
    for part in root.findall('part'):
        name, program, drums = part_infos.get(part.get('id'), ('', None, {}))
        p = parse_part(part, drums)
        p['name'], p['program'] = name_part(name, program, p, from_audiveris)
        parsed.append(p)
    if not parsed:
        raise ValueError("악보에 파트가 없습니다.")

    n_measures = max(len(p['measures']) for p in parsed)
    lengths = []
    marks = []
    for i in range(n_measures):
        ms = [p['measures'][i] for p in parsed if i < len(p['measures'])]
        extent = max(m['extent'] for m in ms)
        nominal = next((m['nominal'] for m in ms if m['nominal']), None)
        # 마디 길이는 내용이 가장 긴 파트 기준 (못갖춘마디 대응).
        # 박자보다 길면 리듬을 잘못 읽은 것이므로 박자 길이로 맞춰서 다른 파트가 밀리지 않게 함
        lengths.append(min(extent, nominal) if nominal and extent else (nominal or extent))
        marks.append({
            'forward': any(m['forward'] for m in ms),
            'backward': max(m['backward'] for m in ms),
            'ending_start': next((m['ending_start'] for m in ms if m['ending_start']), None),
            'ending_stop': any(m['ending_stop'] for m in ms),
        })

    order = playback_order(marks)
    bases = []
    offset = Fraction(0)
    for i in order:
        bases.append(offset)
        offset += lengths[i]

    score = {'parts': [], 'tempos': [], 'times': [], 'length': offset}
    for p in parsed:
        notes = []
        open_ties = {}
        for i, base in zip(order, bases):
            if i >= len(p['measures']):
                continue
            m = p['measures'][i]
            for at, bpm in m['tempos']:
                if bpm > 0 and (base + at, bpm) not in score['tempos']:
                    score['tempos'].append((base + at, bpm))
            # 박자는 모든 파트에 똑같이 적혀 있으므로 첫 파트 것만 사용
            if p is parsed[0] and m['time'] and (not score['times'] or score['times'][-1][1] != m['time']):
                score['times'].append((base, m['time']))
            for start, dur, note, is_drum, tie_start, tie_stop in m['notes']:
                key = (is_drum, note)
                begin = base + start
                tied = open_ties.pop(key, None) if tie_stop else None
                if tied is not None and begin - tied[1] <= Fraction(1, 4):
                    tied[1] = max(tied[1], begin + dur)  # 붙임줄: 앞 음을 늘림
                    rec = tied
                else:
                    rec = [begin, begin + dur, note, is_drum]
                    notes.append(rec)
                if tie_start:
                    open_ties[key] = rec
        score['parts'].append({'name': p['name'], 'program': p['program'], 'notes': notes})
    return score

def scores_to_midi(scores):
    """load_musicxml 결과(악장이 여러 개면 순서대로 이어 붙임) -> MidiFile"""
    def tick(quarters):
        return int(round(quarters * TICKS_PER_BEAT))

    conductor = []
    parts = []
    offset = Fraction(0)
    for score in scores:
        conductor += [(tick(offset + t), MetaMessage('set_tempo', tempo=bpm2tempo(bpm))) for t, bpm in score['tempos']]
        conductor += [(tick(offset + t), MetaMessage('time_signature', numerator=num, denominator=den))
                      for t, (num, den) in score['times'] if den in (1, 2, 4, 8, 16, 32) and 0 < num < 256]
        for idx, part in enumerate(score['parts']):
            if idx == len(parts):
                parts.append({'name': part['name'], 'program': part['program'], 'notes': []})
            parts[idx]['notes'] += [(offset + s, offset + e, note, drum) for s, e, note, drum in part['notes']]
        offset += score['length']

    def to_track(events):
        track = MidiTrack()
        last = 0
        for at, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
            track.append(msg.copy(time=at - last))
            last = at
        return track

    mid = MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    mid.tracks.append(to_track([(at, 0, msg) for at, msg in conductor]))
    for idx, part in enumerate(parts):
        if not part['notes']:
            continue
        ch = MELODIC_CHANNELS[idx % len(MELODIC_CHANNELS)]
        events = []
        if part['name']:
            events.append((0, 0, MetaMessage('track_name', name=part['name'])))
        if part['program'] is not None and 0 <= part['program'] <= 127:
            events.append((0, 0, Message('program_change', channel=ch, program=part['program'])))
        for start, end, note, is_drum in part['notes']:
            on = tick(start)
            off = max(tick(end), on + 1)
            note_ch = DRUM_CHANNEL if is_drum else ch
            # 같은 틱에서는 note_off(1)가 note_on(2)보다 먼저 오도록 정렬
            events.append((on, 2, Message('note_on', channel=note_ch, note=note, velocity=NOTE_VELOCITY)))
            events.append((off, 1, Message('note_off', channel=note_ch, note=note, velocity=0)))
        mid.tracks.append(to_track(events))
    if len(mid.tracks) < 2:
        raise ValueError("악보에서 음표를 찾지 못했습니다.")
    return mid

def score_to_midi(path, log=print):
    """PDF / MusicXML 악보 -> MidiFile"""
    if path.lower().endswith(MUSICXML_EXTS):
        return scores_to_midi([load_musicxml(path)])
    with tempfile.TemporaryDirectory(prefix='midivision_') as work_dir:
        log("   PDF 악보 인식 중입니다 (Audiveris). 페이지 수에 따라 몇 분 걸릴 수 있습니다...")
        xml_paths = pdf_to_musicxml(path, work_dir)
        return scores_to_midi([load_musicxml(p) for p in xml_paths])
