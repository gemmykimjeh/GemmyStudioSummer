# 로컬 LLM (LM Studio + Qwen3 8B) 실행 가이드

스모크 테스트 / 프롬프트 이터레이션 전용. **점수용 아님** (약모델). 실제 A/B·ReasoningBank
비교는 API 경로로. 목적·양자화·temp=0 등 공통 스펙은 팀 노트 참조.

## 아키텍처: LLM 경로가 둘

| 경로 | 무엇 | 클라이언트 | 로컬 연결 |
|---|---|---|---|
| **ACE 브레인** | Generator/Reflector/Curator/retrieval | OpenAI SDK (`llm.py`) | **LM Studio(:1234)에 직접** |
| **에이전트 tool-loop** | ace_react/ace_tau/ace_gdpval/claude_sdk의 tool-calling | 네이티브 anthropic SDK | **LiteLLM 통역기(:4000) 경유** → LM Studio |

- ACE 브레인은 `utils.initialize_clients`에 `local`/`lmstudio` provider 추가로 배선됨
  (base_url 기본 `http://localhost:1234/v1`, `LOCAL_LLM_BASE_URL`/`LOCAL_LLM_API_KEY` env로 오버라이드).
  temp=0은 `llm.py`가 non-anthropic provider에 자동 적용.
- 에이전트 loop는 anthropic SDK가 **`ANTHROPIC_BASE_URL` env를 자동 인식** → 코드 수정 없이 통역기로 라우팅.

## 사전 준비 (각 머신 1회, GUI)

1. LM Studio 설치 — 런타임: Windows=**Vulkan**(Intel Arc), Mac=**Metal**
2. `qwen3` 검색 → **8B Q4_K_M GGUF** 다운로드 (양쪽 동일 파일)
3. 모델 로드 시 **GPU offload 최대**
4. **Developer 탭 → Start Server** (→ `http://localhost:1234`)
5. 로드된 **model id** 확인: `curl http://localhost:1234/v1/models`
   - 이 id가 `configs/litellm_local.yaml`의 `model: openai/<id>` 및 harness `--model`과 일치해야 함.
     기본값 `qwen3-8b`과 다르면 그 파일과 실행 커맨드를 맞출 것.

## 실행

**터미널 A — 통역기(에이전트 loop 돌릴 때만 필요):**
```bash
cd test_benchmark
bash scripts/run_local_proxy.sh          # :4000, 내부적으로 PYTHONUTF8=1 필요
```
> 직접 실행 시: `PYTHONUTF8=1 .venv/Scripts/litellm.exe --config configs/litellm_local.yaml --port 4000`
> (PowerShell: `$env:PYTHONUTF8=1; .venv\Scripts\litellm.exe --config configs\litellm_local.yaml --port 4000`)

**터미널 B — harness:**

로컬 provider는 **CLI 플래그가 아니라 env `ACE_API_PROVIDER=local`** 로 켠다
(ace 어댑터의 `api_provider` 기본값이 이 env를 읽음. harness.run엔 `--api-provider`가 없음).
`--model`은 LM Studio에 로드된 model id와 일치시킬 것.

```bash
cd test_benchmark
# (A) ACE 파이프라인만 로컬 — 단, grader가 있는 gdpval은 통역기도 필요(아래 B):
ACE_API_PROVIDER=local LOCAL_LLM_BASE_URL=http://localhost:1234/v1 \
  .venv/Scripts/python.exe -m harness.run --agent ace_react --model qwen3-8b \
  --benchmark browsecomp --limit 1 --concurrency 1

# (B) 에이전트 tool-loop / gdpval grader까지 로컬 (통역기 경유):
ACE_API_PROVIDER=local \
ANTHROPIC_BASE_URL=http://localhost:4000 \
LOCAL_LLM_BASE_URL=http://localhost:1234/v1 \
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  .venv/Scripts/python.exe -m harness.run --agent ace_gdpval --model qwen3-8b \
  --benchmark gdpval --limit 3 --concurrency 1
```
> 전체 플래그: `python -m harness.run --help`.

## 검증 순서

```bash
# 1) LM Studio 서버 살아있나
curl http://localhost:1234/v1/models
# 2) LM Studio 단발 chat
curl http://localhost:1234/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"qwen3-8b","messages":[{"role":"user","content":"ping"}],"temperature":0}'
# 3) 통역기 살아있나 (터미널 A 실행 후)
curl http://localhost:4000/health/liveliness
# 4) 통역기 경유 Anthropic 포맷 tool-call 스모크 (아래 참조)
# 5) harness 1태스크 end-to-end
```

## A/B 유효성 (반드시)
- 진짜 A/B 점수는 **한 대에서** baseline vs FBL을 같은 머신·모델·설정으로. 두 머신에 쪼개지 말 것(Vulkan vs Metal이 교란변수).
- 로컬=배관 검증 전용. τ-bench 실제 점수는 API 경로로.
