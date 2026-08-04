# OGC2026 — MSGA

조선소 블록 배치(공간 일정계획) 결선 저장소.

## 목표
결선 히든 P3-P6에서 예선 모든-팀 최솟값급 이하. 기준선은
`data/prelim_refs.json`의 §3 전-제출 최솟값(P3 64,695 / P4 1,908,043 /
P5 7,718,486 / P6 25,452,695). `data/leaderboard.json`(§5 최종-제출
순위표)은 등수 환산 전용 — 최솟값 판정에 쓰지 말 것.

## 구조
- `solver/`        커널(C++ `src/` + `sscore*.so`) + 드라이버 `myalgorithm.py`. 유일한 솔버 트리. (v4.1)
- `submissions/`   제출 zip. 대표본 = `2026-08-03_v41-finals.zip` (= `[OGC2026_AlgorithmCode]_MSGA.zip`),
                   포트폴리오 변형 = `v41-{nfp2,sh45,noslice,k60}`, 이정표 = v21/v22b/v30/v31/v31d4/v40.
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
- 빌드+카나리아: `sbatch -p titan tools/battery.sbatch` (기대 p1 1499 / p2 3690)
- 대외 위치(P3–P6, 공식 TL 240/480/600/900): `sbatch -p titan tools/eval_p36.sbatch TAG $PWD/solver SEED`
  → 판정: `python3 tools/judge.py p36 TAG` (§3 최솟값 갭 + 등수 환산)
- 강건성(train_final 40 스윕): `OGC_DATA=train_final SOLVER=$PWD/solver tools/submit_eval.sh TAG 600 titan`
  → 동결: `python3 tools/collect.py TAG` → 판정: `python3 tools/judge.py pair BASETAG TAG` (부호검정 포함)
- 규칙: 게이트웨이 계산 금지(Slurm만) · 동시 잡 ≤8, 대기열 캠핑 금지 · 시드 7 고정(다중 시드는 측정 대조 전용)
  · 판정: train_final 40-쌍 부호검정 1차, eval_p36 갭 2차, 1%-급 변동은 노이즈.
