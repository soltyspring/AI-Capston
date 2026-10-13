# AI-Capston — 토큰 기반 로드밸런싱 시뮬레이터

입력 길이, 예상 출력, 대기 작업, 공통 입력 KV 캐시로 가상 서버 3대 중
예상 완료 비용이 가장 낮은 서버를 선택합니다. 실제 LLM/GPU를 실행하는 버전은 아닙니다.

## 백엔드 실행

프로젝트 루트에서 Python 3.10+, Node 22.12+ 환경으로 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Windows 활성화 명령: `.venv\Scripts\Activate.ps1`.
API 문서: `http://127.0.0.1:8000/docs`. 단일 프로세스로 실행하세요(`--workers` 사용 금지).
인증 없는 개발 API이므로 8000 포트를 외부에 직접 공개하지 마세요.

## Frontend 실행

다른 터미널에서:

```bash
npm install
npm run dev -- --host 0.0.0.0
```

프로덕션 빌드:

```bash
npm run build
```

`http://localhost:5173`에서 시작/일시정지/1초 진행/초기화를 사용할 수 있습니다.
정책을 고른 뒤 **초기화 / 정책 적용**을 누르면 동일 초기 상태로 비교합니다.
EC2의 5173 접근은 본인 IP로 제한하세요. 이 요청에서는 서버에 배포하지 않았습니다.
Vite가 `/api`를 로컬 8000 포트로 전달합니다. 프로덕션 배포에는 리버스 프록시가 필요합니다.

## 구조

```text
backend/core.py         데이터 검증, 예측 기준선, 서버·캐시·분배·시뮬레이션
backend/api.py          상태 조회, 실행 제어, 시간 진행
backend/experiment.py   시간순 학습/평가 및 정책 비교
src/SimulatorApp.jsx    API 기반 새 화면
src/LegacyDashboard.jsx 이전 임의 수치 데모 보관 (기본 화면에서 미사용)
data/demo.jsonl         자체 생성한 8개 동작 확인 요청
tests/                  캐시, 제한, 미래 정보 누출, API 테스트
```

## 계산 및 가정

- 비용(초) = 예상 대기 + (입력 - 재사용 토큰)/입력 속도 + 예상 출력/생성 속도.
- 서버당 단일 작업 실행과 FIFO 큐. 연속 배칭, prefill/decode 간섭은 미구현입니다.
- 입력 4000토큰/초, 출력 100토큰/초는 설명용 가정값이며 A10G 실측값이 아닙니다.
- KV 용량 32768토큰, 출력 예약 한도 2048토큰입니다. 실제 VRAM 바이트 모델이 아닙니다.
- GPU 상태는 실행/유휴만 모델링하며 실제 사용률은 측정하지 않습니다.
- 실제 출력 길이는 시뮬레이터의 완료 시각 계산에만 사용하고, 라우터에는 예측값만 전달합니다.
- 캐시는 완료 후 등록합니다. 전체 앞부분 키가 일치해야 하며 마지막 불완전 블록은 제외합니다.
- 즉시 시작하는 요청만 현재 캐시를 재사용합니다. 대기 작업의 미래 캐시 적중은 추정하지 않습니다.
- 활성 작업의 재사용 블록은 보호하고, 다른 블록은 용량 제한에 따라 LRU 퇴출합니다.
- 동일 모델·처리 조건의 단일 서비스를 가정합니다. 실제 서비스에는 사용자 권한 격리가 필요합니다.
- 지연 평균은 완료 요청만 집계합니다. 반복 점 애니메이션은 마지막 선택 경로 안내입니다.

## Mooncake 연결과 출력 길이 학습

기본 화면은 고정 400토큰 예측 + 합성 요청입니다. 실제 원본은 Git에 포함하지 않습니다.

```bash
curl -L https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/FAST25-release/traces/conversation_trace.jsonl -o data/conversation_trace.jsonl
python -m backend.experiment data/conversation_trace.jsonl
```

학습 기준선을 대시보드에 연결(Ubuntu):

```bash
AICAP_TRACE=data/conversation_trace.jsonl AICAP_TRAIN=1 python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

PowerShell은 `$env:AICAP_TRACE='data/conversation_trace.jsonl'`, `$env:AICAP_TRAIN='1'`을 먼저 설정합니다.
학습은 입력 길이 구간별 평균을 구하는 지도학습 기준선입니다. 딥러닝/강화학습은 아직 아닙니다.
시간순 앞 70%를 학습하고 뒤 15%만 재생합니다. 시각은 원본의 상대 시각입니다.
데이터 형식과 전처리: [data/README.md](data/README.md).

## 검증 및 다음 단계

```bash
python -m unittest discover -s tests -v
npm run build
```

다음 단계: A10G 처리 속도·KV 바이트 실측, 예측 고도화, 배칭 모델,
실제 GPU 상태 수집, 정책별 완료 지연·거절률 비교.

커밋 규칙: `[Feat] 기능 추가`, `[Fix] 버그 수정`, `[Docs] 문서 추가`, `[Test] 테스트 추가`.
