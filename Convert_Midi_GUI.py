import os
import sys
import threading
import subprocess
import re
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import mido
from mido import MidiFile, MidiTrack

try:
    import windnd
    HAS_WINDND = True
except ImportError:
    HAS_WINDND = False

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
    79: 72, 80: 72, 81: 72, 82: 77, 83: 77
}

def get_mapped_drum_note(note):
    """드럼 노트 변환 시 미등록 음표가 70(0번키) 등으로 빠져나가지 않도록 완벽 보정"""
    if note in DRUM_MAP:
        return DRUM_MAP[note]
    # 안전 폴백: 범위를 벗어난 알 수 없는 드럼 음표 보정
    if note in (35, 36): return 65       # Kick (F)
    if note in (37, 38, 39, 40): return 72  # Snare (Q)
    if note in (42, 44): return 62       # Closed Hi-Hat (S)
    if note in (46, 74): return 79       # Open Hi-Hat (T)
    if 41 <= note <= 43: return 69       # Floor Tom (H)
    if 45 <= note <= 48: return 74       # Mid Tom (W)
    if note in (50, 58): return 76       # High Tom (E)
    if note in (51, 53, 59): return 81   # Ride (Y)
    if note < 35: return 72              # 27~34 스틱/효과음 -> Q키 (스네어)
    return 77 # R (Crash)

# 악기별 판별 설정 (기타, 베이스, 건반, 드럼)
INSTRUMENT_CONFIG = {
    'drum': {
        'name': '드럼',
        'suffix': ' (Drum)',
        'remap_drums': True,  # 드럼은 스타 레조넌스 음계 이동(건반 매핑) 적용!
        'keywords': ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb'],
        'channel_match': lambda ch: ch == 9,
        'program_match': lambda p: False,
    },
    'guitar': {
        'name': '기타',
        'suffix': ' (Guitar)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 24 <= p <= 31,
    },
    'bass': {
        'name': '베이스',
        'suffix': ' (Bass)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스'],
        'channel_match': lambda ch: ch != 9,
        'program_match': lambda p: 32 <= p <= 39,
    },
    'keyboard': {
        'name': '건반',
        'suffix': ' (Keyboard)',
        'remap_drums': False,  # 멜로디 악기는 원음 유지
        'keywords': ['piano', 'key', 'keyboard', 'synth', 'organ', 'clav', 'rhodes', 'harpsichord', '피아노', '건반', '신스'],
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
        if not re.match(r'^[a-z0-9_-]+$', kw_lower):
            if kw_lower in lower:
                return True
        else:
            pattern = r'(?<![a-z0-9])' + re.escape(kw_lower) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                return True
    return False

def analyze_track_instrument(track):
    """
    트랙의 이름, 프로그램 번호, 채널, 음표 정보를 기반으로 악기 판별:
    - 드럼: 채널 9 또는 드럼 키워드
    - 베이스: 베이스 프로그램(32~39) 또는 베이스 키워드
    - 기타: 기타 프로그램(24~31) 또는 기타 키워드
    - 건반: 위 3개를 제외한 모든 악기(피아노, 색소폰, 브라스, 스트링, 플루트, 보컬 멜로디 등)를 건반으로 배정!
    """
    name = ""
    channels = set()
    programs = set()
    total_notes = 0
    has_tempo = False

    for msg in track:
        if msg.type == 'track_name':
            name = msg.name
        elif msg.type in ['set_tempo', 'time_signature', 'key_signature']:
            has_tempo = True
        elif msg.type == 'program_change':
            programs.add(msg.program)
        elif msg.type in ['note_on', 'note_off']:
            if getattr(msg, 'velocity', 1) > 0:
                total_notes += 1
            if hasattr(msg, 'channel'):
                channels.add(msg.channel)

    if total_notes == 0:
        return {
            'name': name,
            'total_notes': 0,
            'has_tempo': has_tempo,
            'instrument': 'tempo' if has_tempo else 'empty',
            'channels': channels,
            'programs': programs
        }

    # 1. 드럼 판별 (Channel 9 또는 드럼 키워드)
    drum_kw = ['drum', 'drums', 'perc', 'percussion', '드럼', '타악기', 'battery', 'kit', 'cymb']
    if (9 in channels) or match_keyword(name, drum_kw):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'drum',
            'channels': channels,
            'programs': programs
        }

    # 2. 베이스 판별 (베이스 키워드 또는 베이스 프로그램 32~39)
    bass_kw = ['bass', 'eb', 'slap', 'pick bass', 'fingered bass', 'ac bass', 'el bass', '베이스']
    if match_keyword(name, bass_kw) or any(32 <= p <= 39 for p in programs):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'bass',
            'channels': channels,
            'programs': programs
        }

    # 3. 기타 판별 (기타 키워드 또는 기타 프로그램 24~31)
    guitar_kw = ['guitar', 'gt', 'guit', 'eg', 'ag', 'clean', 'dist', 'overdrive', 'lead gt', 'ac gt', 'el gt', '기타']
    if match_keyword(name, guitar_kw) or any(24 <= p <= 31 for p in programs):
        return {
            'name': name,
            'total_notes': total_notes,
            'has_tempo': has_tempo,
            'instrument': 'guitar',
            'channels': channels,
            'programs': programs
        }

    # 4. 그 외 모든 악기 -> 건반(Keyboard)으로 배정!
    # (색소폰, 트럼펫, 브라스, 바이올린, 스트링, 플루트, 오보에, 보컬 멜로디, 피아노, 오르간, 신스 등)
    return {
        'name': name,
        'total_notes': total_notes,
        'has_tempo': has_tempo,
        'instrument': 'keyboard',
        'channels': channels,
        'programs': programs
    }

