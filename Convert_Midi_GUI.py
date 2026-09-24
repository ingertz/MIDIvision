import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
try:
    import windnd
    HAS_WINDND = True
except ImportError:
    HAS_WINDND = False

# 변환 로직은 Convert_Midi.py 한 곳에서만 관리 (GUI/CLI 결과가 달라지는 문제 방지)
from Convert_Midi import INSTRUMENT_CONFIG, extract_selected_instruments, is_generated_file

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
        self.is_running = False

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
                # force_unicode=True: 한글 등 비 ASCII 경로가 깨지지 않도록 str로 받음
                # 콜백은 윈도우 메시지 처리 중에 호출되므로 after()로 넘겨 Tk 이벤트 루프에서 처리
                drop_cb = lambda files: self.after(0, self.on_drop, list(files))
                try:
                    windnd.hook_dropfiles(self, func=drop_cb, force_unicode=True)
                except TypeError:  # force_unicode 미지원 구버전 windnd
                    windnd.hook_dropfiles(self, func=drop_cb)
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
                midi_files.extend(self.find_midi_files(path))
            elif os.path.isfile(path) and path.lower().endswith(('.mid', '.midi')):
                midi_files.append(path)

        if not midi_files:
            self.log("⚠️ 드롭된 항목 중 .mid 파일을 찾을 수 없습니다.\n")
            return

        self.log(f"📥 드래그 앤 드롭으로 {len(midi_files)}개의 MIDI 파일이 감지되었습니다.\n")
        self.selected_files = midi_files
        self.start_conversion_thread(midi_files)

    @staticmethod
    def find_midi_files(folder):
        """폴더 내 MIDI 파일 검색 (이전에 생성된 '(Drum)' 등 결과 파일은 재변환하지 않도록 제외)"""
        midi_files = []
        for root, _, filenames in os.walk(folder):
            for fn in filenames:
                if fn.lower().endswith(('.mid', '.midi')) and not is_generated_file(fn):
                    midi_files.append(os.path.join(root, fn))
        return midi_files

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
            midi_files = self.find_midi_files(folder)
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
        if self.is_running:
            self.log("⚠️ 이전 추출 작업이 아직 진행 중입니다. 완료 후 다시 시도해주세요.\n")
            return
        selected_keys = self.get_selected_keys()
        if not selected_keys:
            messagebox.showwarning("악기 미선택", "추출할 악기를 하나 이상 선택해주세요 (드럼, 기타, 베이스, 건반).")
            return

        # Tk 위젯은 메인 스레드에서만 접근해야 하므로 작업 스레드 시작 전에 값을 읽고 버튼 상태를 바꿈
        force_ch0 = bool(self.chk_force_ch0.get())
        self.is_running = True
        self.btn_convert_now.configure(state="disabled", text="⏳ 추출 작업 중...")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")
        threading.Thread(target=self.process_files, args=(list(file_list), selected_keys, force_ch0), daemon=True).start()

    def process_files(self, file_list, selected_keys, force_ch0):
        try:
            self._process_files(file_list, selected_keys, force_ch0)
        except Exception as e:
            self.log(f"❌ 예기치 못한 오류: {e}\n")
        finally:
            self.after(0, self.on_conversion_finished)

    def _process_files(self, file_list, selected_keys, force_ch0):
        names = [INSTRUMENT_CONFIG[k]['name'] for k in selected_keys]
        self.log(f"🚀 작업 시작: 선택된 악기 파트 [{', '.join(names)}]\n")

        total_extracted_files = 0
        last_dir = None

        for idx, file_path in enumerate(file_list, 1):
            file_name = os.path.basename(file_path)
            self.log(f"[{idx}/{len(file_list)}] 분석 및 추출 중: {file_name}\n")

            try:
                results = extract_selected_instruments(file_path, selected_keys, force_ch0=force_ch0)
                if results:
                    for res in results:
                        total_extracted_files += 1
                        last_dir = os.path.dirname(res['path'])
                        out_fn = os.path.basename(res['path'])
                        if res['is_drum']:
                            self.log(f" ➔ [{res['name']}] 추출 완료: {out_fn} (음표: {res['total_notes']}개, 건반매핑: {res['mapped_notes']}개)\n")
                        else:
                            self.log(f" ➔ [{res['name']}] 추출 완료: {out_fn} (음표: {res['total_notes']}개, 원음 보존)\n")
                else:
                    self.log(" ⚠️ 선택된 악기의 트랙이나 음표를 찾지 못했습니다.\n")
            except Exception as e:
                self.log(f" ➔ 오류 발생: {e}\n")

        self.log(f"✨ 전체 작업 완료! 총 {total_extracted_files}개의 악기별 분리 파일이 생성되었습니다.\n")
        self.log("="*68 + "\n")

        if last_dir and os.path.exists(last_dir):
            self.last_output_dir = last_dir

    def on_conversion_finished(self):
        self.is_running = False
        if self.last_output_dir:
            self.btn_open_folder.configure(state="normal")
        self.btn_convert_now.configure(state="normal", text="⚡ 선택한 악기 추출 실행")
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
            self.after(0, self.log, text)
            return
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
