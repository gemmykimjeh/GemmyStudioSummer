# GemmyStudioSummer — 폴더 이동 기록 / 호환성 체크

원래 `C:\ReAct`, `C:\test_benchmark`, `C:\ReAct_feedback_loop` 세 폴더를
`C:\GemmyStudioSummer\` 아래로 이동. 아래는 이동 전에 미리 끝낸 호환성 수정과,
이동 후 확인할 사항.

## 목표 구조
```
C:\GemmyStudioSummer\
  ├─ ReAct\               (ACE 코어: ace/, llm.py, utils.py, .env=API키)
  ├─ test_benchmark\      (하네스: harness/, .venv, .venv-osworld, external/)
  └─ ReAct_feedback_loop\ (ACE 복제본 + feedback_loop)
```

## 이동 상태 (2026-07-14 기준)
- `ReAct_feedback_loop` → 이미 이동 완료 (Move-Item).
- `ReAct`, `test_benchmark` → **사용자가 GUI로 이동 예정.** 이 둘은 실행 중이던
  Claude Code 세션의 작업 디렉터리라 세션 내부에서 이동 불가였음.
  같은 드라이브(C:) 내 GUI 드래그 = rename이라 .venv/.git 포함 그대로 보존됨.

## 이동 전 미리 고친 절대경로 (전부 적용 완료)
1. `test_benchmark\harness\agents\ace_gdpval.py`  — `ace_path` → `C:\GemmyStudioSummer\ReAct`
2. `test_benchmark\harness\agents\ace_tau.py`     — `ace_path` → `C:\GemmyStudioSummer\ReAct`
3. `test_benchmark\harness\agents\ace_react.py`   — `ace_path`(+docstring) → `C:\GemmyStudioSummer\ReAct`
4. `test_benchmark\configs\loop_ab.yaml`          — `hermes_home` self-경로 → 새 위치
5. `_tau_upstream.py` / `_automation_upstream.py` / `clone_external.ps1` — docstring/주석 경로 갱신(비기능)

> `.env`(API 키)는 `ReAct\.env`에 있고 폴더째 이동되므로, 위 `ace_path`가
> 새 위치를 가리키면 어댑터가 키를 그대로 찾음.

## 이동해도 안 깨지는 것 (확인 완료)
- `external/tau-bench`, `external/AutomationBench` 경로: `Path(__file__).resolve().parents[2]`
  상대 계산 → 안전.
- git 저장소 / 서브모듈(ace-appworld): `.git/config`에 절대경로 없음,
  서브모듈 포인터 `gitdir: ../.git/modules/...` 상대 → 안전.
- 가상환경 `pyvenv.cfg`의 `home`이 이동 폴더 밖(시스템 Python312)을 가리킴 →
  `.venv\Scripts\python.exe -m ...` 방식은 이동 후에도 정상.

## ✅ 이동 후 할 일 (2026-07-14 완료)
1. **가상환경 정비 — 완료.** ⚠️ **`uv sync`는 쓰지 말 것.** 이 환경은 `uv.lock`이
   없고 설치 패키지 130+개 중 상당수(torch, transformers, sentence-transformers,
   faiss-cpu, modal, openai 등)가 `pyproject.toml`의 어떤 extra에도 선언돼 있지 않아,
   `uv sync`를 돌리면 이들을 전부 **삭제(prune)**해버림. 실제로는 아래 방식으로 정비함:
   - 콘솔 스크립트 exe(트램폴린)에 옛 경로가 박혀 `uv trampoline failed to canonicalize`
     로 전부 깨져 있었음 → **현재 버전 핀 고정 후 오프라인 재설치**로 스크립트만 재생성:
     `uv pip install --python <venv>\Scripts\python.exe --no-deps --offline --reinstall -r <핀목록>`
   - editable 설치 `agent-bench-harness`가 옛 경로(`file:///C:/test_benchmark`)를
     물고 있었음 → 새 위치에서 `uv pip install -e . --no-deps --reinstall-package agent-bench-harness` 로 재지정.
   - activate 계열(activate/.bat/.csh/.fish/.nu)의 옛 경로 문자열 치환. (Activate.ps1은 상대경로라 무관.)
   - 대상 venv 3개 모두 처리: `test_benchmark\.venv`, `test_benchmark\.venv-osworld`,
     `ReAct_feedback_loop\.venv`. pyvenv.cfg의 `home`은 셋 다 시스템 Python312라 이동과 무관하게 정상.
2. **동작 확인 — 통과.** 프로젝트 밖 디렉터리에서도 정상:
   ```
   C:\GemmyStudioSummer\test_benchmark\.venv\Scripts\python.exe -X utf8 -c "import anthropic, harness; print('ok')"
   ```
   → `harness`가 `C:\GemmyStudioSummer\test_benchmark\harness`로 해석됨. 옛 경로 exe 0개.
3. Claude Code는 새 위치(`C:\GemmyStudioSummer\test_benchmark`)에서 새로 열면 됨.

## 참고 (별개 이슈)
- Anthropic **워크스페이스 API 사용 한도**에 도달 → 2026-08-01까지 API 호출 실패.
  콘솔에서 워크스페이스 usage limit 상향하면 조기 재개 가능. (폴더 이동과 무관)
