# OGC2026 — MSGA

조선소 블록 배치(공간 일정계획) 결선 저장소.

## 목표
결선의 새 히든 인스턴스에 일반화되는 단일 솔버를 만들고, 공개 추격 지표로
예선 P3-P6 모든-팀 최솟값 이하를 목표로 한다. 현재 솔버·제출·점수의 단일
권위표는 `docs/CURRENT_BASELINE.md`다. 목표 기준선은
`data/prelim_refs.json`의 §3 전-제출 최솟값(P3 64,695 / P4 1,908,043 /
P5 7,718,486 / P6 25,452,695). `data/leaderboard.json`(§5 최종-제출
순위표)은 등수 환산 전용이며, 결선 히든 점수는 서로 다른 입력이라 이 목표와
직접 비교하지 않는다.

## 구조
- `solver/`        v6.3 기반 커널(C++ `src/` + `sscore*.so`) + 드라이버 `myalgorithm.py`; 실험 중에는 mutable.
- `submissions/`   로컬 canonical zip = `[OGC2026_AlgorithmCode]_MSGA.zip` (v6.3, 서버 미검증).
                   마지막 서버 실측 제출은 `2026-08-04_v51-finals.zip`; 둘을 혼용하지 말 것.
- `docs/`          기술 보고서(`TECHREPORT.md` → PDF), 설계 문서(PIPELINE/UNDERSTANDING/MODEL/EXACT_DESIGN),
                   예선 최종 보고서 PDF(권위 소스).
- `data/`          `preliminary_test/`(6) · `train_final/`(40, 결선 판정용) · `train/`(40, 구) ·
                   `prelim_refs.json`(목표 기준선) · `leaderboard.json`(등수 환산) · `instances.json`.
- `tools/`         활성 하니스만: 빌드 배터리, 평가(eval_p36/eval/eval_prelim + submit_eval/collect/run_instance),
                   판정(judge: p36 갭·등수 / pair 부호검정), 하한(z1lb/z3lb), 진단(census/coalition/frag_diag),
                   게이트(test_driver, preflight).
- `runs/`          실행 로그(transient, gitignore; 수시 정리). 보존 근거는 `results/`·`archive/`에.
- `results/`       동결된 스윕 결과 JSON.
- `archive/`       역사 전부: 판정-종료 프로브(`tools-probes/`), 실험 트리(`exp/`), 초기 문서, 근거 로그, best 보드.
- `problem/`       문제 서술.

## 사용
- 빌드+`data/train` 카나리아: `sbatch -p titan tools/battery.sbatch`
  (기대 P1 1,499 / P2 3,690). `preliminary_test` P1/P2 smoke의 별도 기대값은
  11,280 / 31,368이며 두 코퍼스의 같은 문제 번호를 혼용하지 말 것.
- 대외 위치(P3–P6, 공식 TL 240/480/600/900): `sbatch -p titan tools/eval_p36.sbatch TAG $PWD/solver SEED`
  → 판정: `python3 tools/judge.py p36 TAG` (§3 최솟값 갭 + 등수 환산)
- 강건성(train_final 40 스윕): `OGC_DATA=train_final SOLVER=$PWD/solver tools/submit_eval.sh TAG 600 titan`
  → 동결: `python3 tools/collect.py TAG` → 판정: `python3 tools/judge.py pair BASETAG TAG` (부호검정 포함)
- 규칙: 게이트웨이 계산 금지(Slurm만) · 동시 잡 ≤8, 대기열 캠핑 금지 · 시드 7 고정(다중 시드는 측정 대조 전용)
  · 판정: train_final 40-쌍 부호검정 1차, eval_p36 갭 2차, 1%-급 변동은 노이즈.
