# 실험 후보 (v5.3 기준)

`builds/`는 무시되는 스크래치 빌드 영역이다. 측정에 올린 후보의 **소스 델타만**
여기에 패치로 고정한다. 채택되면 `solver/`로 올라가고 패치는 원장에 남는다.

기준선 `builds/b53` = `submissions/[OGC2026_AlgorithmCode]_MSGA.zip` 압축해제본.
같은 플래그로 재빌드하면 출하 `.so`와 **바이트 동일**(`6c600758…`)이므로, 후보와
출하본의 차이는 아래 패치가 전부다.

빌드: `sbatch -p titan tools/build_variant.sbatch builds/<name>`
판정: `python3 tools/abgate.py submit <tag> --base builds/b53 --cand builds/<name>`

## 하나의 근본 결함, 두 개의 얼굴

커널은 같은 날 이벤트의 **순서**를 방출기와 일관되게 다루지 못한다.

**진입**: `vpConservative` rule 2는 A가 같은 날 배치 **전원 뒤에** 들어갈 수 있는지를,
rule 4는 그 전원이 **A 뒤에** 들어갈 수 있는지를 **둘 다** 요구한다. 공식 체커
Stage 2는 `present_at_entry = { b : b.entry_time < entry_time < b.exit_time }`로
같은 날 진입을 **아예 제외**하고, 순서는 Stage 5가 방출된 순서로만 검증한다.
실측: 같은 날·같은 베이 묶음(≥2)에 속한 진입이 P3 74% / P4 65% / P5 64% / P6 65%.

**퇴출**: rule 3/5의 타이브레이크가 진입일뿐(`s.e[k] < eN`)이라, **진입일도 퇴출일도
같은 쌍**은 서로의 present 집합에서 빠져 **검사 자체가 되지 않는다**. v5.3 build.txt가
스스로 "미인증 클래스"로 적어 둔 바로 그것이다. 실측: 출하 보드에도 그런 인접 퇴출쌍이
P3 44 / P4 21 / P5 29 / P6 22개 있다.

두 결함은 얽혀 있다 — 진입 완화(m1)는 같은 날 진입을 늘려 퇴출 타이 클래스를
희귀에서 흔함으로 만든다. m1 단독 게이트에서 32쌍 중 7쌍이 `_trivial`로 붕괴한
원인이 이것이고, 진단 증거는 `Stage5: EXIT block X obstructed by block Y (sweep)`
111건이다.

## 후보

| 패치 | 무엇 | 판정 |
|---|---|---|
| `m1-entry-order.patch` | rule 2/4가 방출 순서(오름차순 id)만 검사 | ❌ 32쌍 7W/24L p=0.0033. **단, P3만 5W/2L 중앙 −1.57%.** 7쌍이 퇴출 타이 결함으로 폴백 |
| `m4-exit-tiebreak.patch` | rule 3/5 타이브레이크를 방출 순서(진입일 내림차순, 동률 시 id 오름차순)까지 완성. v5.3 단독 정확성 수리 | 게이트 중 |
| `m5-entry-and-exit.patch` | m1 + m4 | 게이트 중 |
| `m6-pairconflict-order.patch` | m5 + `api.cpp pairConflicts`의 같은 결함(진입 양방향 요구, 퇴출 타이 임의 선택) 제거 | 대기 |
| `m2-layer-order-*.patch` | 같은 날 진입 순서를 층수 오름차순(평평한 것 먼저)으로. 크레인이 layer j≥k를 막으므로 높은 이웃이 먼저면 더 많이 막는다. 커널·방출기 동시 변경 | 대기 (m5 기반으로 재작성 필요) |

## 왜 이게 점수 밖에서도 중요한가

`insert_capped` → `insertGreedy` → `vpConservative`, `move_block(tight)`도 같은 경로다.
따라서 이 프로젝트의 구조적 기각 — "최종 보드는 정확히 강성", "퇴출 캐스케이드 제로섬",
"P3 배정 하강 117후보 중 상위 14/14가 정시 배치 불가" — 이 전부 **실제 규칙보다
엄격한 오라클**로 측정됐다. `tools/rigidity_probe.py`가 고쳐진 커널로 그 축을 재측정한다.
