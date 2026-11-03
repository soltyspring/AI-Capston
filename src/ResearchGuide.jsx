import React, { useState } from 'react'

const fields = [
  ['timestamp', '0', '도착 시각(ms)', '원래 간격대로 재생'],
  ['input_length', '6,758', '입력 토큰 수', '입력 처리·용량·출력 예측'],
  ['output_length', '500', '실제 출력 토큰 수', '학습 정답·완료 계산 (선택 시 숨김)'],
  ['hash_ids', '[0, 1, …, 13]', '512토큰 블록 ID', '공통 입력 비교'],
]
const tabs = [['problem','문제 정의'], ['data','데이터와 전처리'], ['system','각 구성의 역할'], ['progress','진행 사항·검증']]

export default function ResearchGuide({ state }) {
  const [tab, setTab] = useState('problem')
  return <section className="panel research-guide" id="research-guide">
    <div className="panel-head"><div><span className="eyebrow">RESEARCH GUIDE</span><h2>프로젝트 안내</h2></div><a href="#live-flow">흐름도 ↑</a></div>
    <details><summary>문제 정의·데이터·구현 내용 보기</summary>
    <div role="tablist" aria-label="연구 설명" className="research-tabs">{tabs.map(([key,label]) => <button key={key} id={`guide-tab-${key}`} role="tab" aria-selected={tab === key} aria-controls={`guide-panel-${key}`} onClick={() => setTab(key)}>{label}</button>)}</div>
    <div role="tabpanel" id={`guide-panel-${tab}`} aria-labelledby={`guide-tab-${tab}`} className="research-body">
      {tab === 'problem' && <>
        <h3>같은 요청 수, 다른 작업량</h3>
        <p>입력·답변 길이와 서버의 남은 작업에 따라 처리 시간이 달라집니다.</p>
        <dl className="research-definitions"><dt>목표</dt><dd>가상 서버 3대 중 먼저 끝낼 서버 선택</dd><dt>판단 재료</dt><dd>입력·예상 출력·대기 작업·처리 한도</dd><dt>검증</dt><dd>같은 기록으로 Round Robin과 지연 비교</dd><dt>지표</dt><dd>평균 지연·완료·거절 (P50·P95는 예정)</dd></dl>
        <p className="research-warning">실제 GPU 사용률은 아직 반영하지 않습니다.</p>
      </>}
      {tab === 'data' && <>
        <h3>Mooncake 요청 기록</h3>
        <p>JSONL · 12,031건 · 약 59분. 질문 원문과 GPU 측정값은 없습니다.</p>
        <div className="table-scroll"><table><caption>실제 첫 번째 요청의 필드와 사용 위치</caption><thead><tr><th>원본 필드</th><th>예시 값</th><th>뜻</th><th>프로젝트에서 하는 일</th></tr></thead><tbody>{fields.map(row => <tr key={row[0]}>{row.map((cell,i) => i === 0 ? <th scope="row" key={i}><code>{cell}</code></th> : <td key={i}>{cell}</td>)}</tr>)}</tbody></table></div>
        <ol className="research-steps"><li><strong>검증</strong> SHA256·필드·토큰 길이·시각 순서 검사</li><li><strong>분리</strong> 학습 8,421 / 검증 1,805 / 재생 1,805건</li><li><strong>예측 입력</strong> 입력 길이·블록 수·과거 앞부분 등장 빈도</li><li><strong>재생</strong> 일반 보기: 유입 간격 조정 · 과부하 실험: 원본 간격 유지</li></ol>
        <a href="https://github.com/kvcache-ai/Mooncake/blob/main/FAST25-release/README.md" target="_blank" rel="noreferrer">Mooncake 공식 데이터 설명 ↗</a>
      </>}
      {tab === 'system' && <>
        <h3>각 구성의 역할</h3>
        <ol className="research-steps"><li><strong>재생기</strong> 기록된 시각에 요청 발생</li><li><strong>예측기</strong> 과거 기록으로 학습한 랜덤 포레스트로 출력 길이 예측 (온라인 재학습 아님)</li><li><strong>로드밸런서</strong> 입력 + 출력 예약 2,048토큰 수용 여부 확인 후 예상 시간 비교</li><li><strong>서버 A·B·C</strong> 요청을 순서대로 처리</li><li><strong>화면</strong> 분배·대기·처리 상태 표시</li></ol>
        <div className="research-formula">예상 완료 비용 = 예상 대기 + (입력 − 재사용 입력) ÷ 입력 처리 속도 + 예상 출력 ÷ 생성 속도</div>
        <p>가정값: 처리 한도 32,768토큰 · 입력 4,000 / 생성 100 tok/s. 실제 GPU 측정은 아닙니다.</p>
      </>}
      {tab === 'progress' && <>
        <h3>진행 상황</h3>
        <dl className="research-definitions"><dt>구현</dt><dd>데이터 검증·출력 예측·가상 서버·실시간 화면</dd><dt>확인 방법</dt><dd>자동 재생 → 선택 근거 → 서버 상태 → 완료 결과</dd><dt>다음 단계</dt><dd>A10G 실측·정책 비교·지연 분석·분배 정책 학습</dd><dt>미구현</dt><dd>실제 GPU 3대·GPU 사용률 연동·온라인 학습</dd></dl>
        {state?.model && <p className="research-warning">검증 MAE: 모델 {state.model.validation.mae_tokens.toFixed(1)} / 고정 400 {state.model.validation_fixed400.mae_tokens.toFixed(1)} / 학습 평균 {state.model.validation_training_mean.mae_tokens.toFixed(1)}토큰. 지연 개선은 별도 검증합니다.</p>}
      </>}
    </div>
    </details>
  </section>
}
