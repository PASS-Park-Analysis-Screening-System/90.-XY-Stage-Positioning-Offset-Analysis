from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
import logging
import os
import traceback
from core.recipe_scanner import (build_recipe, normalize_spec_names, recipes_from_mapping,
                                 scan_recipes)
from core.workflow_map import common_source_folder
from ui.dialogs.workflow_folder_dialog import WorkflowFolderDialog
from ui.workers.data_loader_thread import DataLoaderThread
from core.checklist import basis_warnings, summarize_runs
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

        # 검증을 통과한 뒤에만 바꾼다. 취소했을 때 이전 결과가 새 Recipe 이름 아래 보이면 안 된다
        recipes = scan_recipes(folder)
        spec_keys = list(self.settings.get('spec_deviation', {}).keys())
        if not recipes:
            # 고른 폴더 자체가 Lot 묶음이면(예: 1..14 Lot만 있는 Vision Pattern 폴더)
            # Workflow를 지정해 분석할 수 있게 한다
            if spec_keys and build_recipe(folder) is not None:
                self._load_with_workflow_dialog(
                    folder, "선택한 폴더에 Lot 데이터가 바로 들어 있습니다. "
                            "어느 Workflow의 데이터인지 지정해 주세요.")
                return
            self.logger.warn("Recipe 구조를 찾지 못했습니다.")
            QMessageBox.information(self, "알림", "Recipe 구조를 찾지 못했습니다.")
            return

        # ─── 표준 Recipe 이름 검증 (Spec 설정 기준) ───
        # 일치하면 short_name을 정확한 Spec 키로 맞춘다 (판정·Cpk는 키를 정확히 찾는다)
        if spec_keys:
            mismatched = normalize_spec_names(recipes, spec_keys)
            if mismatched:
                self.logger.warn("폴더명이 Spec 이름과 달라 Workflow별 폴더 선택을 엽니다: "
                                 + ', '.join(mismatched))
                mis_list = '\n'.join(f"  • {m}" for m in mismatched)
                self._load_with_workflow_dialog(
                    folder, "폴더명이 Spec 이름과 달라 Workflow별로 데이터 폴더를 지정해 주세요.\n"
                            f"Spec 이름과 다른 폴더:\n{mis_list}")
                return

        self._start_load(recipes, folder)

    def _open_workflow_dialog(self):
        """상단 'Workflow별 선택' 버튼 — 폴더명과 관계없이 Workflow마다 폴더를 고른다."""
        if self._loader_thread is not None and self._loader_thread.isRunning():
            self.logger.warn("이미 스캔이 진행 중입니다. 완료 후 다시 시도하세요.")
            QMessageBox.information(self, "알림",
                                    "이미 스캔이 진행 중입니다. 완료될 때까지 기다려주세요.")
            return
        if not self.settings.get('spec_deviation'):
            QMessageBox.warning(self, "경고", "Spec 설정(spec_deviation)이 없어 Workflow를 지정할 수 없습니다.")
            return
        folder = self.path_edit.text().strip()
        self.main_tabs.setCurrentIndex(0)
        self.logger.section("Workflow별 폴더 선택")
        self._load_with_workflow_dialog(
            folder if os.path.isdir(folder) else '',
            "Workflow마다 분석할 데이터 폴더를 지정합니다. 폴더 이름은 Spec 이름과 달라도 됩니다.")

    def _load_with_workflow_dialog(self, root, reason):
        """Workflow별 폴더 대화상자를 열고, 확인하면 그 폴더들로 로드한다. 취소하면 아무것도 바꾸지 않는다."""
        spec_keys = list(self.settings.get('spec_deviation', {}).keys())
        remembered = self.settings.get('workflow_folder_map', {})
        dlg = WorkflowFolderDialog(self, spec_keys, root, remembered, reason)
        if dlg.exec() != QDialog.Accepted:
            self.logger.info("Workflow별 폴더 선택을 취소했습니다. 분석하지 않습니다.")
            self.statusBar().showMessage("스캔 취소")
            return

        mapping = dlg.selected_mapping()
        # 지금 Spec에 없는 Workflow의 지난 지정은 버린다
        merged = {**(remembered if isinstance(remembered, dict) else {}), **dlg.folder_map()}
        self.settings['workflow_folder_map'] = {
            key: value for key, value in merged.items() if key in spec_keys}
        for text in dlg.warnings():
            self.logger.warn(f"  ⚠ {text}")
        recipes, problems = recipes_from_mapping(mapping, spec_keys)
        if problems:
            lines = '\n'.join(f"  • {name}: {reason_text} ({path or '경로 없음'})"
                              for name, path, reason_text in problems)
            for name, path, reason_text in problems:
                self.logger.warn(f"  ⚠ {name}: {reason_text} — {path}")
            QMessageBox.warning(self, "경고", f"아래 Workflow는 건너뜁니다.\n{lines}")
        if not recipes:
            self._save_settings()
            self.logger.warn("분석할 Workflow 폴더가 없습니다.")
            QMessageBox.information(self, "알림", "분석할 Workflow 폴더가 없습니다.")
            return

        for r in recipes:
            self.logger.info(f"  {r['short_name']} ← {r['source_label']} ({r['source_path']})")
        source = common_source_folder([r['source_path'] for r in recipes])
        self._start_load(recipes, source, source_label='Workflow별 선택')

    def _start_load(self, recipes, source_folder, source_label=None):
        """검증을 마친 Recipe 목록으로 로드를 시작한다.

        source_label이 없으면 루트 폴더 자동 스캔이다. 그때만 최근 폴더에 넣는다
        (Workflow별 선택의 공통 상위 폴더는 다시 스캔할 루트가 아니다).
        """
        self.recipes = recipes
        self.logger.ok(f"✅ {len(self.recipes)}개 Recipe 발견"
                       + (f" ({source_label})" if source_label else ""))
        for r in self.recipes:
            self.logger.info(f"  Step {r['index']}: {r['name']}")

        if source_label is None:
            self.settings = add_recent_folder(self.settings, source_folder)
        self._save_settings()

        self.logger.info("전체 Recipe 데이터 로드 시작 (회차 폴더가 여럿이면 모든 회차)...")
        self._loader_thread = DataLoaderThread(source_folder, recipes=recipes)
        self._loader_thread.finished.connect(self._on_scan_complete)
        self._loader_thread.error.connect(self._on_scan_error)
        self._loader_thread.progress.connect(self.statusBar().showMessage)
        try:
            self._reset_results_for_scan()
        except Exception as e:
            # 시작하지 않은 스레드를 남기면 스캔이 돌지 않는데도 로드 중으로 보인다
            tb = traceback.format_exc()
            self._clear_cards_after_failed_start()
            self._on_scan_error(e, tb)
            return
        self._set_scan_controls_enabled(False)
        self._loader_thread.start()

    def _reset_results_for_scan(self):
        """이전 폴더의 결과를 지운다. 안 그러면 로드 중에 새 Step 이름으로 이전 판정이 보인다."""
        self.recipe_results = []
        self._run_compare_key = None    # 새 결과의 회차 비교 차트를 다시 그리게 한다
        self.sum_table.setColumnCount(0)
        self._build_nav()
        if self.recipes:
            self._select_step(0)

    def _clear_cards_after_failed_start(self):
        """화면을 비우다 실패하면 이전 폴더의 PASS와 Cpk가 카드에 남는다.

        복구 중이므로 다시 예외를 내지 않는다. 버튼 복구와 대화상자가 막히면 안 된다.
        """
        for name in ('card_x', 'card_y'):
            try:
                getattr(self, name).clear_stats()
            except Exception as e:
                self._log_during_recovery(f"{name} 초기화 오류: {type(e).__name__}")

    def _log_during_recovery(self, text):
        """System Log 기록 실패가 스캔 실패 복구를 막지 않게 한다."""
        try:
            self.logger.error(text)
        except Exception:
            logging.getLogger(__name__).exception('System Log 기록 실패')

    def _set_scan_controls_enabled(self, enabled: bool):
        """스캔 진행 중 중복 트리거 방지를 위해 Scan/Open/Workflow별 선택 버튼 활성/비활성."""
        for attr in ('btn_scan', 'btn_browse', 'btn_workflow'):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(enabled)

    def _on_scan_error(self, e, tb=''):
        """로드 스레드 error 슬롯 — 메시지 표시 + 상태 복구(스레드 리셋, 버튼 재활성).

        다이얼로그에는 요약만, System Log에는 traceback 전문을 남긴다.
        (현장에서 어느 파일·어느 함수인지 추적할 수 있어야 한다)
        """
        self._loader_thread = None
        self._set_scan_controls_enabled(True)
        # 메시지 없는 예외도 대화상자가 비지 않게 한다
        try:
            text = str(e)
        except Exception:
            text = ''
        message = text if text.strip() else type(e).__name__
        self._log_during_recovery(f"로드 오류: {message}")
        if tb:
            self._log_during_recovery(tb.rstrip())
        try:
            self.statusBar().showMessage("스캔 실패")
            self.filter_info_label.setText("⚠ 스캔 실패")
            self.filter_info_label.setToolTip(message)
        except Exception as label_error:
            self._log_during_recovery(f"실패 표시 오류: {type(label_error).__name__}")
        QMessageBox.critical(self, "오류", message)


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
            for run in r.get('run_results') or []:
                if run.get('error') and run.get('round') != r.get('round'):
                    self.logger.error(f"  ❌ {label} 회차 {run.get('round', '')}: {run['error']}")
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

    def _render_recipe_comparison_charts(self, announce=True):
        """Recipe 간 비교 차트(Boxplot·Trend·Heatmap). 회차를 바꾸면 다시 그린다."""
        if len(self.recipe_results) < 2:
            return
        for name, plot, done in (
                ('Boxplot', viz.plot_recipe_comparison_boxplot, "📊 Recipe Boxplot 비교 차트 생성 완료"),
                ('Trend', viz.plot_recipe_comparison_trend, "📈 Recipe Trend 비교 차트 생성 완료"),
                ('Heatmap', viz.plot_recipe_comparison_heatmap, "🗺️ Recipe Heatmap 비교 차트 생성 완료")):
            try:
                self.chart_widgets[name].set_figure(plot(self.recipe_results))
                if announce:
                    self.logger.ok(done)
            except Exception as e:
                self.logger.error(f"{name} 비교 오류: {e}")

    def _log_runs(self, results):
        """회차를 모두 읽었다는 것과 기본 회차를 남긴다 (전에는 첫 회차만 조용히 분석했다)."""
        for r in results:
            runs = r.get('runs') or []
            if len(runs) < 2:
                continue
            label = r.get('short_name') or r.get('recipe', '')
            self.logger.info(f"  ℹ {label}: 회차 {len(runs)}개 분석 — Step 상세 기본 회차 "
                             f"{r.get('default_run')} (회차 콤보·'회차 비교' 탭에서 변경)")
            if r.get('default_skipped'):
                self.logger.warn(f"  ⚠ {label}: 최신 회차 {', '.join(r['default_skipped'])}에 "
                                 f"데이터가 없어 기본 회차를 {r.get('default_run')}(으)로 했습니다")
            warned = [str(run['run']) for run in runs if run['warnings']]
            if warned:
                self.logger.warn(f"  ⚠ {label}: 경고가 있는 회차 {', '.join(warned)} "
                                 f"(회차 비교 탭의 ⚠ 참고)")

    def _on_scan_complete(self, results, comparison, elapsed):
        # 회차가 둘 이상인 Workflow는 회차별 요약을 붙인다 (회차 비교 표·Excel)
        spec_limits = self.settings.get('spec_limits', {})
        spec_deviation = self.settings.get('spec_deviation', {})
        for result in results:
            if result.get('run_results'):
                result['runs'] = summarize_runs(result, spec_limits, spec_deviation)
        self.recipe_results = results
        # 경로 입력란은 스캔 뒤에 바뀔 수 있다. 내보내기는 이 값으로 출처를 적는다.
        self._scanned_folder = self._loader_thread.folder
        total = sum(len(r.get('raw_data', [])) for r in results)
        self.logger.ok(f"✅ 전체 로드 완료: {total}개 데이터 ({elapsed:.1f}초 소요)")

        self._log_data_quality(results)
        self._log_runs(results)
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
        self._render_recipe_comparison_charts()

        self.statusBar().showMessage(
            f"✅ {len(self.recipes)}개 Recipe | {total}개 데이터 | Step 클릭 → 상세 분석")

        # 스레드 완료 → 참조 해제 + 컨트롤 재활성 (isRunning() 가드를 신뢰 가능하게 유지)
        self._loader_thread = None
        self._set_scan_controls_enabled(True)


