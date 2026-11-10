# mixdog 토큰 절약 코드 발췌

[tribgames/mixdog](https://github.com/tribgames/mixdog)에서 컨텍스트·토큰 절약과 관련된 모듈만 골라 원본 경로 그대로 복사한 참고용 사본입니다.

- 원본 커밋: `04cdf91e85c4ae09fcb035779c7808c0a5081d21` (2026-10-13)
- 라이선스: Apache-2.0 ([LICENSE](LICENSE), [NOTICE.md](NOTICE.md)). 파일 내용은 수정하지 않았습니다.
- 제외한 것: Claude 구독 OAuth 관련 코드(`anthropic-oauth*.mjs`)와 테스트 파일.
- 원본 설명 문서: [docs/context-efficiency.md](docs/context-efficiency.md)

## 그대로 실행되지는 않습니다

`.mjs` 54개 중 21개만 이 폴더 안에서 import가 모두 해결됩니다. 나머지 33개는 mixdog의 다른 모듈 80곳을 참조하므로, 읽고 옮겨 쓰기 위한 참고 자료로 보세요. npm 외부 패키지 의존성은 없습니다.

## 기법별 위치

경로의 `O/`는 `src/runtime/agent/orchestrator/`입니다.

| # | 기법 | 파일 |
|---|---|---|
| 1 | 짧은 시스템 지침 | `src/rules/` (공통 12개, 역할별 4개, 합계 225줄), `O/context/role-instructions.mjs` |
| 2 | 읽기·검색 결과 캐시 | `O/session/cache/read-cache.mjs`, `scoped-cache.mjs`, `prefetch-cache.mjs`, `tool-result-reuse.mjs` |
| 3 | 코드 그래프 도구 정의 | `O/tools/code-graph-tool-defs.mjs` (Rust 엔진 본체는 원본의 `native/mixdog-graph`에 있고 여기엔 없음) |
| 4 | 프롬프트 캐시 전략 | `O/agent-runtime/cache-strategy.mjs` |
| 5 | 구조화 컴팩션 | `O/session/compact/` (`runner.mjs`, `summary.mjs`, `summary-schema.mjs`, `execution-tail.mjs`, `budget.mjs`), `O/session/pre-send-compact.mjs`, `O/session/loop/compact-policy.mjs` |
| 6 | 유휴 시 자동 컴팩션 | `src/tui/session/session-flow/auto-clear.mjs`, `O/session/manager/compaction-runner.mjs` |
| 7 | 도구·스킬 지연 로딩 | `O/context/deferred-tools.mjs`, `skill-catalog.mjs`, `skill-state.mjs` |
| 9 | 도구 결과 축소·중복 제거·파일 오프로드 | `O/session/tool-result-offload.mjs`, `context-utils.mjs`, `read-dedup.mjs`, `reduction-metrics.mjs`, `O/session/tool-batch/` |
| 10 | 자동 추론 강도 | `O/providers/auto-effort.mjs`, `O/session/loop/step-auto-effort.mjs`, `src/runtime/effort-judge/` |

8번(PostgreSQL + pgvector 장기 메모리)은 `src/runtime/memory/` 전체가 필요한 큰 서브시스템이라 가져오지 않았습니다.

## 단독으로 읽을 수 있는 파일

다른 mixdog 모듈 없이 이해되는 21개입니다. 옮겨 쓰려면 여기서 시작하는 편이 쉽습니다.

- `O/context/deferred-tools.mjs`, `collect.mjs`, `skill-state.mjs`
- `O/providers/auto-effort.mjs`
- `O/session/compact.mjs`, `context-tool-pairs.mjs`, `read-dedup.mjs`, `reduction-metrics.mjs`
- `O/session/compact/budget.mjs`, `constants.mjs`, `continuation.mjs`, `json-byte-length.mjs`, `observation-supersession.mjs`, `summary-schema.mjs`, `summary.mjs`
- `O/session/cache/text-cache-budget.mjs`
- `O/tools/code-graph-tool-defs.mjs`
- `src/runtime/effort-judge/compact-tokenizer.mjs`, `judge-input.mjs`, `judge-worker.mjs`, `verified-context-input.mjs`
