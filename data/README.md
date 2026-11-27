# data/ — XY Stage Positioning Offset Analysis 실측 데이터 (로컬 전용)

## 이 Tool이 쓰는 데이터
Root 폴더(또는 Lot 폴더 하나)를 연다. Workflow 폴더 이름이 Spec 키와 같으면 자동으로 읽고, 다르면 Workflow별 폴더 선택 창을 연다.
```
<Root>/
├─ 1. Vision Pattern/            ← Workflow (2. In-Die Align, 3. LLC Translation, 4. Global Align …)
│  ├─ <회차>/                    ← 선택 (1st·2nd 또는 숫자·날짜)
│  │  ├─ <Lot>/                  ← Repeat 1회 = Lot 폴더 1개
│  │  │  ├─ <Sample>_X_UL.csv    ← X축 측정값 (필수)
│  │  │  ├─ <Sample>_Y_UL.csv    ← Y축 측정값 (필수)
│  │  │  ├─ <요약>.csv, *.tif(f) ← 부가 정보·보기용
│  │  │  ├─ Log/<Sample>_Log.csv ← 진단 탭용
│  │  │  └─ Debug/SamplePositioning/, Debug/TipPositioning/
```
체크리스트 엑셀 같은 참고 자료도 여기에 둘 수 있다(커밋 금지). pytest는 임시 폴더에 가짜 데이터를 만들어 쓰므로 이 폴더가 없어도 돈다.

## 보관 규칙 (2026-11-26 결정)
- 이 폴더의 데이터는 **로컬 전용**이다. git에 커밋하지 않는다. `.gitignore`가 `/data/*`를 무시하고 이 `README.md`만 추적한다.
  측정 파일에는 장비·고객 정보가 들어 있을 수 있다. 공유가 필요하면 git 대신 사내 자료실을 쓴다.
- worktree(`Pass\worktrees\<번호>__<브랜치>`)에는 `data/`가 없다(README만 있음).
  worktree에서 확인할 때는 앱의 열기 대화상자로 main 체크아웃(`Pass\repos\<이 저장소>\data`)의 파일을 고른다.
  `data/`를 기준으로 하는 테스트는 main 체크아웃에서 돌리거나, 필요한 파일만 worktree의 `data/`에 복사해 쓴다(복사본도 커밋 금지).