def extract_single_instrument(mid, inst_key, force_ch0=True):
    """지정된 단일 악기(drum, guitar, bass, keyboard)를 추출하여 새 MidiFile 객체 생성"""
    cfg = INSTRUMENT_CONFIG[inst_key]
    new_mid = MidiFile(type=mid.type)
    new_mid.ticks_per_beat = mid.ticks_per_beat

    target_tracks = []
    tempo_tracks = []

    for idx, track in enumerate(mid.tracks):
        info = analyze_track_instrument(track)
        if info['instrument'] == inst_key and info['total_notes'] > 0:
            target_tracks.append(idx)
        elif info['has_tempo'] and info['total_notes'] == 0:
            tempo_tracks.append(idx)

    fallback_filter = (len(target_tracks) == 0)
    total_notes_extracted = 0
    total_mapped = 0

    for idx, track in enumerate(mid.tracks):
        if idx in tempo_tracks:
            new_mid.tracks.append(track.copy())
            continue

        if idx in target_tracks or fallback_filter:
            new_track = MidiTrack()
            accumulated_time = 0
            has_valid_note = False
            current_program = None

            for msg in track:
                accumulated_time += msg.time

                if msg.is_meta:
                    new_track.append(msg.copy(time=accumulated_time))
                    accumulated_time = 0
                    continue

                if msg.type == 'program_change':
                    current_program = msg.program

                # 폴백 필터링 검사 (단일 트랙에 모든 악기가 섞인 경우)
                is_target_note = False
                if not fallback_filter:
                    is_target_note = True
                else:
                    ch = getattr(msg, 'channel', 0)
                    prog = current_program if current_program is not None else 0
                    if inst_key == 'drum' and ch == 9:
                        is_target_note = True
                    elif inst_key == 'bass' and ch != 9 and (32 <= prog <= 39):
                        is_target_note = True
                    elif inst_key == 'guitar' and ch != 9 and (24 <= prog <= 31):
                        is_target_note = True
                    elif inst_key == 'keyboard' and ch != 9 and not (24 <= prog <= 39):
                        # 색소폰, 관악기, 현악기, 피아노 등 드럼/기타/베이스 제외한 모든 음표
                        is_target_note = True

                if msg.type in ['note_on', 'note_off'] and is_target_note:
                    total_notes_extracted += 1
                    
                    # 드럼인 경우에만 음계 이동 적용!
                    if cfg['remap_drums']:
                        new_note = get_mapped_drum_note(msg.note)
                        total_mapped += 1
                    else:
                        # 기타, 베이스, 건반(색소폰 등 포함)은 원음 유지!
                        new_note = msg.note

                    kwargs = {'note': new_note, 'time': accumulated_time}
                    if force_ch0:
                        kwargs['channel'] = 0
                    new_track.append(msg.copy(**kwargs))
                    accumulated_time = 0
                    has_valid_note = True
                elif msg.type not in ['note_on', 'note_off'] and is_target_note:
                    kwargs = {'time': accumulated_time}
                    if force_ch0 and hasattr(msg, 'channel'):
                        kwargs['channel'] = 0
                    new_track.append(msg.copy(**kwargs))
                    accumulated_time = 0

            if has_valid_note:
                new_mid.tracks.append(new_track)

    return new_mid, total_notes_extracted, total_mapped

