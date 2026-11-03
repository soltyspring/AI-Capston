import React from 'react'

const n = value => Number(value).toLocaleString()
const tok = value => `${n(value)} 토큰`

export default function ConsiderationPanel({state}) {
  const latest = state.decisions.at(-1)
  const chosen = latest?.candidates.find(c => c.server === latest.selected)
  const [a] = state.servers
  if (!a.factors) return <section className="panel factors-panel"><h2>고려 요소 현황</h2><p>새 항목을 불러오려면 백엔드를 재시작하세요.</p></section>
  const rows = [
    ['대기 요청 수', s => `${n(s.factors.queue)}개`, '서버 상태', '대기 시간에 반영'],
    ['대기 중인 입력 합계', s => tok(s.factors.queued_input_tokens), '서버 상태', '대기 작업 규모 확인'],
    ['처리 중 입력 컨텍스트', s => tok(s.factors.context_tokens), '서버 상태', '메모리 예약에 반영'],
    ['현재 생성된 출력', s => tok(s.factors.generated_tokens), '시뮬레이션', '현재 처리 상황 표시'],
    ['현재 컨텍스트 (입력 + 생성)', s => tok(s.factors.live_context_tokens), '시뮬레이션', '컨텍스트 증가량 표시'],
    ['입력 / 생성 속도', s => `${n(s.factors.prefill_rate)} / ${n(s.factors.decode_rate)} tok/s`, '가정값', '처리 시간 식에 사용 · GPU 실측 아님'],
    ['GPU 실제 사용률', () => '미수집', '미구현', '실제 GPU 연결 전 · 비용 식에 미반영'],
  ]
  return <section className="panel factors-panel" id="factors"><div className="panel-head"><div><span className="eyebrow">ROUTING FACTORS</span><h2>고려 요소 현황</h2><p>현재 {state.time.toFixed(2)}초</p></div></div>
    <div className="factor-summary">{[
      ['마지막 요청 입력', latest ? tok(latest.input_tokens) : '요청 대기', 'Mooncake 실제 기록'],
      ['예상 출력', latest ? tok(latest.predicted_output_tokens) : '요청 대기', state.predictor === 'fixed-400' ? '학습 비활성 · 고정 기준선' : state.model?.algorithm === 'RandomForestRegressor' ? 'Mooncake 학습 · 랜덤 포레스트 예측' : '학습 구간의 통계 기준선'],
    ].map(([label,value,note]) => <article key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</div>
    {state.model && <div className="prediction-report"><strong>출력 토큰 예측 모델 · {state.model.algorithm}</strong>
      <div className="prediction-scores"><span>검증 MAE <b>{state.model.validation.mae_tokens.toFixed(1)} 토큰</b></span><span>고정 400 기준 MAE <b>{state.model.validation_fixed400.mae_tokens.toFixed(1)} 토큰</b></span><span>학습 평균 {state.model.training_mean_tokens} 기준 MAE <b>{state.model.validation_training_mean.mae_tokens.toFixed(1)} 토큰</b></span><span>재생 중 완료 요청 MAE <b>{state.prediction_mae_tokens == null ? '완료 대기' : `${state.prediction_mae_tokens.toFixed(1)} 토큰`}</b></span></div>
      <details><summary>예측 모델 설명</summary><p>시간순 학습 {n(state.model.train_requests)} / 검증 {n(state.model.validation_requests)} / 재생 {n(state.model.test_requests)}건. 입력 길이·블록 수·과거 앞부분 빈도로 예측합니다.</p><p>MAE = 평균 절대 오차. 실시간 값은 완료 {n(state.completed)}건 기준이며 검증 구간과 다릅니다. 예측 정확도와 분배 성능은 별도 평가합니다.</p>
      {chosen && latest && <p>생성 {chosen.decode.toFixed(2)}초 + 대기 {chosen.wait.toFixed(2)}초 + 입력 {chosen.prefill.toFixed(2)}초</p>}</details>
    </div>}
    <div className="table-scroll"><table><caption>서버별 상태 · 토큰 기준 (실제 GPU 메모리 아님)</caption><thead><tr><th>항목</th>{state.servers.map(s => <th key={s.id}>서버 {s.id}</th>)}<th>종류 / 반영 방법</th></tr></thead><tbody>{rows.map(([label,value,type,explanation]) => <tr key={label}><th scope="row">{label}</th>{state.servers.map(s => <td key={s.id}>{value(s)}</td>)}<td><span className={`factor-tag ${type === '미구현' ? 'unavailable' : ''}`}>{type}</span><small>{explanation}</small></td></tr>)}</tbody></table></div>
    {latest && <details className="factor-decision"><summary>요청 #{latest.id + 1} 선택 당시 비교</summary><p>입력 {tok(latest.input_tokens)} + 출력 예약 2,048토큰. 실제 출력은 선택 시 숨깁니다.</p>
      <div className="table-scroll"><table><thead><tr><th>서버</th><th>당시 대기</th><th>대기 입력</th><th>처리 중 입력</th><th>예상 합계</th></tr></thead><tbody>{latest.candidates.map(c => <tr className={c.server === latest.selected ? 'chosen' : ''} key={c.server}><th>{c.server}{c.server === latest.selected ? ' 선택' : ''}</th><td>{n(c.observation?.queue ?? 0)}개</td><td>{tok(c.observation?.queued_input_tokens ?? 0)}</td><td>{tok(c.observation?.context_tokens ?? 0)}</td><td>{c.eligible ? `${c.total.toFixed(2)}초` : '용량 초과'}</td></tr>)}</tbody></table></div>
    </details>}
  </section>
}
