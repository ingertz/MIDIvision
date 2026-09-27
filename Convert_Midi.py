import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import io
import sys
import os
import re

# GM 드럼 노트 번호 -> 스타 레조넌스 건반 노트 번호 정밀 매핑 테이블
# 인게임 건반:
# F  = F4 (65) : 베이스 드럼 (Kick)
# Q  = C5 (72) : 스네어 드럼 (Snare)
# S  = D4 (62) : 클로즈드/페달 하이햇 (Closed/Pedal Hi-Hat)
# T  = G5 (79) : 오픈 하이햇 (Open Hi-Hat)
# W  = D5 (74) : 미드 탐 (Mid Tom)
# E  = E5 (76) : 하이 탐 (High Tom)
# H  = A4 (69) : 플로어 탐 (Floor Tom) ★ (A4#인 70번=0번키가 아닌 A4=69번=H키!)
# R  = F5 (77) : 크래시 심벌 1 (Crash Cymbal 1 / China / Splash)
# Y  = A5 (81) : 라이드 심벌 / 크래시 2 (Ride Cymbal / Crash 2)

DRUM_MAP = {
    # 베이스 드럼 계열 -> F4 (인게임 F키 = 65)
    35: 65,  # Acoustic Bass Drum
    36: 65,  # Bass Drum 1
    
    # 스네어 계열 -> C5 (인게임 Q키 = 72)
    37: 72,  # Side Stick
    38: 72,  # Acoustic Snare
    39: 72,  # Hand Clap
    40: 72,  # Electric Snare
    
    # 하이햇 계열
    42: 62,  # Closed Hi-Hat -> D4 (인게임 S키 = 62)
    44: 62,  # Pedal Hi-Hat  -> D4 (인게임 S키 = 62)
    46: 79,  # Open Hi-Hat   -> G5 (인게임 T키 = 79)
    
    # 탐(Tom) 계열
    41: 69,  # Low Floor Tom  -> A4 (인게임 H키 = 69) ★ A4#인 70(0번키)이 아닌 69(H키)!
    43: 69,  # High Floor Tom -> A4 (인게임 H키 = 69)
    45: 74,  # Low Tom        -> D5 (인게임 W키 = 74)
    47: 74,  # Low-Mid Tom    -> D5 (인게임 W키 = 74)
    48: 74,  # Hi-Mid Tom     -> D5 (인게임 W키 = 74)
    50: 76,  # High Tom       -> E5 (인게임 E키 = 76)
    58: 76,  # Vibra-Slap     -> E5 (인게임 E키 = 76)
    
    # 심벌(Cymbal) 계열 -> F5 (인게임 R키 = 77) / A5 (인게임 Y키 = 81)
    49: 77,  # Crash Cymbal 1 -> F5 (인게임 R키 = 77)
    52: 77,  # Chinese Cymbal -> F5 (인게임 R키 = 77)
    55: 77,  # Splash Cymbal  -> F5 (인게임 R키 = 77)
    57: 77,  # Crash Cymbal 2 -> F5 (인게임 R키 = 77)
    51: 81,  # Ride Cymbal 1  -> A5 (인게임 Y키 = 81)
    53: 81,  # Ride Bell      -> A5 (인게임 Y키 = 81)
    59: 81,  # Ride Cymbal 2  -> A5 (인게임 Y키 = 81)

    # 기타 확장 타악기
    27: 72, 28: 72, 29: 72, 30: 72, 31: 72,  # Laser/Slap/Scratch/Sticks -> Q키 (스네어/스틱)
    32: 62, 33: 62, 34: 62,                  # Clicks/Bells -> S키 (하이햇)
    54: 81,  # Tambourine     -> Y키 (81)
    56: 77,  # Cowbell        -> R키 (77)
    60: 81, 61: 81, 62: 74, 63: 76, 64: 76, 65: 74, 66: 69,
    67: 62, 68: 72, 69: 69, 
    70: 62,  # 70번 Maracas / Shaker -> D4 (인게임 S키 = 62, Closed Hi-Hat) ★ 플로어탐(H)이 아닌 하이햇으로 매핑!
    71: 77, 72: 72, 73: 72, 74: 79, 75: 81, 76: 81, 77: 72, 78: 72,
    79: 72, 80: 72, 81: 72, 82: 77, 83: 77,
    84: 77, 85: 72, 86: 69, 87: 69,  # GS 확장: Belltree / Castanets / Surdo
}

# 드럼 음표로 인정하는 범위 (GM/GS 타악기 27~87). 범위 밖 음표는 드럼 소리가 아니므로 버림
DRUM_NOTE_RANGE = range(27, 88)

# 쉐이커 계열 보조 타악기는 드럼 세트가 아니므로 버림
# (예: Melt는 마라카스(70)가 1,891번 나와서 H/S키를 계속 연타하게 됨)
SKIP_DRUM_NOTES = {
    69,  # Cabasa
    70,  # Maracas
    82,  # Shaker
}

def get_mapped_drum_note(note):
    """
    GM 드럼 음표 -> 인게임 키. 타악기 범위(27~87) 밖의 음표는 None을 반환하여 버립니다.
    (예전에는 범위 밖 음표를 전부 크래시(R)로 바꿔서, 드럼이 아닌 음표가 섞이면
     크래시가 계속 울리는 문제가 있었음)
    """
    if note not in DRUM_NOTE_RANGE or note in SKIP_DRUM_NOTES:
        return None
    return DRUM_MAP[note]

