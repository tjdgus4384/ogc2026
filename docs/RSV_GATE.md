# RSV gate contract

이 문서는 `rsv-tf` 결과가 완성되기 전(58/70 영수증, 효과값 미판독)에 고정한
예약 가드의 의미·계측·채택 규약이다. 후보 바이너리는
`builds/rsv/sscore.cpython-312-x86_64-linux-gnu.so`
(`sha256=c4df7f8bc24689c14334a6ce1174257e26a6cc81407ee0dbb613570b62dfbef6`)이고,
무시되는 build tree의 정확한 소스 델타는
`experiments/rsv-isolated.patch`에 보존한다.

## 이 신호가 증명하는 것과 증명하지 않는 것

`dayLoadE[t]`는 각 블록의 `area * min(1, P/(D-R))`을 `[R,D]`의 모든 정수일에
더한 **휴리스틱 혼잡 신호**다. 어떤 날 반드시 존재해야 하는 면적도, 에너지 하한도
아니다. 특히 C++ 루프가 `D`를 포함하므로 합계는 일반적으로 `area*P`보다 한 날짜분
크다. `packFill`도 증명된 적재 상한이 아니라, 각 블록의 최소 union-bbox 방향에서
`sum(max-layer polygon area) / sum(union bbox area)`를 구한 경험적 밀도다.

따라서 RSV의 주장은 좁다. `dayLoadE[e] > packFill`인 연속 release 날짜를 건너뛰는
정책이 실제 탐색 분포에서 목적값을 낮추는지를 짝실험으로 재는 것이다. 코드 주석의
`provably over-subscribed`, `unavoidable demand`, `standard energetic profile` 표현은
수학적 하한으로 읽으면 안 되며, 채택 시 바로잡는다.

## source-faithful 계측 정정

원장의 초기 `peak/fill`은 layer 0 bbox만 사용했다. C++ `OrientGeom::bb`는 모든 층의
union bbox이고 `parea`는 층 중 최대 폴리곤 면적이다. 별도 debug binding으로 현재
소스를 다시 컴파일해 `packFill`, `max(dayLoadE)`, 그 비를 직접 읽었고
`tools/rsv_analyze.py`의 JSON 재현값과 비트 수준에서 같은 double을 얻었다.

| 보드 | 원장 비 | 실제 C++ 비 | 실제 이동 블록 |
|---|---:|---:|---:|
| P3 | 0.64 | 0.820 | 0/200 |
| P4 | 1.26 | 1.470 | 74/150 |
| P5 | 1.36 | 1.578 | 106/200 |
| P6 | 2.19 | **2.864815** | 171/250 |

이 때문에 실제 발동 집합도 달라진다.

- `train_final`: 원장 35개가 아니라 37개. 기존 목록에 `prob_1`, `prob_21` 추가.
  `rsv-tf-srcmiss`(job 74434)가 누락 2개 × 시드 2를 같은 규약으로 보충한다.
- `train`: 원장 14개가 아니라 18개.
  `21,23,24,25,26,27,28,30,31,32,33,34,35,36,37,38,39,40`.

`tools/rsv_analyze.py`는 gate가 dataset 전체의 source-active 집합을 덮는지 자동으로
검사한다. 부하 기울기의 독립 단위는 seed pair가 아니라 **인스턴스**다. 같은 인스턴스의
두 시드는 같은 노출값을 공유하므로 먼저 평균 delta 하나로 줄인 뒤 Spearman 상관과
Theil--Sen 기울기를 계산한다.

## 결과 판정 규약

예선 두 게이트(서로소 24시드씩)는 이미 세 보드 모두 같은 방향으로 복제됐다. train은
효과를 다시 발견하는 검정이라기보다 히든 전이 위험을 거르는 검정이다.

1. `rsv-tf`와 `rsv-tf-srcmiss`를 합쳐 37개 실제 발동 인스턴스를 판독한다. 전 쌍
   feasible, hard timeout 0, `_trivial`/씨앗 바닥의 새 파국 0이어야 한다.
2. C++ 기준 예선 지지구간은 `peak/fill <= 2.864815051879244`로 결과 전에 고정한다.
   지지구간 안과 밖 각각에서 인스턴스 평균 delta의 평균·중앙이 0 이하이고 W>=L이면
   무제한 RSV가 일반화 방향을 통과한다.
3. 지지구간 밖이 손해면 RSV 전체를 채택하지 않는다. `peak/fill` 상한 2.864815는
   결과에서 고른 값이 아닌 예선 support 경계이므로 다음 후보가 될 수 있지만, 별도
   A/B를 통과해야 하며 자동 채택하지 않는다. 지지구간 안도 손해면 현재 RSV는 기각한다.
4. 통과 시 `train`의 위 18개 × 시드 2를 검증한다. 그 뒤 env 스위치를 제거한 출하
   후보를 만들고, 현재 `solver/`와 직접 P4/P5/P6 짝게이트를 돌려 후보 바이너리의
   분기·계측 오버헤드까지 포함해 다시 확인한다.
5. 직접 게이트와 shipcheck가 모두 통과한 뒤에만 `solver/`, 대표 zip, `STATUS.md`의
   제출 기준을 함께 바꾼다. `builds/rsv`의 A팔이 구성상 동일하다는 사실만으로 출하본과
   시간제한 탐색 분포까지 동일하다고 가정하지 않는다.