def extract_selected_instruments(input_path, selected_keys, force_ch0=True):
    """선택된 악기들을 순회하며 각각 파일로 저장"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {input_path}")

    mid = MidiFile(input_path)
    dir_name, file_name = os.path.split(input_path)
    base_name, ext = os.path.splitext(file_name)

    results = []
    for inst_key in selected_keys:
        if inst_key not in INSTRUMENT_CONFIG:
            continue
        cfg = INSTRUMENT_CONFIG[inst_key]
        new_mid, total_notes, mapped_notes = extract_single_instrument(mid, inst_key, force_ch0=force_ch0)

        if total_notes > 0:
            suffix = cfg['suffix']
            out_base = base_name if not base_name.endswith(suffix) else base_name[:-len(suffix)]
            output_path = os.path.join(dir_name, f"{out_base}{suffix}{ext}")
            new_mid.save(output_path)
            results.append({
                'key': inst_key,
                'name': cfg['name'],
                'path': output_path,
                'total_notes': total_notes,
                'mapped_notes': mapped_notes,
                'is_drum': cfg['remap_drums']
            })

    return results

class MidiConverterGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("스타 레조넌스 밴드스코어 악기 분리 추출기")
        self.geometry("760x750")
        self.minsize(700, 650)

        self.last_output_dir = None
        self.selected_files = []

        self.setup_ui()
        self.setup_dnd()

    def setup_ui(self):
        # 상단 헤더
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(16, 6))

        title_label = ctk.CTkLabel(
            header_frame, 
            text="🎼 밴드스코어 악기 선택 추출기", 
            font=ctk.CTkFont(family="Malgun Gothic", size=22, weight="bold")
        )
        title_label.pack(anchor="w")

        desc_label = ctk.CTkLabel(
            header_frame, 
            text="밴드 MIDI에서 원하는 파트를 선택 추출합니다. 드럼(음계 이동), 기타/베이스(원음 보존), 건반(색소폰·관현악 등 포함 원음 보존)",
            font=ctk.CTkFont(family="Malgun Gothic", size=12),
            text_color="#9AA0A6"
        )
        desc_label.pack(anchor="w", pady=(2, 0))

        # 악기 선택 체크박스 카드
        inst_card = ctk.CTkFrame(self, fg_color="#181B20", corner_radius=12)
        inst_card.pack(fill="x", padx=24, pady=6, ipady=4)

        inst_top_row = ctk.CTkFrame(inst_card, fg_color="transparent")
        inst_top_row.pack(fill="x", padx=16, pady=(8, 4))

        card_title = ctk.CTkLabel(
            inst_top_row,
            text="🎯 추출할 악기 파트 선택 (다중 선택 가능)",
            font=ctk.CTkFont(family="Malgun Gothic", size=13, weight="bold"),
            text_color="#93C5FD"
        )
        card_title.pack(side="left")

        btn_select_all = ctk.CTkButton(
            inst_top_row,
            text="전체 선택",
            width=68,
            height=24,
            font=ctk.CTkFont(family="Malgun Gothic", size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self.select_all_instruments
        )
        btn_select_all.pack(side="right", padx=(4, 0))

        btn_clear_all = ctk.CTkButton(
            inst_top_row,
            text="선택 해제",
            width=68,
            height=24,
            font=ctk.CTkFont(family="Malgun Gothic", size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self.clear_all_instruments
        )
        btn_clear_all.pack(side="right")

        # 체크박스 4개 (드럼, 기타, 베이스, 건반)
        check_row = ctk.CTkFrame(inst_card, fg_color="transparent")
        check_row.pack(fill="x", padx=16, pady=(4, 8))

        self.inst_vars = {
            'drum': tk.BooleanVar(value=True),
            'guitar': tk.BooleanVar(value=True),
            'bass': tk.BooleanVar(value=True),
            'keyboard': tk.BooleanVar(value=True),
        }

        self.chk_drum = ctk.CTkCheckBox(
            check_row,
            text="🥁 드럼 (Drum)\n   └ 음계이동 적용",
            variable=self.inst_vars['drum'],
            font=ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold"),
            text_color="#F8FAFC",
            checkmark_color="#FFFFFF",
            fg_color="#2563EB"
        )
        self.chk_drum.pack(side="left", expand=True, anchor="w", padx=4)

        self.chk_guitar = ctk.CTkCheckBox(
            check_row,
            text="🎸 기타 (Guitar)\n   └ 원음 보존",
            variable=self.inst_vars['guitar'],
            font=ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold"),
            text_color="#F8FAFC",
            checkmark_color="#FFFFFF",
            fg_color="#10B981"
        )
        self.chk_guitar.pack(side="left", expand=True, anchor="w", padx=4)

        self.chk_bass = ctk.CTkCheckBox(
            check_row,
            text="🎸 베이스 (Bass)\n   └ 원음 보존",
            variable=self.inst_vars['bass'],
            font=ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold"),
            text_color="#F8FAFC",
            checkmark_color="#FFFFFF",
            fg_color="#8B5CF6"
        )
        self.chk_bass.pack(side="left", expand=True, anchor="w", padx=4)

        self.chk_keyboard = ctk.CTkCheckBox(
            check_row,
            text="🎹 건반 (Keyboard)\n   └ 색소폰·관현악 전악기",
            variable=self.inst_vars['keyboard'],
            font=ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold"),
            text_color="#F8FAFC",
            checkmark_color="#FFFFFF",
            fg_color="#F59E0B"
        )
        self.chk_keyboard.pack(side="left", expand=True, anchor="w", padx=4)

        # 드래그 앤 드롭 영역
        self.drop_frame = ctk.CTkFrame(
            self, 
            fg_color="#1E222B", 
            border_color="#3B82F6", 
            border_width=2, 
            corner_radius=14
        )
        self.drop_frame.pack(fill="x", padx=24, pady=6, ipady=8)

        drop_icon = ctk.CTkLabel(
            self.drop_frame, 
            text="📥", 
            font=ctk.CTkFont(size=34)
        )
        drop_icon.pack(pady=(4, 0))

        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text="여기에 MIDI 파일(.mid)을 끌어다 놓으세요 (드래그 & 드롭)",
            font=ctk.CTkFont(family="Malgun Gothic", size=15, weight="bold"),
            text_color="#E2E8F0"
        )
        self.drop_label.pack(pady=2)

        btn_container = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        btn_container.pack(pady=(6, 4))

        self.btn_select_files = ctk.CTkButton(
            btn_container,
            text="📁 MIDI 파일 직접 선택",
            font=ctk.CTkFont(family="Malgun Gothic", size=13, weight="bold"),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            height=36,
            corner_radius=8,
            command=self.select_files
        )
        self.btn_select_files.pack(side="left", padx=6)

        self.btn_select_folder = ctk.CTkButton(
            btn_container,
            text="📂 폴더 일괄 선택",
            font=ctk.CTkFont(family="Malgun Gothic", size=13),
            fg_color="#334155",
            hover_color="#475569",
            height=36,
            corner_radius=8,
            command=self.select_folder
        )
        self.btn_select_folder.pack(side="left", padx=6)

        # 건반 매핑 및 옵션 안내 카드
        info_frame = ctk.CTkFrame(self, fg_color="#181B20", corner_radius=10)
        info_frame.pack(fill="x", padx=24, pady=4, ipady=2)

        self.chk_force_ch0 = ctk.CTkCheckBox(
            info_frame,
            text="인게임 악기 인식을 위해 채널을 0(Channel 1)으로 통일 (필수 권장)",
            font=ctk.CTkFont(family="Malgun Gothic", size=11),
            onvalue=True,
            offvalue=False
        )
        self.chk_force_ch0.select()
        self.chk_force_ch0.pack(anchor="w", padx=14, pady=(6, 3))

        mapping_title = ctk.CTkLabel(
            info_frame,
            text="🎹 스타 레조넌스 드럼 음계 이동 키 매핑 (드럼만 적용됨)",
            font=ctk.CTkFont(family="Malgun Gothic", size=11, weight="bold"),
            text_color="#93C5FD"
        )
        mapping_title.pack(anchor="w", padx=14, pady=(0, 1))

        mapping_text = (
            "• 킥: F4 [ F ]    • 스네어: C5 [ Q ]    • 하이햇: D4 [ S ], 오픈: G5 [ T ]\n"
            "• 탐: 미드 D5 [ W ], 하이 E5 [ E ], 플로어 A4 [ H ] (★ 0번키 방지 정밀 매핑)\n"
            "• 심벌: 크래시 F5 [ R ], 라이드 A5 [ Y ]"
        )
        mapping_label = ctk.CTkLabel(
            info_frame,
            text=mapping_text,
            font=ctk.CTkFont(family="Malgun Gothic", size=10),
            text_color="#94A3B8",
            justify="left"
        )
        mapping_label.pack(anchor="w", padx=14, pady=(0, 6))

        # 액션 바
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.pack(fill="x", padx=24, pady=4)

        self.btn_convert_now = ctk.CTkButton(
            action_bar,
            text="⚡ 선택한 악기 추출 실행",
            font=ctk.CTkFont(family="Malgun Gothic", size=13, weight="bold"),
            fg_color="#10B981",
            hover_color="#059669",
            height=38,
            corner_radius=8,
            command=self.convert_selected
        )
        self.btn_convert_now.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_open_folder = ctk.CTkButton(
            action_bar,
            text="📂 저장 폴더 열기",
            font=ctk.CTkFont(family="Malgun Gothic", size=13),
            fg_color="#4B5563",
            hover_color="#374151",
            height=38,
            corner_radius=8,
            command=self.open_output_folder,
            state="disabled"
        )
        self.btn_open_folder.pack(side="left", padx=(0, 6))

        self.btn_clear_log = ctk.CTkButton(
            action_bar,
            text="🧹 로그 지우기",
            font=ctk.CTkFont(family="Malgun Gothic", size=12),
            fg_color="#374151",
            hover_color="#1F2937",
            width=80,
            height=38,
            corner_radius=8,
            command=self.clear_logs
        )
        self.btn_clear_log.pack(side="left")

        # 로그 텍스트 박스
        self.log_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#111317",
            text_color="#E2E8F0",
            wrap="word",
            corner_radius=10
        )
        self.log_box.pack(fill="both", expand=True, padx=24, pady=(4, 16))

        self.log("✅ 프로그램이 준비되었습니다.\n")
        self.log("💡 상단에서 추출할 악기(드럼/기타/베이스/건반)를 체크한 뒤 MIDI 파일을 드래그하세요.\n")
        self.log("   * 드럼: 스타 레조넌스 인게임 건반 노트로 자동 이동\n")
        self.log("   * 기타/베이스/건반: 원본 음계 그대로 보존\n" + "-"*68 + "\n")

    def select_all_instruments(self):
        for v in self.inst_vars.values():
            v.set(True)

    def clear_all_instruments(self):
        for v in self.inst_vars.values():
            v.set(False)

    def setup_dnd(self):
        if HAS_WINDND:
            try:
                windnd.hook_dropfiles(self, func=self.on_drop)
            except Exception as e:
                self.log(f"⚠️ 드래그 앤 드롭 후킹 알림: {e}\n")
        else:
            self.log("⚠️ windnd 모듈 미탑재로 파일 선택 버튼을 사용해 주세요.\n")

    def get_selected_keys(self):
        return [k for k, v in self.inst_vars.items() if v.get()]

    def on_drop(self, files):
        decoded_files = []
        for item in files:
            if isinstance(item, bytes):
                try:
                    path = item.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        path = item.decode('cp949')
                    except UnicodeDecodeError:
                        path = item.decode(sys.getfilesystemencoding(), errors='replace')
            else:
                path = str(item)
            decoded_files.append(path)

        midi_files = []
        for path in decoded_files:
            if os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for fn in filenames:
                        if fn.lower().endswith(('.mid', '.midi')):
                            midi_files.append(os.path.join(root, fn))
            elif os.path.isfile(path) and path.lower().endswith(('.mid', '.midi')):
                midi_files.append(path)

        if not midi_files:
            self.log("⚠️ 드롭된 항목 중 .mid 파일을 찾을 수 없습니다.\n")
            return

        self.log(f"📥 드래그 앤 드롭으로 {len(midi_files)}개의 MIDI 파일이 감지되었습니다.\n")
        self.selected_files = midi_files
        self.start_conversion_thread(midi_files)

    def select_files(self):
        files = filedialog.askopenfilenames(
            title="변환할 MIDI 파일들을 선택하세요",
            filetypes=[("MIDI Files", "*.mid *.midi"), ("All Files", "*.*")]
        )
        if files:
            self.selected_files = list(files)
            self.log(f"📁 {len(self.selected_files)}개의 파일이 선택되었습니다.\n")
            self.start_conversion_thread(self.selected_files)

    def select_folder(self):
        folder = filedialog.askdirectory(title="MIDI 파일이 있는 폴더를 선택하세요")
        if folder:
            midi_files = []
            for root, _, filenames in os.walk(folder):
                for fn in filenames:
                    if fn.lower().endswith(('.mid', '.midi')):
                        midi_files.append(os.path.join(root, fn))
            if midi_files:
                self.selected_files = midi_files
                self.log(f"📂 폴더 내 {len(midi_files)}개의 MIDI 파일이 감지되었습니다.\n")
                self.start_conversion_thread(midi_files)
            else:
                self.log(f"⚠️ 선택한 폴더에 .mid 파일이 없습니다: {folder}\n")

    def convert_selected(self):
        if not self.selected_files:
            self.select_files()
        else:
            self.start_conversion_thread(self.selected_files)

    def start_conversion_thread(self, file_list):
        selected_keys = self.get_selected_keys()
        if not selected_keys:
            messagebox.showwarning("악기 미선택", "추출할 악기를 하나 이상 선택해주세요 (드럼, 기타, 베이스, 건반).")
            return
        threading.Thread(target=self.process_files, args=(file_list, selected_keys), daemon=True).start()

    def process_files(self, file_list, selected_keys):
        self.btn_convert_now.configure(state="disabled", text="⏳ 추출 작업 중...")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")

        force_ch0 = self.chk_force_ch0.get()
        names = [INSTRUMENT_CONFIG[k]['name'] for k in selected_keys]
        self.log(f"🚀 작업 시작: 선택된 악기 파트 [{', '.join(names)}]\n")

        total_extracted_files = 0
        last_dir = None

        for idx, file_path in enumerate(file_list, 1):
            file_name = os.path.basename(file_path)
            self.log(f"[{idx}/{len(file_list)}] 분석 및 추출 중: {file_name}")

            try:
                results = extract_selected_instruments(file_path, selected_keys, force_ch0=force_ch0)
                if results:
                    for res in results:
                        total_extracted_files += 1
                        last_dir = os.path.dirname(res['path'])
                        out_fn = os.path.basename(res['path'])
                        if res['is_drum']:
                            self.log(f" ➔ [{res['name']}] 추출 완료: {out_fn} (음표: {res['total_notes']}개, 건반매핑: {res['mapped_notes']}개)")
                        else:
                            self.log(f" ➔ [{res['name']}] 추출 완료: {out_fn} (음표: {res['total_notes']}개, 원음 보존)")
                else:
                    self.log(" ⚠️ 선택된 악기의 트랙이나 음표를 찾지 못했습니다.")
                self.log("\n")
            except Exception as e:
                self.log(f" ➔ 오류 발생: {e}\n")

        self.log(f"✨ 전체 작업 완료! 총 {total_extracted_files}개의 악기별 분리 파일이 생성되었습니다.\n")
        self.log("="*68 + "\n")

        if last_dir and os.path.exists(last_dir):
            self.last_output_dir = last_dir
            self.btn_open_folder.configure(state="normal")

        self.btn_convert_now.configure(state="normal", text="⚡ 선택한 악기 추출 실행")
        self.btn_select_files.configure(state="normal")
        self.btn_select_folder.configure(state="normal")

    def open_output_folder(self):
        if self.last_output_dir and os.path.exists(self.last_output_dir):
            os.startfile(self.last_output_dir)
        else:
            messagebox.showinfo("알림", "저장된 폴더를 찾을 수 없습니다.")

    def clear_logs(self):
        self.log_box.delete("1.0", "end")
        self.log("기록이 초기화되었습니다.\n")

    def log(self, text):
        self.log_box.insert("end", text)
        self.log_box.see("end")

def main():
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        midi_files = [f for f in args if f.lower().endswith(('.mid', '.midi'))]
        if midi_files:
            app = MidiConverterGUI()
            app.after(300, lambda: app.on_drop(midi_files))
            app.mainloop()
            return

    app = MidiConverterGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
