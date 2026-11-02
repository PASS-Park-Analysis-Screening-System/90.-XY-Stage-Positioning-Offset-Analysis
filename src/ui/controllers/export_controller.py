from PySide6.QtWidgets import QMessageBox, QFileDialog
from PySide6.QtCore import QTimer
import os, threading
from core.exporter import (export_combined_csv, export_excel_report,
                           export_quality_report, collect_anomalies)
from core import compute_repeatability, compute_trend, filter_stabilization_die
from core.recipe_scanner import compare_recipes
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class ExportMixin:
    def _export_csv(self):
        if not self.raw_data:
            QMessageBox.warning(self, "경고", "먼저 분석을 실행해주세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV", "", "Text (*.txt);;CSV (*.csv)", "Analysis.txt")
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
            self, "Excel", "", "Excel (*.xlsx)", "Report.xlsx")
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
                    recipe_results=getattr(self, 'recipe_results', None),
                    spec_limits=self.settings.get('spec_limits', {}),
                    log_rows=self.logger.export_rows(),
                    spec_deviation=self.settings.get('spec_deviation', {}))
                self.logger.ok(f"Excel 저장: {path}")
            except Exception as e:
                self.logger.error(f"Excel 오류: {e}")
                QMessageBox.critical(self, "오류", f"Excel 저장 실패\n{e}")


    def _export_quality_report(self):
        """측정 실패·이상치·Spec 초과 + System Log 전용 리포트 (전체 Recipe)."""
        results = getattr(self, 'recipe_results', None)
        if not results:
            QMessageBox.warning(self, "경고", "먼저 분석을 실행해주세요.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Quality Report", "", "Excel (*.xlsx)", "QualityReport.xlsx")
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
        results = getattr(self, 'recipe_results', None)
        if not results:
            QMessageBox.warning(self, "경고", "스캔된 데이터가 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "PDF", "", "PDF (*.pdf)", "Report.pdf")
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


