# 캠페인 대시보드 — 모든-팀 최솟값급 솔루션

> 지금 무엇을 하고 있고 어떻게 되고 있는지의 단일 창구. 마일스톤마다 갱신.
> 판정 명령: `python3 tools/judge.py p36 <tag>` / `judge.py pair <base> <cand>`.

## 목표 vs 현재 (eval_p36, 공식 TL, §3 전-제출 최솟값 기준)

| | 목표(모든-팀 min) | v41 s7 | v41 s41 | v41 s42 | 갭(최선 시드) |
|---|---|---|---|---|---|
| P3 | 64,695 | 79,410 | 81,170 | 82,610 | **+22.7%** |
| P4 | 1,908,043 | 3,203,137 | 2,956,671 | 3,202,070 | **+55.0%** |
| P5 | 7,718,486 | 10,643,157 | 9,798,330 | 10,490,894 | **+26.9%** |
| P6 | 25,452,695 | 28,674,341 | 28,082,532 | 28,789,584 | **+10.3%** |

등수-환산(§5 최종-제출 분포, 참고용): ~7-14위. 강건성: train_final 40/40 feasible, 0 overrun.

## 스토리 보드 (ultragoal)

| # | 스토리 | 상태 | 증거 |
|---|---|---|---|
| G001 | 리포·Git 위생 + 베이스라인 동결 | ✅ 완료 | 커밋 74c960f..557d391, battery 1499/3690 PASS, runs 40M→168K |
| G002 | 판정 파이프라인 단일화 | ✅ 완료 | tools/judge.py + v41 스코어카드 동결 (d7feb90, 9695a90) |
| G003 | 솔버 간결화 (v5 코어) | 🔨 진행 중 | 드라이버 완료(1220→880줄, env 노브 30→4), 커널 수술 중 |
| G004 | 10%-급 개선 축 공략 | ⏳ 대기 | 후보: NFP2(-2% 검증), bestfit, TL-미사용 회수(P3 42s), 혼잡-창 재배치 |
| G005 | 결선 제출 번들 + 최종 검증 | ⏳ 대기 | |

## 지금 하는 일 (G003: 솔버 간결화 — 검증 단계)

- ✅ 드라이버(myalgorithm.py): 기각된 실험 경로 12개 제거(ORDSEARCH/PREFCYC/SLPEER/
  LOCKK/MARM/FLEET/BAYSPLIT/BOXLNS/REFINE/coh/rand/k60·sh45), baysplit/boxlns/refine.py 삭제.
  1220→880줄. env 표면: OGC_SEED/DIAG/SLICE(+내부 PREFP)만 생존.
- ✅ 커널(src/): 기본값-off 기각 노브 ~30개의 분기 절제(OTA/GLS/LAHC/tabu/relink/
  rescue/squeak/swap3/swapRel/takeover/iCompact/reorP/shiftLoc/exactCut/costMix/
  congest/slide/fastscan/repairR...), 채택-상수 하드코딩. src 4,739→2,503줄(-47%),
  knobs.hpp 131→58줄, 커널 env 표면 60+→4 (PREFP 채널 + NFP2/NFPAUTO/BESTFIT G004 후보).
- ✅ **증명: battery 62800 카나리아 p1 1499 / p2 3690 정확 일치** = 무-env 출하 궤적
  바이트-동일. 드라이버 게이트 PASS. 커밋 cdfc3e7.
- 🔨 실행 중: tf-v50-t600 40-쌍 비회귀(잡 62801-04) + p36-v50-s7 스팟(62805).
  판정 기준: `judge pair tf-v41-t600 tf-v50-t600` = 중립(1%-급 노이즈 허용).

## 판정 로그 (최신 우선)

- **08-04 v41 베이스라인 동결**: tf-v41-t600(s7) 40/40 OK, vs v40 32W/7L −2.49% p<0.001;
  p36 3시드 스코어카드 위 표. P3가 TL240 중 198s만 사용(거버넌스 미사용분) → G004 레버.
- **08-04 데이터 권위 정정**: 목표치는 §3 전-제출 최솟값(leaderboard.json 최종-제출 min보다
  8~11% 낮음). data/prelim_refs.json에 고정, judge.py가 출처 명시.
- **08-03 (전임자)**: nfp2 −4.3% vs v40(32W/7L, 채택 후보), bfit −3.6%; ota 계열 폭발 기각;
  coh/noslice/k60/sh45/slkdef 기각. mixAcc OFF 확정(26W-14L).

## 실행 중인 잡

- 62801-04: tf-v50-t600 (train_final 40, 간결화 비회귀 판정)
- 62805: p36-v50-s7 (공식-TL P3-P6 스팟)
