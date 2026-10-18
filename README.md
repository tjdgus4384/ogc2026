# OGC2026 — MSGA

Optimization Grand Challenge 2026의 조선소 블록 배치 문제
*The Grand Shipyard Puzzle: Pack the Block, Beat the Clock*에 참가한 MSGA 팀의 최종 제출본.

**Finalist — 958팀 중 26위 (55개국).**
[SeongHyeon Yun의 finalist certificate](OGC2026-0825.pdf)
(발급일: 2026-08-18, certificate ID: OGC2026-0825).

## 최종 제출본 (v6.5.2)

| 파일 | 내용 |
|------|------|
| [Algorithm code ZIP](submissions/%5BOGC2026_AlgorithmCode%5D_MSGA.zip) | 최종 제출 패키지, 432,348 bytes |
| [Technical report](docs/%5BOGC2026_TechReport%5D_MSGA.pdf) | 제출 기술 보고서 |
| [Certificate](OGC2026-0825.pdf) | 결선 진출 및 최종 순위 증명서 |
| [Source](solver/) | ZIP과 일치하는 Python 드라이버, C++ 소스, 컴파일된 확장 모듈 |

최종 ZIP의 SHA-256:

```text
5380a47a6e603c1397b73168b3987c1f7ecefecd7c0385f378b5b0b259c76955
```

ZIP의 12개 파일은 `solver/`의 대응 파일과 바이트 단위로 일치한다
(`build.txt`는 `solver/BUILD.txt`에 대응). 최종 제출 파일은 원본 그대로 보존했다.
버전별 변경 사항은 [solver/VERSION](solver/VERSION), 최종 검증 기록은
[campaign ledger](docs/CAMPAIGN_LEDGER.md)의 `08-15 최종 제출 판정: v6.5.2 GO`와
`bundle-p4` 완료 기록에 있다. 이전 제출본과 실험 기록은 개발 이력으로 보존한다.

## 구조
- `solver/`        최종 v6.5.2 커널(C++ `src/` + `sscore*.so`) + 드라이버 `myalgorithm.py`.
- `submissions/`   최종 `[OGC2026_AlgorithmCode]_MSGA.zip` 및 이전 제출 패키지.
- `docs/`          제출 기술 보고서, 설계 문서, 캠페인 검증 기록, 예선 최종 보고서.
- `data/`          `preliminary_test/`(6) · `train_final/`(40, 결선 판정용) · `train/`(40, 구) ·
                   `prelim_refs.json`(목표 기준선) · `leaderboard.json`(등수 환산) · `instances.json`.
- `tools/`         활성 하니스만: 빌드 배터리, 평가(eval_p36/eval/eval_prelim + submit_eval/collect/run_instance),
                   판정(judge: p36 갭·등수 / pair 부호검정), 하한(z1lb/z3lb), 진단(census/coalition/frag_diag),
                   게이트(test_driver, preflight).
- `runs/`          로컬 실행 로그(gitignore). 보존 근거는 `results/`·`archive/`에.
- `results/`       동결된 스윕 결과 JSON.
- `archive/`       역사 전부: 판정-종료 프로브(`tools-probes/`), 실험 트리(`exp/`), 초기 문서, 근거 로그, best 보드.
- `problem/`       문제 서술.

## 실행 및 빌드

제출 확장 모듈은 Python 3.12 / Linux x86-64용이며 x86-64-v3 CPU를 대상으로 빌드했다.
C++17, OpenMP, pybind11을 사용한다. OR-Tools CP-SAT은 선택 사항이다.
빌드 명령과 의존성은 [solver/BUILD.txt](solver/BUILD.txt)에 기록되어 있다.
Python 3.12와 pybind11을 설치한 환경에서 `PY=/path/to/python3.12 bash solver/build.sh`로 재빌드한다.

아래는 개발 당시 Slurm 클러스터에서 사용한 평가 명령이다.
- 빌드+카나리아: `sbatch -p titan tools/battery.sbatch` (기대 p1 1499 / p2 3690)
- 대외 위치(P3–P6, 공식 TL 240/480/600/900): `sbatch -p titan tools/eval_p36.sbatch TAG $PWD/solver SEED`
  → 판정: `python3 tools/judge.py p36 TAG` (§3 최솟값 갭 + 등수 환산)
- 강건성(train_final 40 스윕): `OGC_DATA=train_final SOLVER=$PWD/solver tools/submit_eval.sh TAG 600 titan`
  → 동결: `python3 tools/collect.py TAG` → 판정: `python3 tools/judge.py pair BASETAG TAG` (부호검정 포함)
- 규칙: 게이트웨이 계산 금지(Slurm만) · 동시 잡 ≤8, 대기열 캠핑 금지 · 시드 7 고정(다중 시드는 측정 대조 전용)
  · 판정: train_final 40-쌍 부호검정 1차, eval_p36 갭 2차, 1%-급 변동은 노이즈.

`STATUS.md`와 `SUBMISSIONS.md`의 과거 항목은 당시 상태를 기록한 개발 이력이다.
최종 제출본의 기준은 위 v6.5.2 패키지다.
