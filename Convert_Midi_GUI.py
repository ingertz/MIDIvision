import os
import sys
import queue
import threading
import traceback
import subprocess

def _app_dir():
    """exe로 실행하면 exe가 있는 폴더, 아니면 이 스크립트가 있는 폴더"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

ERROR_LOG = os.path.join(_app_dir(), 'Convert_Midi_error.log')

def report_fatal_error(exc_type, exc, tb):
    """
    exe(--windowed)는 콘솔이 없어서 오류가 나면 아무 말 없이 꺼집니다.
    오류 내용을 exe 옆 Convert_Midi_error.log에 저장하고 메시지 창으로도 보여줍니다.
    """
    text = ''.join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"오류가 발생했습니다.\n\n{text[-1500:]}\n\n저장 위치: {ERROR_LOG}", "Convert_Midi 오류", 0x10)
    except Exception:
        print(text, file=sys.stderr)

sys.excepthook = report_fatal_error

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import customtkinter as ctk
    # 변환 로직은 Convert_Midi.py 한 곳에서만 관리 (GUI/CLI 결과가 달라지는 문제 방지)
    from Convert_Midi import (INPUT_EXTS, MIDI_EXTS, SCORE_EXTS, AUDIO_EXTS,
                              convert_file, is_generated_file)
except Exception:
    report_fatal_error(*sys.exc_info())
    sys.exit(1)

# 드래그 앤 드롭: tkinterdnd2 (Tk 공식 확장 tkdnd 사용)
# ※ 예전에 쓰던 windnd는 윈도우 메시지 처리기를 가로채는 방식이라
#   Python 3.12 이상에서 "Fatal Python error: PyEval_RestoreThread"로 프로그램이 통째로 꺼짐
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
    _GUI_BASES = (ctk.CTk, TkinterDnD.DnDWrapper)
except ImportError:
    HAS_DND = False
    _GUI_BASES = (ctk.CTk,)

class MidiConverterGUI(*_GUI_BASES):
    def __init__(self):
        super().__init__()
        self.dnd_ready = False
        if HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_ready = True
            except Exception:
                pass

        # 작업 스레드 -> 메인 스레드 전달용 (Tk 위젯은 메인 스레드에서만 건드려야 안전)
        self.ui_queue = queue.Queue()
        self.after(50, self._drain_ui_queue)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("스타 레조넌스 합본 MIDI 변환기 (드럼 매핑)")
        self.geometry("760x680")
        self.minsize(680, 560)

        self.last_output_dir = None
        self.selected_files = []
        self.is_running = False

        self.setup_ui()
        self.setup_dnd()

    def report_callback_exception(self, exc_type, exc, tb):
        # 버튼 클릭/드래그 처리 중 오류도 로그에 남김 (프로그램은 계속 실행)
        report_fatal_error(exc_type, exc, tb)

    def setup_ui(self):
        # 상단 헤더
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(16, 6))

        title_label = ctk.CTkLabel(
            header_frame, 
            text="🎼 스타 레조넌스 합본 MIDI 변환기", 
            font=ctk.CTkFont(family="Malgun Gothic", size=22, weight="bold")
        )
        title_label.pack(anchor="w")

        desc_label = ctk.CTkLabel(
            header_frame, 
            text="원본 트랙·채널 구조는 그대로 유지하고, 드럼만 인게임 건반 음계로 변환하여 합본 MIDI(곡명_converted.mid)로 저장합니다.",
            font=ctk.CTkFont(family="Malgun Gothic", size=12),
            text_color="#9AA0A6"
        )
        desc_label.pack(anchor="w", pady=(2, 0))

        # 드래그 앤 드롭 영역
        self.drop_frame = ctk.CTkFrame(
            self, 
            fg_color="#1E222B", 
            border_color="#3B82F6", 
            border_width=2, 
            corner_radius=14
        )
        self.drop_frame.pack(fill="x", padx=24, pady=8, ipady=10)

        drop_icon = ctk.CTkLabel(
            self.drop_frame, 
            text="📥", 
            font=ctk.CTkFont(size=34)
        )
        drop_icon.pack(pady=(4, 0))

        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text="여기에 MIDI(.mid), 악보(.pdf / .mxl), 음원(.mp3) 파일을 끌어다 놓으세요 (드래그 & 드롭)",
            font=ctk.CTkFont(family="Malgun Gothic", size=15, weight="bold"),
            text_color="#E2E8F0"
        )
        self.drop_label.pack(pady=2)

        btn_container = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        btn_container.pack(pady=(6, 4))

        self.btn_select_files = ctk.CTkButton(
            btn_container,
            text="📁 MIDI / 악보 파일 직접 선택",
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
        info_frame.pack(fill="x", padx=24, pady=4, ipady=4)

        bpm_row = ctk.CTkFrame(info_frame, fg_color="transparent")
        bpm_row.pack(anchor="w", padx=14, pady=(8, 4))
        ctk.CTkLabel(
            bpm_row,
            text="악보(PDF) 템포 BPM:",
            font=ctk.CTkFont(family="Malgun Gothic", size=11)
        ).pack(side="left")
        self.entry_score_bpm = ctk.CTkEntry(
            bpm_row, width=60, height=24, placeholder_text="자동",
            font=ctk.CTkFont(family="Malgun Gothic", size=11)
        )
        self.entry_score_bpm.pack(side="left", padx=6)
        ctk.CTkLabel(
            bpm_row,
            text="비우면 악보의 ♩= 숫자를 자동으로 읽음 (못 읽으면 120)",
            font=ctk.CTkFont(family="Malgun Gothic", size=10),
            text_color="#94A3B8"
        ).pack(side="left")

        mapping_title = ctk.CTkLabel(
            info_frame,
            text="🥁 스타 레조넌스 드럼 음계 이동 키 매핑 (드럼 트랙·채널만 자동 적용, 나머지 악기는 원음·채널 유지)",
            font=ctk.CTkFont(family="Malgun Gothic", size=11, weight="bold"),
            text_color="#93C5FD"
        )
        mapping_title.pack(anchor="w", padx=14, pady=(2, 1))

        mapping_text = (
            "• 킥: F4 [ F ]    • 스네어: C5 [ Q ]    • 하이햇: D4 [ S ], 오픈: G5 [ T ]\n"
            "• 탐: 미드 D5 [ W ], 하이 E5 [ E ], 플로어 A4 [ H ] (★ 0번키 방지 정밀 매핑)\n"
            "• 심벌: 라이드 F5 [ R ], 크래시 A5 [ Y ]"
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
            text="⚡ 드럼 변환 및 합본 MIDI 생성",
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
        self.log("💡 MIDI / 악보 / 음원 파일을 드래그하면 드럼만 인게임용으로 수정된 합본 MIDI(곡명_converted.mid)를 생성합니다.\n")
        self.log("   * 드럼: 스타 레조넌스 인게임 건반 노트로 자동 이동\n")
        self.log("   * 기타/베이스/건반 등: 원본 트랙·채널·음계 그대로 보존\n" + "-"*68 + "\n")

    def setup_dnd(self):
        if self.dnd_ready:
            self.drop_target_register(DND_FILES)
            self.dnd_bind('<<Drop>>', self._on_dnd_drop)
        else:
            self.log("⚠️ tkinterdnd2 모듈이 없어 드래그 앤 드롭을 쓸 수 없습니다. 파일 선택 버튼을 사용해 주세요.\n")

    def _on_dnd_drop(self, event):
        # 공백/한글이 들어간 경로는 {C:/내 폴더/곡.mid} 형태로 오므로 splitlist로 분리
        self.on_drop(list(self.tk.splitlist(event.data)))
        return event.action

    def call_in_main(self, func, *args):
        """작업 스레드에서 화면을 바꿔야 할 때 사용 (메인 스레드가 큐를 꺼내서 실행)"""
        self.ui_queue.put((func, args))

    def _drain_ui_queue(self):
        try:
            while True:
                func, args = self.ui_queue.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        self.after(50, self._drain_ui_queue)

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
                midi_files.extend(self.find_midi_files(path))
            elif os.path.isfile(path) and path.lower().endswith(INPUT_EXTS):
                midi_files.append(path)

        if not midi_files:
            self.log("⚠️ 드롭된 항목 중 MIDI(.mid) / 악보(.pdf, .mxl) / 음원(.mp3) 파일을 찾을 수 없습니다.\n")
            return

        self.log(f"📥 드래그 앤 드롭으로 {len(midi_files)}개의 파일이 감지되었습니다.\n")
        self.selected_files = midi_files
        self.start_conversion_thread(midi_files)

    @staticmethod
    def find_midi_files(folder):
        """
        폴더 내 MIDI / 악보 파일 검색 (이전에 생성된 '_converted' 등 결과 파일은 재변환하지 않도록 제외)
        같은 이름의 MIDI가 있는 악보는 제외 (둘 다 변환하면 결과 파일이 서로 덮어씀)
        """
        midi_files = []
        for root, _, filenames in os.walk(folder):
            midi_names = {os.path.splitext(fn)[0].lower() for fn in filenames if fn.lower().endswith(MIDI_EXTS)}
            for fn in filenames:
                if not fn.lower().endswith(INPUT_EXTS) or is_generated_file(fn):
                    continue
                if fn.lower().endswith(SCORE_EXTS + AUDIO_EXTS) and os.path.splitext(fn)[0].lower() in midi_names:
                    continue
                midi_files.append(os.path.join(root, fn))
        return midi_files

    def select_files(self):
        files = filedialog.askopenfilenames(
            title="변환할 MIDI / 악보 파일들을 선택하세요",
            filetypes=[("MIDI / 악보 / 음원", "*.mid *.midi *.pdf *.mxl *.musicxml " + " ".join("*" + e for e in AUDIO_EXTS)),
                       ("MIDI Files", "*.mid *.midi"), ("악보 (PDF / MusicXML)", "*.pdf *.mxl *.musicxml"),
                       ("음원", " ".join("*" + e for e in AUDIO_EXTS)), ("All Files", "*.*")]
        )
        if files:
            self.selected_files = list(files)
            self.log(f"📁 {len(self.selected_files)}개의 파일이 선택되었습니다.\n")
            self.start_conversion_thread(self.selected_files)

    def select_folder(self):
        folder = filedialog.askdirectory(title="MIDI / 악보 파일이 있는 폴더를 선택하세요")
        if folder:
            midi_files = self.find_midi_files(folder)
            if midi_files:
                self.selected_files = midi_files
                self.log(f"📂 폴더 내 {len(midi_files)}개의 파일이 감지되었습니다.\n")
                self.start_conversion_thread(midi_files)
            else:
                self.log(f"⚠️ 선택한 폴더에 MIDI / 악보 파일이 없습니다: {folder}\n")

    def convert_selected(self):
        if not self.selected_files:
            self.select_files()
        else:
            self.start_conversion_thread(self.selected_files)

    def start_conversion_thread(self, file_list):
        if self.is_running:
            self.log("⚠️ 이전 변환 작업이 아직 진행 중입니다. 완료 후 다시 시도해주세요.\n")
            return

        # Tk 위젯은 메인 스레드에서만 접근해야 하므로 작업 스레드 시작 전에 값을 읽고 버튼 상태를 바꿈
        score_bpm = self.get_score_bpm()
        self.is_running = True
        self.btn_convert_now.configure(state="disabled", text="⏳ 변환 작업 중...")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")
        threading.Thread(target=self.process_files, args=(list(file_list), score_bpm), daemon=True).start()

    def get_score_bpm(self):
        """템포 칸에 적힌 BPM (비었거나 잘못된 값이면 None)"""
        text = self.entry_score_bpm.get().strip()
        if not text:
            return None
        try:
            bpm = float(text)
        except ValueError:
            bpm = 0
        if not 20 <= bpm <= 400:
            self.log(f"⚠️ 템포 칸의 값({text})은 쓸 수 없어 무시합니다. (20~400 사이 숫자)\n")
            return None
        return bpm

    def process_files(self, file_list, score_bpm):
        try:
            self._process_files(file_list, score_bpm)
        except Exception as e:
            self.log(f"❌ 예기치 못한 오류: {e}\n")
        finally:
            self.call_in_main(self.on_conversion_finished)

    def _process_files(self, file_list, score_bpm):
        self.log("🚀 합본 MIDI 변환 작업 시작 (드럼 인게임 음계 매핑)\n")

        total_converted_files = 0
        last_dir = None

        for idx, file_path in enumerate(file_list, 1):
            file_name = os.path.basename(file_path)
            self.log(f"[{idx}/{len(file_list)}] 변환 중: {file_name}\n")

            try:
                res = convert_file(file_path, log=lambda text: self.log(text + "\n"), score_bpm=score_bpm)
                if res:
                    total_converted_files += 1
                    last_dir = os.path.dirname(res['path'])
                    out_fn = os.path.basename(res['path'])
                    self.log(f" ➔ 합본 생성 완료: {out_fn} (전체 음표: {res['total_notes']}개, 드럼 매핑: {res['mapped_notes']}개)\n")
                else:
                    self.log(" ⚠️ 변환할 음표를 찾지 못했거나 원본 파일과 경로가 같아 건너뛰었습니다.\n")
            except Exception as e:
                self.log(f" ➔ 오류 발생: {e}\n")

        self.log(f"✨ 전체 작업 완료! 총 {total_converted_files}개의 합본 MIDI 파일이 생성되었습니다.\n")
        self.log("="*68 + "\n")

        if last_dir and os.path.exists(last_dir):
            self.last_output_dir = last_dir

    def on_conversion_finished(self):
        self.is_running = False
        if self.last_output_dir:
            self.btn_open_folder.configure(state="normal")
        self.btn_convert_now.configure(state="normal", text="⚡ 드럼 변환 및 합본 MIDI 생성")
        self.btn_select_files.configure(state="normal")
        self.btn_select_folder.configure(state="normal")

    def open_output_folder(self):
        if self.last_output_dir and os.path.exists(self.last_output_dir):
            if sys.platform.startswith('win'):
                os.startfile(self.last_output_dir)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', self.last_output_dir])
            else:
                subprocess.Popen(['xdg-open', self.last_output_dir])
        else:
            messagebox.showinfo("알림", "저장된 폴더를 찾을 수 없습니다.")

    def clear_logs(self):
        self.log_box.delete("1.0", "end")
        self.log("기록이 초기화되었습니다.\n")

    def log(self, text):
        # 작업 스레드에서 호출되어도 안전하도록 메인 스레드에서 위젯 갱신
        if threading.current_thread() is not threading.main_thread():
            self.call_in_main(self.log, text)
            return
        self.log_box.insert("end", text)
        self.log_box.see("end")

def main():
    try:
        _main()
    except Exception:
        report_fatal_error(*sys.exc_info())

def _main():
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        midi_files = [f for f in args if f.lower().endswith(INPUT_EXTS)]
        if midi_files:
            app = MidiConverterGUI()
            app.after(300, lambda: app.on_drop(midi_files))
            app.mainloop()
            return

    app = MidiConverterGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
