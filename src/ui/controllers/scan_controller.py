from PySide6.QtWidgets import QFileDialog, QMessageBox
import os
from core.recipe_scanner import scan_recipes
from ui.workers.data_loader_thread import DataLoaderThread
from core.checklist import basis_warnings
from core.settings import add_recent_folder
from core import (compute_deviation_matrix, compute_affine_transform, extract_die_positions,
                  filter_stabilization_die)
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE
import charts as viz


class ScanMixin:
    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "Root Data 폴더 선택", self.path_edit.text() or "")
        if path:
            self.path_edit.setText(path)
            self._scan_folder()


    def _scan_folder(self):
        folder = self.path_edit.text()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "경고", "유효한 폴더를 선택해주세요.")
            return

        # 재진입 차단: 이전 로드 스레드가 아직 실행 중이면 새 스캔을 시작하지 않는다.
        # (실행 중 스레드 참조를 덮어써 누수·UI 변조가 일어나는 것을 방지)
        if self._loader_thread is not None and self._loader_thread.isRunning():
            self.logger.warn("이미 스캔이 진행 중입니다. 완료 후 다시 시도하세요.")
            QMessageBox.information(self, "알림",
                                    "이미 스캔이 진행 중입니다. 완료될 때까지 기다려주세요.")
            return

        self.main_tabs.setCurrentIndex(0)
        self.logger.section("폴더 스캔 시작")
        self.logger.info(f"경로: {folder}")
        self.statusBar().showMessage("스캔 중...")

        self.recipes = scan_recipes(folder)
        if not self.recipes:
            self.logger.warn("Recipe 구조를 찾지 못했습니다.")
            QMessageBox.information(self, "알림", "Recipe 구조를 찾지 못했습니다.")
            return

        # ─── 표준 Recipe 이름 검증 (Spec 설정 기준) ───
        spec_dev = self.settings.get('spec_deviation', {})
        std_names = list(spec_dev.keys())

        if std_names:
            # 설정의 스펙 이름과 발견된 Recipe의 short_name이 일치하는지 확인
            # (recipe_scanner가 '1. Vision Pattern' 처럼 앞의 숫자를 자른 short_name을 제공함)
            detected = [r['short_name'] for r in self.recipes]
            mismatched = []
            for r in self.recipes:
                is_match = False
                for std in std_names:
                    # 엄격한 일치 (대소문자 무시, 공백 제거)
                    std_clean = std.strip().lower()
                    if std_clean == r['name'].strip().lower() or \
                       std_clean == r['short_name'].strip().lower():
                        is_match = True
                        break

                if not is_match:
                    mismatched.append(r['name'])  # 불일치하면 원본 폴더명을 기록

            if mismatched:
                std_list = '\n'.join(f"  • {s}" for s in std_names)
                det_list = '\n'.join(f"  • {d}" for d in [r['name'] for r in self.recipes])
                mis_list = '\n'.join(f"  ❌ {m}" for m in mismatched)
                
                msg = (f"데이터 폴더명이 설정된 Spec 이름과 일치하지 않습니다.\n"
                       f"정확한 분석 및 Spec 판정을 위해 폴더명을 아래와 같이 변경해 주세요.\n\n"
                       f"【권장 폴더명 (Spec 설정 기준)】\n{std_list}\n\n"
                       f"【현재 감지된 폴더명】\n{det_list}\n\n"
                       f"【불일치 항목】\n{mis_list}")
                
                QMessageBox.critical(self, "❌ 폴더명 불일치 오류", msg)
                self.logger.error("폴더명 불일치로 스캔이 취소되었습니다.")
                return

        self.logger.ok(f"✅ {len(self.recipes)}개 Recipe 발견")
        for r in self.recipes:
            self.logger.info(f"  Step {r['index']}: {r['name']}")

        self.settings = add_recent_folder(self.settings, folder)
        self._save_settings()
        self._build_nav()

        self.logger.info("전체 Recipe 데이터 로드 시작 (1st round)...")
        self._set_scan_controls_enabled(False)
        self._loader_thread = DataLoaderThread(folder)
        self._loader_thread.finished.connect(self._on_scan_complete)
        self._loader_thread.error.connect(self._on_scan_error)
        self._loader_thread.start()

    def _set_scan_controls_enabled(self, enabled: bool):
        """스캔 진행 중 중복 트리거 방지를 위해 Scan/Open 버튼 활성/비활성."""
        for attr in ('btn_scan', 'btn_browse'):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(enabled)

    def _on_scan_error(self, e, tb=''):
        """로드 스레드 error 슬롯 — 메시지 표시 + 상태 복구(스레드 리셋, 버튼 재활성).

        다이얼로그에는 요약만, System Log에는 traceback 전문을 남긴다.
        (현장에서 어느 파일·어느 함수인지 추적할 수 있어야 한다)
        """
        self.logger.error(f"로드 오류: {e}")
        if tb:
            self.logger.error(tb.rstrip())
        self._loader_thread = None
        self._set_scan_controls_enabled(True)
        self.statusBar().showMessage("스캔 실패")
        QMessageBox.critical(self, "오류", str(e))


    # System Log에 한 Recipe당 표시할 개별 경고 줄 수 상한 (나머지는 '외 N건'으로 요약)
    _MAX_WARN_LINES = 5

    def _log_data_quality(self, results):
        """데이터 품질 문제를 System Log에 드러낸다.

        기존에는 이 경고들이 Python logging(stderr)으로만 나가서, 콘솔이 없는
        windowed EXE에서는 현장에서 전혀 볼 수 없었다. 일부 Lot/측정이 빠진 채
        정상처럼 보이는 상황을 막기 위해 반드시 화면에 남긴다.
        """
        clean = True
        basis_differs = False
        for r in results:
            label = r.get('short_name') or r.get('recipe', '')

            if r.get('error'):
                self.logger.error(f"  ❌ {label}: {r['error']}")
                clean = False

            for msg in r.get('load_errors', []):
                self.logger.error(f"  ❌ {label}: {msg}")
                clean = False

            # Z Drive 채널 제외는 정상 동작이지만 행 수가 줄므로 근거를 남긴다
            dropped = r.get('channel_dropped', 0)
            if dropped:
                self.logger.info(
                    f"  ℹ {label}: Z Drive 채널 {dropped}행 제외 "
                    f"(위치 오프셋은 Height 채널로 분석)")

            # 첫 리핏 제외도 정상 동작이지만 분석 대상이 바뀌므로 폴더 이름을 남긴다
            first_repeat = r.get('first_repeat_excluded')
            if first_repeat:
                self.logger.info(
                    f"  ℹ {label}: 첫 리핏 폴더 {first_repeat} 제외 (Repeat No 1)")

            # 시트 기준(21 Die × 10 Repeat)과 다르면 값은 나오지만 시트와 비교할 수 없다
            for msg in basis_warnings(r):
                basis_differs = True
                self.logger.warn(f"  ⚠ {label}: {msg}")

            # 측정 실패 행 요약 — '어느 Lot/Die가 실제로 문제인지' 구별용
            failed = r.get('failed_measurements') or {}
            if failed.get('count'):
                self.logger.warn(f"  ⚠ {label}: {failed['text']}")
                clean = False

            # 요약 CSV의 비정상 셀 (예: 장비가 NaN을 U+FFFD로 기록)
            warns = r.get('data_warnings', [])
            for msg in warns[:self._MAX_WARN_LINES]:
                self.logger.warn(f"  ⚠ {label}: {msg}")
            if len(warns) > self._MAX_WARN_LINES:
                self.logger.warn(
                    f"  ⚠ {label}: … 외 {len(warns) - self._MAX_WARN_LINES}건 "
                    f"(CSV 비정상 값, 총 {len(warns)}건)")
            if warns:
                clean = False

        if clean:
            if not basis_differs:
                self.logger.ok("  ✅ 데이터 품질 이상 없음")
        else:
            self.logger.info(
                "  ℹ 측정 실패 행은 분석·차트에서 제외되었습니다. "
                "장비 원본 데이터 확인이 필요합니다.")

    def _on_scan_complete(self, results, comparison, elapsed):
        self.recipe_results = results
        # 경로 입력란은 스캔 뒤에 바뀔 수 있다. 내보내기는 이 값으로 출처를 적는다.
        self._scanned_folder = self._loader_thread.folder
        total = sum(len(r.get('raw_data', [])) for r in results)
        self.logger.ok(f"✅ 전체 로드 완료: {total}개 데이터 ({elapsed:.1f}초 소요)")

        self._log_data_quality(results)
        self._update_summary_table(comparison, results)

        # 모든 Step Pass/Fail 일괄 계산 → 버튼 색상 즉시 반영
        self._compute_all_step_pass_states()

        # Affine Transform
        self.logger.section("Affine Transform 계통 오차 분석")
        for i, result in enumerate(self.recipe_results):
            data = filter_stabilization_die(result.get('raw_data', []))
            dx = compute_deviation_matrix(data, 'X')
            dy = compute_deviation_matrix(data, 'Y')
            if dx['die_stats'] and dy['die_stats']:
                name = result.get('short_name', f'Step {i+1}')
                self.logger.head(f"[{name}]")
                # 이 로그 때문에 스캔 완료 처리(화면 표시, 버튼 복구)가 끊기면 안 된다
                try:
                    af = compute_affine_transform(dx['die_stats'], dy['die_stats'],
                                                  extract_die_positions(data))
                except Exception as e:
                    self.logger.error(f"  Affine 계산 오류: {type(e).__name__}: {e}")
                    continue
                if af.get('degenerate'):
                    self.logger.warn(
                        f"  ⚠ Die 좌표가 공선이거나 유효 Die가 3점 미만이라 "
                        f"Affine 계통오차 분리 불가 (유효 Die {af.get('n_dies', 0)}개) "
                        f"— Scaling/Rotation 신뢰 불가")
                else:
                    # 입력이 평균을 뺀 편차라 절편에 병진 정보가 없다. 병진은 카드의 Avg로 본다.
                    self.logger.info(f"  Scaling: Sx={af['sx_ppm']:+.2f} ppm, Sy={af['sy_ppm']:+.2f} ppm")
                    self.logger.info(f"  Rotation: θ={af['theta_deg']:+.6f}° ({af['theta_urad']:+.2f} µrad)")
                    self.logger.info(f"  Residual RMS: X={af['residual_x']:.4f}, Y={af['residual_y']:.4f}")

        self.main_tabs.setCurrentIndex(1)
        self.data_tabs.setCurrentIndex(0)
        # 새 스캔은 이전 스캔의 Die 선택을 물려받지 않고 기본(안정화 Die 제외)으로 시작한다
        self._die_filter_reset_pending = True
        if self.recipe_results:
            self._select_step(0)

        # ─── Recipe 비교 차트 렌더링 ───
        if len(self.recipe_results) >= 2:
            try:
                self.chart_widgets['Boxplot'].set_figure(
                    viz.plot_recipe_comparison_boxplot(self.recipe_results))
                self.logger.ok("📊 Recipe Boxplot 비교 차트 생성 완료")
            except Exception as e:
                self.logger.error(f"Boxplot 비교 오류: {e}")

            try:
                self.chart_widgets['Trend'].set_figure(
                    viz.plot_recipe_comparison_trend(self.recipe_results))
                self.logger.ok("📈 Recipe Trend 비교 차트 생성 완료")
            except Exception as e:
                self.logger.error(f"Trend 비교 오류: {e}")

            try:
                self.chart_widgets['Heatmap'].set_figure(
                    viz.plot_recipe_comparison_heatmap(self.recipe_results))
                self.logger.ok("🗺️ Recipe Heatmap 비교 차트 생성 완료")
            except Exception as e:
                self.logger.error(f"Heatmap 비교 오류: {e}")

        self.statusBar().showMessage(
            f"✅ {len(self.recipes)}개 Recipe | {total}개 데이터 | Step 클릭 → 상세 분석")

        # 스레드 완료 → 참조 해제 + 컨트롤 재활성 (isRunning() 가드를 신뢰 가능하게 유지)
        self._loader_thread = None
        self._set_scan_controls_enabled(True)


