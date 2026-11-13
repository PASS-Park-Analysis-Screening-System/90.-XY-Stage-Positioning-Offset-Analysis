from PySide6.QtWidgets import QMessageBox, QFileDialog
from PySide6.QtCore import QTimer
import datetime, os, re, threading
from core.exporter import (export_combined_csv, export_excel_report,
                           export_quality_report, collect_anomalies)
from core import compute_repeatability, compute_trend, filter_stabilization_die
from core.recipe_scanner import compare_recipes
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class ExportMixin:
    # QFileDialog.getSaveFileName(parent, caption, dir, filter): 기본 파일 이름은 dir 자리에 준다.
    # 5번째 인자는 선택 필터라 거기 이름을 넣으면 무시된다.
    def _export_csv(self):
        if not self.raw_data:
            QMessageBox.warning(self, "경고", "먼저 분석을 실행해주세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV", "Analysis.txt", "Text (*.txt);;CSV (*.csv)")
        if path:
            try:
                export_combined_csv(self.raw_data, path)
                self.logger.ok(f"CSV 저장: {path}")
            except Exception as e:
                self.logger.error(f"CSV 오류: {e}")
                QMessageBox.critical(self, "오류", f"CSV 저장 실패\n{e}")


    def _export_excel(self):
        if not self.raw_data:
            QMessageBox.warning(self, "경고", "먼저 분석을 실행해주세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Excel", "Report.xlsx", "Excel (*.xlsx)")
        if path:
            try:
                # 통계·트렌드는 분석 기준(21 Die)으로 고정한다. 화면의 Die 필터 선택과는
                # 무관하다. Raw Data 시트만 22 Die를 유지하고 분석 대상 열로 구분한다.
                analysis = filter_stabilization_die(self.raw_data)
                stats = compute_repeatability(analysis)
                trend = compute_trend(analysis)
                # Raw Data/통계는 선택된 Step, 체크리스트·품질·로그 시트는 전체 Recipe 기준
                export_excel_report(
                    self.raw_data, stats, trend, path,
                    recipe_results=list(getattr(self, 'recipe_results', None) or []),
                    spec_limits=self.settings.get('spec_limits', {}),
                    log_rows=self.logger.export_rows(),
                    spec_deviation=self.settings.get('spec_deviation', {}))
                self.logger.ok(f"Excel 저장: {path}")
            except Exception as e:
                self.logger.error(f"Excel 오류: {e}")
                QMessageBox.critical(self, "오류", f"Excel 저장 실패\n{e}")


    def _export_quality_report(self):
        """측정 실패·이상치·Spec 초과 + System Log 전용 리포트 (전체 Recipe)."""
        # 내보내는 동안 회차를 바꿔도 섞이지 않게 지금 목록을 복사해 쓴다
        results = list(getattr(self, 'recipe_results', None) or [])
        if not results:
            QMessageBox.warning(self, "경고", "먼저 분석을 실행해주세요.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Quality Report", "QualityReport.xlsx", "Excel (*.xlsx)")
        if not path:
            return

        try:
            spec_limits = self.settings.get('spec_limits', {})
            n = len(collect_anomalies(results, spec_limits))
            export_quality_report(results, spec_limits,
                                  self.logger.export_rows(), path)
            self.logger.ok(f"Quality Report 저장: {path} (이상 {n}건)")
            os.startfile(path)
        except Exception as e:
            self.logger.error(f"Quality Report 오류: {e}")
            QMessageBox.critical(self, "오류", f"Quality Report 생성 실패\n{e}")


    def _export_pdf(self):
        # 작업 스레드가 쓰는 동안 회차 전환이 목록을 바꾸지 않게 메인 스레드에서 복사한다
        results = list(getattr(self, 'recipe_results', None) or [])
        if not results:
            QMessageBox.warning(self, "경고", "스캔된 데이터가 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "PDF", "Report.pdf", "PDF (*.pdf)")
        if not path:
            return
        self.logger.info("PDF 리포트 생성 중...")
        outcome = []   # 작업 스레드가 결과를 넣고 메인 스레드가 꺼낸다

        # 화면과 같은 결과로 만든다. 폴더를 다시 읽으면 경로 입력란이나 폴더 내용이
        # 바뀐 경우 화면·Excel과 다른 값이 나간다. 위젯 값은 여기 메인 스레드에서 읽는다.
        root = getattr(self, '_scanned_folder', '') or self.path_edit.text()
        spec_limits = self.settings.get('spec_limits', {})
        spec_deviation = self.settings.get('spec_deviation', {})

        def run():
            # 실패해도 불완전한 PDF가 대상 경로에 남지 않게 임시 파일에 쓰고 옮긴다
            temp_path = path + '.part'
            try:
                from core.pdf_generator import generate_pdf_report
                generate_pdf_report(temp_path, root, results, compare_recipes(results),
                                    spec_limits, spec_deviation)
                os.replace(temp_path, path)
                outcome.append(None)
            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                outcome.append(e)

        # 일반 스레드에는 이벤트 루프가 없어 QTimer.singleShot이 실행되지 않는다.
        # 결과를 메인 스레드에서 확인해야 실패가 사용자에게 보인다.
        poll = QTimer(self)

        def check():
            if not outcome:
                return
            poll.stop()
            poll.deleteLater()
            if outcome[0] is None:
                self.logger.ok(f"PDF 저장 완료: {path}")
                try:
                    os.startfile(path)
                except OSError as e:
                    self.logger.warn(f"PDF 열기 실패: {e}")
            else:
                self.logger.error(f"PDF 오류: {outcome[0]}")
                QMessageBox.critical(self, "오류", f"PDF 생성 실패\n{outcome[0]}")

        poll.timeout.connect(check)
        poll.start(200)
        threading.Thread(target=run, daemon=True).start()



    def _export_llm_markdown(self, parent=None):
        """LLM 원인 분석용 Markdown (전체 Workflow, 회차 비교·부록은 모든 회차).

        parent: 파일 대화상자·메시지의 부모 (전문 분석 창 '도구' 탭은 그 창). 기본은 이 창."""
        parent = self if parent is None else parent
        # 내보내는 동안 회차를 바꿔도 섞이지 않게 메인 스레드에서 목록을 복사한다
        results = list(getattr(self, 'recipe_results', None) or [])
        if not results:
            QMessageBox.warning(parent, "경고", "스캔된 데이터가 없습니다.")
            return
        root = getattr(self, '_scanned_folder', '') or self.path_edit.text()
        now = datetime.datetime.now()
        names = [r.get('short_name') or r.get('recipe', '') for r in results]
        label = names[0] if len(names) == 1 else (os.path.basename(os.path.normpath(root)) or 'root')
        # 파일 이름에 쓸 수 없는 문자와 공백은 _로 바꾼다
        label = re.sub(r'[\\/:*?"<>|\s]+', '_', label).strip('_') or 'root'
        path, _ = QFileDialog.getSaveFileName(
            parent, "AI 분석용 Markdown", f"XY_LLM_{label}_{now:%Y%m%d_%H%M}.md",
            "Markdown (*.md)")
        if not path:
            return
        self.logger.info("AI 분석용 Markdown 생성 중...")
        outcome = []
        spec_limits = self.settings.get('spec_limits', {})
        spec_deviation = self.settings.get('spec_deviation', {})
        from core.settings import rotation_spec
        rot_spec = rotation_spec(self.settings)
        # 스캐너 회전 시험 결과는 장비의 것 (회차와 무관). 내보내는 동안 바뀌어도 섞이지 않게 사본
        import copy
        rot_test = copy.deepcopy(getattr(self, '_rotation_test_result', None))
        rot_test_tool = getattr(self, '_rotation_test_tool', None)
        tool_root = getattr(self, '_scanned_folder', '') or None    # 화면 진단과 같은 장비 기준

        # 모든 회차의 셀 값은 .md 옆 CSV에 둔다 (.md 부록은 선택 회차만)
        csv_path = os.path.splitext(path)[0] + '_raw.csv'

        def write_atomic(target, text, encoding, newline):
            temp_path = target + '.part'
            try:
                with open(temp_path, 'w', encoding=encoding, newline=newline) as f:
                    f.write(text)
                os.replace(temp_path, target)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        # 결과: error(.md 실패), csv_error, csv_written(CSV를 썼는가), csv_replaced(기존 CSV를 덮어씀)
        def run():
            state = {'error': None, 'csv_error': None, 'csv_written': False, 'csv_replaced': False}
            try:
                from core.llm_report import build_llm_report, build_raw_csv
                from core.version import APP_VERSION
                raw_csv = {'name': os.path.basename(csv_path)}
                try:
                    # .md 덮어쓰기를 확인했으므로 같은 이름의 CSV도 덮어쓴다 (System Log에 남긴다)
                    state['csv_replaced'] = os.path.exists(csv_path)
                    # Excel에서 바로 열리게 BOM을 붙인다
                    write_atomic(csv_path, build_raw_csv(results), 'utf-8-sig', '')
                    state['csv_written'] = True
                except Exception as e:     # CSV가 실패해도 .md는 쓴다
                    raw_csv['error'] = f'{type(e).__name__}: {e}'
                    state['csv_error'] = raw_csv['error']
                text = build_llm_report(results, spec_limits, spec_deviation,
                                        app_version=APP_VERSION,
                                        exported_at=f"{now:%Y-%m-%d %H:%M:%S}",
                                        source_root=root, raw_csv=raw_csv,
                                        rotation_spec=rot_spec, rotation_test=rot_test,
                                        rotation_test_tool=rot_test_tool, tool_root=tool_root)
                write_atomic(path, text, 'utf-8', '\n')
            except Exception as e:
                state['error'] = e
            outcome.append(state)

        def size_text(target):
            try:
                return f"{os.path.getsize(target) / 1024:.1f} KB"
            except OSError:
                return "크기 확인 실패"

        poll = QTimer(self)

        def check():
            if not outcome:
                return
            poll.stop()
            poll.deleteLater()
            state = outcome[0]
            error = state['error']
            if state['csv_written']:
                if state['csv_replaced']:
                    self.logger.warn(f"같은 이름의 셀 값 CSV를 덮어썼습니다: {csv_path}")
                self.logger.ok(f"셀 값 CSV 저장: {csv_path} ({size_text(csv_path)})")
            if error is None:
                self.logger.ok(f"AI 분석용 Markdown 저장: {path} ({size_text(path)})")
                if state['csv_error']:
                    self.logger.error(f"셀 값 CSV 저장 실패 (Markdown은 저장됨): {state['csv_error']}")
            else:
                written = (f"\n셀 값 CSV는 저장됐습니다: {csv_path}" if state['csv_written'] else '')
                self.logger.error(f"AI 분석용 Markdown 오류: {error}"
                                  + (f" (셀 값 CSV는 저장됨: {csv_path})" if state['csv_written'] else ''))
                QMessageBox.critical(parent, "오류", f"AI 분석용 Markdown 생성 실패\n{error}{written}")

        poll.timeout.connect(check)
        poll.start(200)
        threading.Thread(target=run, daemon=True).start()