# 보컬(멜로디) 트랙 이름 키워드: 악기 번호가 기타/베이스여도 무조건 건반으로 보냄
VOCAL_KEYWORDS = ['vocal', 'vocals', 'vo', 'vox', 'voice', 'melody', 'singer', 'lead vocal', 'chorus',
                  '보컬', '노래', '멜로디', '코러스', 'ボーカル', '歌', 'メロディ', 'コーラス', '人声', '主唱', '旋律', '和声']

# 악기별 판별 설정 (기타, 베이스, 건반, 드럼)
INSTRUMENT_CONFIG = {
    'drum': {
        'name': '드럼',
        'suffix': ' (Drum)',
        'remap_drums': True,  # 드럼은 스타 레조넌스 음계 이동(매핑) 유지!
        'keywords': ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb', 'ドラム', 'パーカッション', '打楽器'],
        'channel_match': lambda ch: ch == 9,
        'program_match': lambda p: False,
    },
    'guitar': {
        'name': '기타',
        'suffix': ' (Guitar)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타', 'ギター'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 24 <= p <= 31,
    },
    'bass': {
        'name': '베이스',
        'suffix': ' (Bass)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스', 'ベース'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 32 <= p <= 39,
    },
    'keyboard': {
        'name': '건반',
        'suffix': ' (Keyboard)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['piano', 'key', 'keyboard', 'synth', 'organ', 'clav', 'rhodes', 'harpsichord', '피아노', '건반', '신스', 'ピアノ', 'オルガン', 'シンセ', 'キーボード'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: (0 <= p <= 23) or (80 <= p <= 103),
    }
}

def match_keyword(name, keywords):
    """
    키워드 매칭 함수:
    - 영문 키워드는 단어 경계(?<![a-z0-9])를 사용하여 supercell 내 'perc', legend 내 'eg' 등의 오판별을 방지합니다.
    - 한글 등 비영문 키워드는 일반 부분 일치 검사를 수행합니다.
    """
    lower = name.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if not re.match(r'^[a-z0-9_ -]+$', kw_lower):
            if kw_lower in lower:
                return True
        else:
            pattern = r'(?<![a-z0-9])' + re.escape(kw_lower) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                return True
    return False

# 모든 트랙에 공통으로 적용되어야 하는 메타 메시지 (템포/박자/조성)
CONDUCTOR_META_TYPES = ('set_tempo', 'time_signature', 'key_signature')

def is_note_on(msg):
    return msg.type == 'note_on' and msg.velocity > 0

def is_note_off(msg):
    return msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)

def classify_program(program):
    """GM 프로그램 번호 -> 악기 키"""
    if 32 <= program <= 39:
        return 'bass'
    if 24 <= program <= 31:
        return 'guitar'
    return 'keyboard'

