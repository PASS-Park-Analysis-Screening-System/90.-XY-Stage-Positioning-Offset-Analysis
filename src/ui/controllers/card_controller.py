from core.checklist import compute_axis_metrics
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class CardMixin:
    def _update_cards(self, data, recipe, warned=False):
        short = recipe.get('short_name', '')
        spec_limits = self.settings.get('spec_limits', {})
        spec_deviation = self.settings.get('spec_deviation', {})
        if not spec_limits.get(short):
            self.logger.warn(f"spec_limits에 '{short}' 키 없음 → Cpk 계산 불가")
        if not spec_deviation.get(short):
            self.logger.warn(f"spec_deviation에 '{short}' 키 없음 → PASS/FAIL 판정 불가 (Cpk도 표시하지 않음)")

        # 카드는 Die 필터를 따른다. Cpk는 편차 기준이다 (core.checklist 참조).
        mx = compute_axis_metrics(data, 'X', short, spec_limits, spec_deviation)
        my = compute_axis_metrics(data, 'Y', short, spec_limits, spec_deviation)
        self._dev_x, self._dev_y = mx['deviation'], my['deviation']

        custom = getattr(self, '_die_filter_is_custom', False)
        for card, m in ((self.card_x, mx), (self.card_y, my)):
            card.update_stats(m['avg'], m['range'], m['stddev'], m['cpk'], m['result'],
                              spec_r=m['spec_range'], spec_s=m['spec_stddev'], warned=warned)
            card.set_basis(m['die_count'], m['repeat_count'], custom)

        # Step 버튼은 Summary와 같은 분석 기준으로 _compute_all_step_pass_states에서만
        # 정한다. 필터를 바꿔도 체크리스트 판정은 바뀌지 않는다.
        self._refresh_step_buttons()

    # ──────────────────────────────────────────────
    # Tables
    # ──────────────────────────────────────────────

