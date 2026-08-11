# 캠페인 원장 (08-07 ~ 08-12)

STATUS.md가 캠페인 6(08-06)에서 멈춘 뒤의 기록. 원본 증거는
`archive/campaigns-0807-0811.tar.zst`로 압축 보관(해시는 아래).

## 요약

| 캠페인 | 날짜 | 결과 | 남은 것 |
|---|---|---|---|
| `fundamental-r2` | 08-07~08 | NO_SURVIVOR — 3개 레인 전부 인과 게이트 실패, 공식 검증 미도달 | 없음 |
| `math-first-r1` | 08-08 | NO_SURVIVOR (`VALID_EMPTY_FUNNEL`), 클러스터 사용 0 | 없음 |
| `score-72h` | 08-09 | INVALID — 50축 스크린 후 동시 잡 32>캡 8로 **자원 무효**, 결과 폐기 | **아래 축 표: 유일한 실측 신호** |
| `structural-72h` | 08-10 | NO_SURVIVOR — M1~M4 전부 인컴번트 목적값 변화 0 | 없음 |
| `minimum-target` g01–g36 | 08-10~11 | 전 세대 no-go | 아래 실패 유형 |

## 왜 아무것도 안 나왔나 (근본 원인, 08-12 확정)

**측정 검정력 부족.** 고정 엔진의 공식 TL draw 산포가 **10~12%**인데
(v5.3 4시드 실측: P4 2,956,671~3,317,418 = 12.2%, P5 9,400,593~10,562,803 = 12.4%),
2~5% 효과를 **짝짓지 않은 1~3회 draw**로 2% 게이트에 걸어 판정했다. 통과할 수
없는 설계다. 스크린 통과는 운, 다음 시드에서 사망이 필연 — 킬 로그가 그대로다
("exact-events failed seeds 19 and 31").

반례: 08-06 population만 **짝지은 24쌍 AB/BA**를 썼고 깔끔한 판정을 냈다
(3W/21L, 부호검정 p=0.000277). 후보의 질이 아니라 **방법**이 갈랐다.

부수 원인: 24~30초 사전관문으로 240~900초 문제를 판정(g15~g27); empty-state
제약으로 24초에 솔버를 0에서 재발명(g35 = P4 목적값 2,447,143,563 = 목표의
1,283배); 판정축이 절대 목표뿐이라 세대 간 학습 신호 0(g28~g34가 같은 cap
실패 7회 반복); 08-11 21시간 동안 32.4 CPU-h(평균 코어 1.5개)만 사용.

→ 대책: `tools/abgate.py` (짝지은 AB/BA + 부호검정, 공식 TL, `--exclusive` 노드).

## score-72h 50축 스크린 (유일하게 살아있는 실측)

축소 TL 스크린, baseline P4 4,253,087 / P6 36,074,448, 시드 7. 생존자 8개.

| 축 | 평균Δ | P4Δ | P6Δ | env |
|---|---|---|---|---|
| only-k15 | −5.09% | −9.96% | −0.21% | `OGC_EXACT_K=15 OGC_EXACT_SLICE_ONLY=1` |
| only-k10 | −3.48% | −3.49% | −3.46% | `OGC_EXACT_K=10 OGC_EXACT_SLICE_ONLY=1` |
| events-contact | −3.34% | −4.48% | −2.20% | `OGC_EXACT_EVENTS=1 OGC_SHAPE_CONTACTS=1` |
| events-anchor | −3.16% | −4.48% | −1.83% | `OGC_ANCHORED_COLUMNS=1 OGC_EXACT_EVENTS=1` |
| events-access | −3.14% | −4.48% | −1.80% | `OGC_ACCESS_COHORT=1 OGC_EXACT_EVENTS=1` |
| events (단독) | −2.37% | −3.09% | −1.66% | `OGC_EXACT_EVENTS=1` |

공식 TL 추종(1~3 unpaired draw)에서 전부 게이트 탈락. **그 판정은 검정력
부족이므로 기각 근거로 쓸 수 없다.** 단, 이 knob들은 워킹트리 실험 엔진에만
있고 출하 v5.3에는 없다 — 그리고 v5.3이 그 엔진보다 P4/P5/P6에서 낫다
(같은 시드 짝비교 6/6 승, P4 +10~12%). 따라서 knob을 켜는 게 아니라
**기제를 v5.3에 이식**하는 것이 옳은 경로.

## minimum-target 36세대 실패 유형

| 세대 | 유형 |
|---|---|
| g01–g06 | 인프라 (`collect2: cannot find 'ld'`, Slurm 스풀 경로, int 타입 게이트가 정상 결과 폐기) |
| g07–g14 | NO_SURVIVOR — 공식 TL 실행, 목표 미달. D 레인 최선: P3 78,705 / P4 2,908,258 / P5 10,658,094 / P6 30,387,284 |
| g15–g27 | 30초 관문 사망 (처리량 초과, 신호 0, ON이 OFF보다 악화) |
| g28–g34 | cap 계열 반복 (geometry / LP time / candidate cap) |
| g35 | 150블록 완주·체커 통과, 목적값 2,447,143,563 |
| g36 | 미완 |

## 다시 열지 말 것

3D 직교 패킹(G001), 어휘 확장 in 전역 MIP(G002), ML 배정-prior(G003),
고정 15% 후미 population, 전체 베이 DFS(WB-WGD), 집합-분할/소유권 마스터,
`minimum-target`식 empty-state 재발명, 봉인·CAS·매니페스트 하네스.