def decode_text(text):
    """
    mido는 트랙 이름을 latin-1로 읽기 때문에 일본어(Shift-JIS)/한국어(CP949) 이름이 깨집니다.
    원래 바이트로 되돌린 뒤 UTF-8 -> Shift-JIS -> CP949 순서로 다시 디코딩합니다.
    """
    try:
        raw = text.encode('latin-1')
    except UnicodeEncodeError:
        return text
    for enc in ('utf-8', 'cp932', 'cp949'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return text

# 뱅크 셀렉트(CC0) 값이 이 값이면 야마하 XG 드럼 킷 (채널 10이 아니어도 드럼)
XG_DRUM_BANKS = (126, 127)

# 베이스 악기 번호라도 중간 음이 이보다 높으면 베이스가 아님 (G3 = 55)
# 예: Pretender는 채널 7이 Synth Bass 2(39)인데 실제로는 32~89 음역 화음을 치는 신스
BASS_MAX_MEDIAN_NOTE = 55

def track_drum_channels(track):
    """드럼 채널 집합: 채널 10(인덱스 9) + XG 드럼 뱅크(127/126)가 선택된 채널"""
    drum = {9}
    for msg in track:
        if msg.type == 'control_change' and msg.control == 0 and msg.value in XG_DRUM_BANKS:
            drum.add(msg.channel)
    return drum

def channel_median_notes(track):
    """채널별 음 높이 중간값"""
    notes = {}
    for msg in track:
        if is_note_on(msg):
            notes.setdefault(msg.channel, []).append(msg.note)
    return {ch: sorted(ns)[len(ns) // 2] for ch, ns in notes.items()}

def file_has_drum_channel(mid):
    """파일 어딘가에 드럼 채널(채널 10 또는 XG 드럼 킷) 음표가 있는지"""
    for track in mid.tracks:
        drum = track_drum_channels(track)
        if any(is_note_on(msg) and msg.channel in drum for msg in track):
            return True
    return False

def analyze_track_instrument(track, drum_by_name=True):
    """
    트랙의 이름, 프로그램 번호, 채널, 음표 정보를 기반으로 악기 판별:
    - 드럼: 채널 9 또는 드럼 키워드 (drum_by_name=False이면 채널 9만 인정)
    - 베이스: 베이스 키워드, 또는 베이스 프로그램(32~39)이면서 중간 음이 G3 이하
    - 기타: 기타 프로그램(24~31) 또는 기타 키워드
    - 보컬: 트랙 이름이 보컬이면 악기 번호와 상관없이 건반
    - 건반: 위를 제외한 모든 악기(피아노, 색소폰, 브라스, 스트링, 플루트, 보컬 멜로디 등)를 건반으로 배정!
    """
    name = ""
    channels = set()
    programs = set()
    total_notes = 0
    has_tempo = False

    for msg in track:
        if msg.type == 'track_name':
            name = decode_text(msg.name)
        elif msg.type in CONDUCTOR_META_TYPES:
            has_tempo = True
        elif msg.type == 'program_change':
            programs.add(msg.program)
        elif msg.type in ('note_on', 'note_off'):
            if is_note_on(msg):
                total_notes += 1
            channels.add(msg.channel)

    drum_channels = track_drum_channels(track)
    medians = channel_median_notes(track)
    all_notes = sorted(msg.note for msg in track if is_note_on(msg))
    median_note = all_notes[len(all_notes) // 2] if all_notes else 0

    info = {
        'name': name,
        'total_notes': total_notes,
        'has_tempo': has_tempo,
        'channels': channels,
        'programs': programs,
        'drum_channels': drum_channels,
        'channel_medians': medians,
    }

    # 음표가 없는 메타/템포 트랙
    if total_notes == 0:
        info['instrument'] = 'tempo' if has_tempo else 'empty'
    # 1. 드럼 판별 (채널 10 / XG 드럼 킷 또는 드럼 키워드)
    elif (channels & drum_channels) or (drum_by_name and match_keyword(name, INSTRUMENT_CONFIG['drum']['keywords'])):
        info['instrument'] = 'drum'
    # 2. 보컬 트랙 -> 건반 (보컬 멜로디를 기타/베이스 음색으로 찍어둔 MIDI 대비)
    elif match_keyword(name, VOCAL_KEYWORDS):
        info['instrument'] = 'keyboard'
    # 3. 베이스 판별 (베이스 키워드, 또는 베이스 프로그램 32~39 이면서 실제로 낮은 음역)
    elif match_keyword(name, INSTRUMENT_CONFIG['bass']['keywords']) or (
            any(32 <= p <= 39 for p in programs) and median_note <= BASS_MAX_MEDIAN_NOTE):
        info['instrument'] = 'bass'
    # 4. 기타 판별 (기타 키워드 또는 기타 프로그램 24~31)
    elif match_keyword(name, INSTRUMENT_CONFIG['guitar']['keywords']) or any(24 <= p <= 31 for p in programs):
        info['instrument'] = 'guitar'
    # 5. 그 외 모든 악기 -> 건반(Keyboard)으로 배정!
    # (색소폰, 트럼펫, 브라스, 바이올린, 스트링, 플루트, 오보에, 보컬 멜로디, 피아노, 오르간, 신스 등)
    else:
        info['instrument'] = 'keyboard'
    return info

def build_conductor_track(mid):
    """
    모든 트랙에서 템포/박자/조성 메타 메시지를 모아 하나의 컨덕터 트랙을 만듭니다.
    (템포가 음표가 있는 트랙에 들어 있어도 추출 파일의 재생 속도가 틀어지지 않도록)
    """
    events = []
    seen = set()
    for track in mid.tracks:
        abs_time = 0
        for msg in track:
            abs_time += msg.time
            if msg.type in CONDUCTOR_META_TYPES:
                key = (abs_time, str(msg.copy(time=0)))
                if key not in seen:
                    seen.add(key)
                    events.append((abs_time, msg))
    events.sort(key=lambda e: e[0])

    conductor = MidiTrack()
    last = 0
    for abs_time, msg in events:
        conductor.append(msg.copy(time=abs_time - last))
        last = abs_time
    return conductor

class DrumNoteMapper:
    """
    여러 GM 드럼 음표가 같은 인게임 키로 합쳐질 때(예: 42/44 -> S키) 발생하는
    note_on/note_off 꼬임(소리가 중간에 끊기거나 겹쳐서 씹히는 현상)을 방지합니다.
    - 같은 틱에 같은 키가 중복으로 눌리면 1번만 누름
    - 이미 눌린 키가 다시 눌리면 먼저 떼고 다시 누름(재타격)
    - note_off는 해당 키를 마지막으로 누른 원본 음표에서만 발생
    """
    def __init__(self):
        self.owner = {}        # 인게임 키 -> 현재 누르고 있는 원본 음표
        self.last_on_tick = {} # 인게임 키 -> 마지막으로 누른 틱

    def process(self, msg, abs_tick):
        """변환된 메시지 목록을 반환 (없으면 빈 목록)"""
        key = get_mapped_drum_note(msg.note)
        if key is None:
            return []  # 타악기 범위 밖 음표는 버림
        if is_note_on(msg):
            if self.last_on_tick.get(key) == abs_tick:
                return []  # 같은 순간 중복 타격 제거
            out = []
            if key in self.owner:
                out.append(Message('note_off', channel=msg.channel, note=key, velocity=0))
            self.owner[key] = msg.note
            self.last_on_tick[key] = abs_tick
            out.append(msg.copy(note=key, time=0))
            return out
        # note_off
        if self.owner.get(key) != msg.note:
            return []
        del self.owner[key]
        return [msg.copy(note=key, time=0)]

def to_absolute(track):
    """MidiTrack -> [(절대틱, 메시지)]"""
    abs_tick = 0
    events = []
    for msg in track:
        abs_tick += msg.time
        events.append((abs_tick, msg))
    return events

def to_track(events):
    """[(절대틱, 메시지)] -> MidiTrack (시간순 정렬, 같은 틱이면 기존 순서 유지)"""
    track = MidiTrack()
    last = 0
    for abs_tick, msg in sorted(events, key=lambda e: e[0]):
        track.append(msg.copy(time=abs_tick - last))
        last = abs_tick
    return track

def song_timeline(mid):
    """원곡 전체의 첫 음 시작 / 마지막 음 시작 / 마지막 음 끝 / 파일 끝 틱"""
    first_on = last_on = last_off = None
    song_end = 0
    for track in mid.tracks:
        for abs_tick, msg in to_absolute(track):
            song_end = max(song_end, abs_tick)
            if msg.type not in ('note_on', 'note_off'):
                continue
            if is_note_on(msg):
                first_on = abs_tick if first_on is None else min(first_on, abs_tick)
                last_on = abs_tick if last_on is None else max(last_on, abs_tick)
            else:
                last_off = abs_tick if last_off is None else max(last_off, abs_tick)
    if first_on is None:
        return None
    if last_off is None or last_off < last_on:
        last_off = last_on
    return {'first_on': first_on, 'last_on': last_on, 'last_off': last_off, 'end': song_end}

# 시작/끝 맞춤용 음표 세기 (소리는 거의 안 나지만 게임이 음표로 인식하도록 1)
ANCHOR_VELOCITY = 1

def align_to_song_timeline(new_mid, part_indices, timeline):
    """
    파트별로 분리하면 그 악기의 첫 음/마지막 음 기준으로 곡이 잘려서
    파트마다 시작·종료 시간이 달라집니다. (예: Melt 드럼 253.7초, 원곡 256.7초)
    원곡의 첫 음/마지막 음 위치에 아주 약한 음표를 넣고 파일 끝도 원곡과 맞춥니다.
    """
    if timeline is None or not part_indices:
        return
    all_events = [(i, to_absolute(new_mid.tracks[i])) for i in part_indices]
    note_ons = [(t, m) for _, evs in all_events for t, m in evs if is_note_on(m)]
    note_offs = [(t, m) for _, evs in all_events for t, m in evs if is_note_off(m)]
    if not note_ons:
        return
    part_first_tick, part_first_msg = min(note_ons, key=lambda e: e[0])
    part_last_tick, part_last_msg = max(note_ons, key=lambda e: e[0])
    part_last_off = max([t for t, _ in note_offs] + [part_last_tick])

    idx, events = all_events[0]
    ch = part_first_msg.channel

    # 시작 맞춤: 원곡 첫 음 위치에 이 파트의 첫 음과 같은 음 (첫 음 전에 끝나도록 짧게)
    if part_first_tick > timeline['first_on']:
        start = timeline['first_on']
        off = min(start + max(1, new_mid.ticks_per_beat // 16), part_first_tick)
        events.append((start, Message('note_on', channel=ch, note=part_first_msg.note, velocity=ANCHOR_VELOCITY)))
        events.append((off, Message('note_off', channel=ch, note=part_first_msg.note, velocity=0)))

    # 끝 맞춤: 원곡 마지막 음 위치에 이 파트의 마지막 음과 같은 음
    if part_last_off < timeline['last_off']:
        on = max(timeline['last_on'], part_last_off)
        off = max(timeline['last_off'], on + 1)
        events.append((on, Message('note_on', channel=part_last_msg.channel, note=part_last_msg.note, velocity=ANCHOR_VELOCITY)))
        events.append((off, Message('note_off', channel=part_last_msg.channel, note=part_last_msg.note, velocity=0)))

    # 파일 끝(end_of_track)도 원곡과 동일하게
    track_end = max(t for t, _ in events) if events else 0
    events = [(t, m) for t, m in events if m.type != 'end_of_track']
    events.append((max(timeline['end'], track_end), MetaMessage('end_of_track')))
    new_mid.tracks[idx] = to_track(events)

# ===== 간소화(화음/트랙 줄이기) 설정 =====
# 파트별 동시에 누르는 최대 음 수 (None = 제한 없음)
SIMPLIFY_MAX_NOTES = {'drum': None, 'guitar': 3, 'bass': 1, 'keyboard': 3}
SIMPLIFY_DROP = 99  # 이 우선순위는 아예 버림

def simplify_tier(inst_key, program, is_melody):
    """
    간소화 시 음표 우선순위 (숫자가 작을수록 중요)
    0: 멜로디(보컬 라인)  1: 멜로디가 쉬는 마디의 리드(솔로)  2: 피아노/건반  3: 신스·브라스·리드 등  4: 현악·코러스
    SIMPLIFY_DROP: 패드/효과음/민속악기/SFX (GM 88~127) -> 버림
    """
    if is_melody:
        return 0
    if inst_key != 'keyboard' or program is None:
        return 2
    if program >= 88:
        return SIMPLIFY_DROP
    if program <= 23:
        return 2
    if 40 <= program <= 55:
        return 4
    return 3

def detect_melody_sources(mid, drum_by_name):
    """
    건반 파트에서 멜로디(보컬 라인)를 치는 (트랙 번호, 채널) 찾기
    1순위: 이름이 보컬인 트랙
    2순위: 한 음씩만 치는(단선율) 고음역 채널 중 음표가 가장 많은 것
    """
    vocal = set()
    candidates = []
    for idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track, drum_by_name)
        if info['total_notes'] == 0:
            continue
        drum_chs = info['drum_channels']
        if match_keyword(info['name'], VOCAL_KEYWORDS):
            vocal |= {(idx, ch) for ch in info['channels'] if ch not in drum_chs}
            continue
        first_program = {}
        active = {}
        stats = {}
        for msg in track:
            if msg.type == 'program_change':
                first_program.setdefault(msg.channel, msg.program)
            elif msg.type in ('note_on', 'note_off') and msg.channel not in drum_chs:
                st = stats.setdefault(msg.channel, {'notes': [], 'poly': 0})
                held = active.setdefault(msg.channel, set())
                if is_note_on(msg):
                    held.add(msg.note)
                    st['notes'].append(msg.note)
                    st['poly'] += len(held)
                else:
                    held.discard(msg.note)
        for ch, st in stats.items():
            prog = first_program.get(ch)
            if prog is not None and (classify_program(prog) in ('guitar', 'bass') or prog >= 88):
                continue
            if prog is None and info['instrument'] in ('guitar', 'bass', 'drum'):
                continue
            ns = sorted(st['notes'])
            if not ns:
                continue
            median = ns[len(ns) // 2]
            avg_poly = st['poly'] / len(ns)
            if avg_poly <= 1.2 and 55 <= median <= 90:
                candidates.append((len(ns), idx, ch))
    if vocal:
        return vocal
    if candidates:
        _, idx, ch = max(candidates)
        return {(idx, ch)}
    return set()

# 멜로디가 길게 끄는 음을 끊고 들어갈 수 있는 솔로의 최소 음표 수 (마디당)
SOLO_MIN_NOTES_PER_BAR = 8

def assign_bar_leads(events, ticks_per_bar, tolerance):
    """
    메인 멜로디가 쉬는 마디(간주, 솔로, 인트로)에서는 그 마디의 리드 라인을 멜로디로 승격합니다.
    - 마디 안에서 음표가 충분히 많은 트랙 중 음이 가장 높은 트랙 = 리드
    - 리드와 거의 같은 타이밍에 같은 라인을 치는 트랙(더블링/에코)도 함께 리드
    예: Pretender 81~88마디 솔로는 Square Lead(ch6)와 Saw Lead(ch11)가 10틱 차이로 같은 라인을 침
    """
    ons = [e for e in events if e[2] == 'on' and e[6] < SIMPLIFY_DROP]
    by_bar = {}
    for e in ons:
        if e[6] != 0:
            by_bar.setdefault(e[0] // ticks_per_bar, {}).setdefault(e[3][:2], []).append((e[0], e[4]))

    # 마디별로 메인 멜로디가 실제로 울리는 시간 비율
    melody_on = {}
    melody_cover = {}
    melody_tail_bars = set()  # 앞 마디의 멜로디 꼬리음이 걸쳐 있는 마디
    for e in sorted(events, key=lambda e: (e[0], e[1])):
        if e[6] == 0 and e[2] == 'on':
            melody_on[e[3]] = e[0]
        elif e[2] == 'off' and e[3] in melody_on:
            start, end = melody_on.pop(e[3]), e[0]
            for bar in range(start // ticks_per_bar + 1, (end - 1) // ticks_per_bar + 1):
                melody_tail_bars.add(bar)
            # 앞 마디에서 이어져 길게 끄는 꼬리음은 '노래 중'으로 치지 않음 (시작한 마디만 계산)
            end = min(end, (start // ticks_per_bar + 1) * ticks_per_bar)
            while start < end:
                bar = start // ticks_per_bar
                seg_end = min(end, (bar + 1) * ticks_per_bar)
                melody_cover[bar] = melody_cover.get(bar, 0) + seg_end - start
                start = seg_end

    leads = {}
    for bar, sources in by_bar.items():
        if melody_cover.get(bar, 0) >= ticks_per_bar * 0.25:
            continue  # 메인 멜로디가 노래하는 마디
        most = max(len(v) for v in sources.values())
        cands = []
        for src, notes in sources.items():
            if len(notes) >= max(2, most * 0.25):
                pitches = sorted(n for _, n in notes)
                cands.append((pitches[len(pitches) // 2], len(notes), src))
        if not cands:
            continue
        best_median, best_count, best = max(cands)
        if bar in melody_tail_bars and best_count < SOLO_MIN_NOTES_PER_BAR:
            continue  # 멜로디가 길게 끄는 중에는 촘촘한 솔로만 끼어들 수 있음
        best_ticks = [t for t, _ in sources[best]]
        chosen = {best}
        for median, _, src in cands:
            if src == best or abs(median - best_median) > 12:
                continue
            notes = sources[src]
            matched = sum(1 for t, _ in notes if any(abs(t - bt) <= tolerance for bt in best_ticks))
            if matched >= 0.8 * len(notes):
                chosen.add(src)  # 같은 라인을 겹쳐 치는 트랙
        leads[bar] = chosen

    promoted = []
    for e in events:
        if e[2] == 'on' and e[6] < SIMPLIFY_DROP and e[3][:2] in leads.get(e[0] // ticks_per_bar, ()):
            e = e[:6] + (1,)
        promoted.append(e)
    return promoted

def reduce_polyphony(events, max_notes, low_first=False, dup_window=0, ticks_per_bar=None):
    """
    events: [(틱, 순서, 'on'/'off', 원본키, 음, 세기, 우선순위)]
    - 같은 순간(dup_window 틱 이내) 같은 음은 한 번만 (여러 트랙 더블링/에코 합치기)
    - 동시에 max_notes개까지만 (우선순위 높은 음 우선, 같은 우선순위면 맨 위/맨 아래 음 우선)
    - 자리가 없으면 더 낮은 우선순위(또는 먼저 눌려 있던 같은 우선순위) 음을 떼고 새 음을 누름
    - 멜로디(우선순위 0)가 울리는 동안 반주는 멜로디보다 높은 음을 치지 않음 (멜로디가 항상 맨 위)
    반환: [(틱, Message)], 버린 음표 수
    """
    out = []
    dropped = 0
    active = {}      # 음 -> (원본키, 우선순위, 누른 틱)
    accepted = {}    # 원본키 -> 음
    events = sorted(events, key=lambda e: (e[0], 0 if e[2] == 'off' else 1, e[1]))

    def order_chord(cands):
        by_tier = {}
        for c in cands:
            by_tier.setdefault(c[6], []).append(c)
        ordered = []
        for tier in sorted(by_tier):
            group = sorted(by_tier[tier], key=lambda c: c[4], reverse=not low_first)
            if len(group) > 2 and not low_first:
                group = [group[0], group[-1]] + group[1:-1]  # 맨 위(멜로디) + 맨 아래(근음) 먼저
            ordered += group
        return ordered

    i = 0
    while i < len(events):
        tick = events[i][0]
        j = i
        while j < len(events) and events[j][0] == tick:
            j += 1
        batch = events[i:j]
        for e in batch:
            if e[2] != 'off':
                continue
            pitch = accepted.pop(e[3], None)
            if pitch is not None and active.get(pitch, (None,))[0] == e[3]:
                del active[pitch]
                out.append((tick, Message('note_off', note=pitch, velocity=0)))
        for c in order_chord([e for e in batch if e[2] == 'on']):
            _, _, _, src, pitch, vel, tier = c
            if tier >= SIMPLIFY_DROP:
                dropped += 1
                continue
            if tier == 1 and ticks_per_bar:
                # 솔로가 시작되면 앞 마디에서 이어져 울리는 멜로디 꼬리음은 뗌
                bar_start = tick - tick % ticks_per_bar
                for p in [p for p, (_, t, since) in active.items() if t == 0 and since < bar_start and p < pitch]:
                    accepted.pop(active[p][0], None)
                    del active[p]
                    out.append((tick, Message('note_off', note=p, velocity=0)))
            melody_pitches = [p for p, (_, t, _) in active.items() if t == 0]
            if tier != 0 and melody_pitches and pitch > min(melody_pitches):
                dropped += 1  # 멜로디보다 높은 반주음
                continue
            if tier == 0:
                # 새 멜로디 음보다 높게 울리고 있는 반주음은 뗌
                for p in [p for p, (_, t, _) in active.items() if t != 0 and p > pitch]:
                    accepted.pop(active[p][0], None)
                    del active[p]
                    out.append((tick, Message('note_off', note=p, velocity=0)))
            if pitch in active:
                if active[pitch][1] == 0 and tier != 0:
                    dropped += 1  # 멜로디가 누르고 있는 음을 반주가 빼앗지 않음
                    continue
                if tick - active[pitch][2] <= dup_window:
                    dropped += 1  # 같은 순간 같은 음 중복
                    continue
                accepted.pop(active[pitch][0], None)
                out.append((tick, Message('note_off', note=pitch, velocity=0)))
                del active[pitch]
            elif max_notes is not None and len(active) >= max_notes:
                victims = [(t, -since, p) for p, (_, t, since) in active.items()
                           if since < tick and (t > tier or t == tier)]
                if not victims:
                    dropped += 1
                    continue
                _, _, vp = max(victims)
                accepted.pop(active[vp][0], None)
                del active[vp]
                out.append((tick, Message('note_off', note=vp, velocity=0)))
            active[pitch] = (src, tier, tick)
            accepted[src] = pitch
            out.append((tick, Message('note_on', note=pitch, velocity=vel)))
        i = j
    return out, dropped

def extract_single_instrument(mid, inst_key, force_ch0=True, align_timeline=True, simplify=False):
    """지정된 단일 악기(drum, guitar, bass, keyboard)를 추출하여 새 MidiFile 객체 생성"""
    cfg = INSTRUMENT_CONFIG[inst_key]
    new_mid = MidiFile(type=1)
    new_mid.ticks_per_beat = mid.ticks_per_beat

    conductor = build_conductor_track(mid)
    if len(conductor) > 0:
        new_mid.tracks.append(conductor)

    total_notes_extracted = 0
    total_mapped = 0
    part_indices = []
    # 채널 10 드럼이 있는 파일이면 트랙 이름만으로 드럼 판정하지 않음
    # (이름 때문에 멜로디 트랙이 드럼 파일에 섞여 들어가는 것 방지)
    drum_by_name = not file_has_drum_channel(mid)

    # 간소화: 모든 트랙의 음표를 모아서 한 트랙으로 정리 (드럼은 제외)
    simplify = simplify and SIMPLIFY_MAX_NOTES.get(inst_key) is not None
    collected = []
    melody_sources = detect_melody_sources(mid, drum_by_name) if simplify and inst_key == 'keyboard' else set()

    for track_idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track, drum_by_name)
        if info['total_notes'] == 0:
            continue

        track_inst = info['instrument']
        # 여러 채널이 섞인 트랙(Type 0 MIDI 등)은 채널별로 악기를 판별
        per_channel = len(info['channels']) > 1
        if not per_channel and track_inst != inst_key:
            continue

        channel_program = {}  # 채널별 현재 프로그램 번호
        drum_chs = info['drum_channels']
        medians = info['channel_medians']

        def channel_matches(ch):
            if not per_channel:
                return True
            if ch in drum_chs:
                return inst_key == 'drum'
            if ch in channel_program:
                inst = classify_program(channel_program[ch])
                if inst == 'bass' and medians.get(ch, 0) > BASS_MAX_MEDIAN_NOTE:
                    inst = 'keyboard'  # 베이스 음색이지만 높은 음역 화음 -> 신스
                return inst == inst_key
            # 프로그램 정보가 없는 채널은 트랙 판별 결과를 따름
            fallback = track_inst if track_inst != 'drum' else 'keyboard'
            return fallback == inst_key

        new_track = MidiTrack()
        mapper = DrumNoteMapper() if cfg['remap_drums'] else None
        abs_tick = 0
        last_written_tick = 0
        has_valid_note = False

        def emit(m):
            nonlocal last_written_tick
            new_track.append(m.copy(time=abs_tick - last_written_tick))
            last_written_tick = abs_tick

        for msg in track:
            abs_tick += msg.time

            if msg.is_meta:
                # 템포 계열은 컨덕터 트랙으로 이동, end_of_track은 저장 시 자동 생성
                if msg.type not in CONDUCTOR_META_TYPES and msg.type != 'end_of_track':
                    emit(msg)
                continue

            if msg.type == 'program_change':
                channel_program[msg.channel] = msg.program

            if not hasattr(msg, 'channel'):
                # sysex 등 채널이 없는 메시지는 단일 악기 트랙에서만 유지
                if not per_channel:
                    emit(msg)
                continue

            if not channel_matches(msg.channel):
                continue

            out_msgs = [msg]
            if msg.type in ('note_on', 'note_off'):
                if mapper is not None:
                    out_msgs = mapper.process(msg, abs_tick)
                    if is_note_on(msg) and get_mapped_drum_note(msg.note) is None:
                        continue  # 버려진 음표는 개수에 포함하지 않음
                    if is_note_on(msg):
                        total_mapped += 1
                if is_note_on(msg):
                    total_notes_extracted += 1
                    has_valid_note = True

            if simplify:
                if msg.type in ('note_on', 'note_off'):
                    prog = channel_program.get(msg.channel, min(info['programs']) if info['programs'] else None)
                    tier = simplify_tier(inst_key, prog, (track_idx, msg.channel) in melody_sources)
                    kind = 'on' if is_note_on(msg) else 'off'
                    collected.append((abs_tick, len(collected), kind, (track_idx, msg.channel, msg.note),
                                      msg.note, getattr(msg, 'velocity', 0), tier))
                continue

            for out in out_msgs:
                if force_ch0:
                    out = out.copy(channel=0)
                emit(out)

        if simplify:
            continue
        if has_valid_note:
            part_indices.append(len(new_mid.tracks))
            new_mid.tracks.append(new_track)

    if simplify and collected:
        dup_window = max(1, mid.ticks_per_beat // 24)
        if inst_key == 'keyboard':
            collected = assign_bar_leads(collected, mid.ticks_per_beat * 4, dup_window)
        reduced, _ = reduce_polyphony(collected, SIMPLIFY_MAX_NOTES[inst_key],
                                      low_first=(inst_key == 'bass'), dup_window=dup_window,
                                      ticks_per_bar=mid.ticks_per_beat * 4)
        total_notes_extracted = sum(1 for _, m in reduced if m.type == 'note_on')
        if total_notes_extracted:
            track = MidiTrack([MetaMessage('track_name', name=cfg['suffix'].strip(' ()'))])
            events = [(0, track[0])] + [(t, m) for t, m in reduced]
            track = to_track(events)
            part_indices.append(len(new_mid.tracks))
            new_mid.tracks.append(track)

    if align_timeline:
        align_to_song_timeline(new_mid, part_indices, song_timeline(mid))

    return new_mid, total_notes_extracted, total_mapped

def strip_known_suffixes(base_name):
    """'곡 (Drum) (Guitar)' 처럼 이미 붙은 악기 접미사를 모두 제거"""
    changed = True
    while changed:
        changed = False
        for cfg in INSTRUMENT_CONFIG.values():
            if base_name.endswith(cfg['suffix']):
                base_name = base_name[:-len(cfg['suffix'])]
                changed = True
    return base_name

def is_generated_file(path):
    """이 프로그램이 만든 결과 파일인지 (폴더 일괄 처리 시 재변환 방지용)"""
    base = os.path.splitext(os.path.basename(path))[0]
    return strip_known_suffixes(base) != base

def repair_smf_bytes(data):
    """
    규격 위반 MIDI 바이트 복구 (파일 길이/구조는 그대로 두고 값만 교정).

    예: Rally go round 의 바이올린 트랙은 피치벤드가 최저값 아래로 내려가면서
        음수 값(f8 ff 등)이 그대로 기록되어 있음.
        - mido는 'data byte must be in range 0..127' 오류로 파일을 거부하고
        - clip=True로 읽어도 f8을 '타이밍 클럭' 메시지로 오인해서 뒤쪽 트랙 전체가 밀려
          곡 길이가 20분으로 늘어나는 등 망가짐.
    러닝 스테이터스 중에 0x80 이상 바이트가 연달아 나오면 상태 바이트가 아닌 깨진 데이터로 간주하고,
    범위를 넘은 데이터는 피치벤드면 최저값(0), 그 외는 127로 교정합니다.
    반환: (복구된 바이트, 교정한 개수)
    """
    buf = bytearray(data)
    fixed = 0
    pos = 14 if buf[:4] == b'MThd' else 0
    if buf[:4] == b'MThd':
        pos = 8 + int.from_bytes(buf[4:8], 'big')

    def read_vlq(i, end):
        value = 0
        while i < end:
            b = buf[i]
            i += 1
            value = (value << 7) | (b & 0x7F)
            if b < 0x80:
                break
        return value, i

    while pos + 8 <= len(buf):
        length = int.from_bytes(buf[pos + 4:pos + 8], 'big')
        start, end = pos + 8, min(pos + 8 + length, len(buf))
        if buf[pos:pos + 4] == b'MTrk':
            i = start
            status = None
            while i < end:
                _, i = read_vlq(i, end)  # delta time
                if i >= end:
                    break
                b = buf[i]
                # 정상 파일에서는 상태 바이트 바로 뒤에 0x80 이상이 올 수 없음(메타 타입/데이터는 0~127).
                # 러닝 스테이터스 중에 0x80 이상이 두 개 연속이면 음수로 기록된 깨진 데이터로 판단
                # (예: f8 ff, e7 fd, d6 fb = 16비트 음수 피치벤드)
                is_running = b < 0x80 or (
                    status is not None and b not in (0xF0, 0xF7)
                    and i + 1 < end and buf[i + 1] > 0x7F
                )
                if not is_running:
                    i += 1
                    if b == 0xFF:  # 메타 메시지
                        i += 1
                        size, i = read_vlq(i, end)
                        i += size
                        continue
                    if b in (0xF0, 0xF7):  # sysex
                        size, i = read_vlq(i, end)
                        i += size
                        continue
                    if b >= 0xF0:
                        continue
                    status = b
                n_data = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
                data_bytes = buf[i:i + n_data]
                if any(x > 0x7F for x in data_bytes):
                    fixed += 1
                    if (status & 0xF0) == 0xE0 and n_data == 2:
                        buf[i] = 0x00      # 음수로 넘어간 피치벤드 -> 최저값
                        buf[i + 1] = 0x00
                    else:
                        for k in range(n_data):
                            if i + k < end and buf[i + k] > 0x7F:
                                buf[i + k] = 0x7F
                i += n_data
        pos = start + length
    return bytes(buf), fixed

def load_midi(input_path):
    """
    MIDI 파일 읽기. 규격 위반(데이터 바이트 > 127) 파일은 자동 복구 후 읽습니다.
    반환: (MidiFile, 복구 여부)
    """
    try:
        return MidiFile(input_path), False
    except (OSError, ValueError) as e:
        if 'data byte' not in str(e):
            raise
    with open(input_path, 'rb') as f:
        data, _ = repair_smf_bytes(f.read())
    return MidiFile(file=io.BytesIO(data)), True

def extract_selected_instruments(input_path, selected_keys, force_ch0=True, simplify=True):
    """
    밴드스코어에서 사용자가 선택한 악기 목록(drum, guitar, bass, keyboard)을 각각 추출하여 저장합니다.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {input_path}")

    mid, repaired = load_midi(input_path)
    if repaired:
        print(f"⚠️ 규격에 맞지 않는 데이터가 있어 자동 복구해서 읽었습니다: {os.path.basename(input_path)}")
    dir_name, file_name = os.path.split(input_path)
    base_name, ext = os.path.splitext(file_name)
    out_base = strip_known_suffixes(base_name)

    results = []
    for inst_key in selected_keys:
        if inst_key not in INSTRUMENT_CONFIG:
            continue
        cfg = INSTRUMENT_CONFIG[inst_key]
        new_mid, total_notes, mapped_notes = extract_single_instrument(mid, inst_key, force_ch0=force_ch0, simplify=simplify)

        # 노트가 1개 이상 추출된 경우에만 파일 저장
        if total_notes > 0:
            output_path = os.path.join(dir_name, f"{out_base}{cfg['suffix']}{ext}")
            # 원본 파일을 덮어쓰지 않음 (드럼 매핑이 이중으로 적용되는 것 방지)
            if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(input_path)):
                print(f"[{cfg['name']}] 원본 파일과 저장 경로가 같아 건너뜁니다: {file_name}")
                continue
            new_mid.save(output_path)
            results.append({
                'key': inst_key,
                'name': cfg['name'],
                'path': output_path,
                'total_notes': total_notes,
                'mapped_notes': mapped_notes,
                'is_drum': cfg['remap_drums']
            })
            print(f"[{cfg['name']}] 추출 완료 ({total_notes}개 음표): {os.path.basename(output_path)}")
        else:
            print(f"[{cfg['name']}] 트랙 또는 음표가 감지되지 않아 건너뜁니다.")

    return results

def print_analysis(input_path):
    """트랙별 판별 결과 출력 (어떤 트랙이 어떤 악기로 분류되는지 확인용)"""
    mid, repaired = load_midi(input_path)
    if repaired:
        print("⚠️ 규격에 맞지 않는 데이터가 있어 자동 복구해서 읽었습니다.")
    drum_by_name = not file_has_drum_channel(mid)
    print(f"파일: {os.path.basename(input_path)} (Type {mid.type}, {len(mid.tracks)}개 트랙, TPB {mid.ticks_per_beat})")
    for idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track, drum_by_name)
        notes = [m.note for m in track if is_note_on(m)]
        note_range = f"{min(notes)}~{max(notes)}" if notes else "-"
        print(f"  [{idx}] {info['name'] or '(이름 없음)'!r}: {info['instrument']} | "
              f"음표 {info['total_notes']}개 (범위 {note_range}) | "
              f"채널 {sorted(info['channels'])} | 프로그램 {sorted(info['programs'])}")

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == '--analyze':
        for arg in sys.argv[2:]:
            print_analysis(arg)
    elif len(sys.argv) > 1:
        print("="*65)
        print("🎸 밴드스코어 악기 선택 추출기 (기타 / 베이스 / 건반 / 드럼)")
        print("   * 드럼: 스타 레조넌스 음계 이동 적용")
        print("   * 기타/베이스/건반: 원본 음계 보존")
        print("="*65)
        for arg in sys.argv[1:]:
            if os.path.exists(arg) and arg.lower().endswith(('.mid', '.midi')):
                print(f"\n처리 중: {os.path.basename(arg)}")
                # CLI 기본값: 4대 악기 모두 추출
                try:
                    extract_selected_instruments(arg, ['drum', 'guitar', 'bass', 'keyboard'])
                except Exception as e:
                    print(f"오류 발생: {e}")
        input("\n[Enter 키를 누르면 종료됩니다...]")
    else:
        try:
            from Convert_Midi_GUI import main as run_gui
        except Exception as e:
            print(f"GUI 실행 중 오류: {e}")
            print("MIDI 파일을 이 스크립트에 드래그 앤 드롭하거나, customtkinter를 설치한 뒤 GUI를 사용해주세요.")
            input("\n[Enter 키를 누르면 종료됩니다...]")
        else:
            run_gui()
