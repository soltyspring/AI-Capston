import React, { useEffect, useState } from 'react'

const number = value => Number(value).toLocaleString()

function Flow({ state }) {
  const latest = state.decisions.at(-1)
  const chosen = ['A', 'B', 'C'].indexOf(latest?.selected)
  return <section className="panel simulation-flow"><h2>요청 분배 흐름</h2><p>점은 마지막 선택 경로의 반복 안내입니다. 실제 요청 개수·속도가 아닙니다.</p>
    <svg viewBox="0 0 1000 310" role="img" aria-label={`마지막 선택 서버 ${latest?.selected ?? '없음'}`}>
      <rect x="400" y="10" width="200" height="55" rx="8"/><text x="500" y="44">사용자 요청</text>
      <rect x="370" y="100" width="260" height="55" rx="8"/><text x="500" y="134">로드밸런서</text>
      <path d="M500 65 V100"/>
      {state.servers.map((s, i) => { const x = 170 + i * 330; return <g key={s.id}>
        <path id={`sim-route-${s.id}`} d={`M500 65 V190 H${x} V240`}/>
        <rect className={latest?.selected === s.id ? 'selected-node' : ''} x={x - 140} y="240" width="280" height="60" rx="8"/>
        <text x={x} y="265">GPU 서버 {s.id} · 대기 {s.queue}개</text><text className="flow-detail" x={x} y="286">완료 {s.completed}개 · 캐시 {s.cache_tokens} 토큰</text>
      </g> })}
      {chosen >= 0 && state.running && <circle key={latest.id} r="7" fill="#159b70"><animateMotion dur="2s" repeatCount="indefinite"><mpath href={`#sim-route-${latest.selected}`}/></animateMotion></circle>}
    </svg>
  </section>
}

export default function SimulatorApp() {
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  const [policy, setPolicy] = useState('cost')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true, timer
    async function poll() {
      try {
        const response = await fetch('/api/state')
        if (!response.ok) throw new Error('API 조회 실패')
        const data = await response.json()
        if (alive) { setState(data); setError('') }
      } catch {
        if (alive) setError('백엔드 연결을 확인하세요. python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000')
      } finally { if (alive) timer = setTimeout(poll, 500) }
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  async function command(action) {
    setBusy(true)
    try {
      const response = await fetch(action === 'step' ? '/api/step' : '/api/control', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action === 'step' ? { seconds: 1 } : { action, policy }),
      })
      if (!response.ok) throw new Error(`요청 실패 (${response.status})`)
      const data = await response.json()
      setState(previous => ({ ...previous, ...data })); setError('')
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  const latest = state?.decisions.at(-1)
  return <main className="simulation-app">
    <header><div><h1>토큰 기반 LLM 로드밸런싱</h1><p>가상 GPU 서버 3대 · 대기 + 입력 처리 + 답변 생성</p></div><span className="simulation-badge">SIMULATION · 실제 GPU 측정 아님</span></header>
    <div className="content">
      <div className="simulation-controls">
        <label>분배 정책 <select value={policy} onChange={e => setPolicy(e.target.value)} disabled={state?.running || busy}><option value="cost">예상 완료 비용 최소</option><option value="round_robin">Round Robin</option></select></label>
        <button disabled={busy || !state} onClick={() => command('reset')}>초기화 / 정책 적용</button>
        <button disabled={busy || !state || state.finished} onClick={() => command(state.running ? 'pause' : 'start')}>{state?.running ? '일시정지' : '시작'}</button>
        <button disabled={busy || !state || state.running || state.finished} onClick={() => command('step')}>1초 진행</button>
        <span>시뮬레이션 {state?.time.toFixed(2) ?? '0'}초 · 적용 정책 {state?.policy ?? '—'}</span>
      </div>
      {error && <p role="alert" className="simulation-error">{error}</p>}
      <p>데이터: {state?.source ?? '접속 확인 중'} · 기본 demo.jsonl은 직접 만든 동작 확인용 8개 요청입니다. Mooncake 전환은 README를 참고하세요.</p>
      {state && <>
        <Flow state={state}/>
        <section className="metrics-grid below-flow">{[
          ['도착 요청', `${state.processed} / ${state.total}`], ['완료 요청', state.completed],
          ['제한 초과 거절', state.rejected], ['평균 완료 지연', `${state.avg_latency.toFixed(2)}초`],
        ].map(([label, value]) => <article className="metric-card" key={label}><div>{label}</div><div className="metric-value">{value}</div></article>)}</section>
        <p>평균 완료 지연: 완료된 요청의 도착부터 완료까지 걸린 시간입니다. 실제 GPU 측정이 아닙니다.</p>
        <section className="server-grid">{state.servers.map(s => <article className="panel" key={s.id}>
          <h2>서버 {s.id}</h2><dl><dt>대기 요청</dt><dd>{s.queue}개</dd><dt>대기 입력</dt><dd>{number(s.queued_input_tokens)} 토큰</dd><dt>처리 중 입력 컨텍스트</dt><dd>{number(s.context_tokens)} 토큰</dd><dt>저장된 공통 입력 캐시</dt><dd>{number(s.cache_tokens)} 토큰</dd><dt>모델링한 KV 점유율</dt><dd>{s.memory}%</dd><dt>처리 중</dt><dd>{s.gpu ? '예' : '아니요'}</dd></dl>
        </article>)}</section>
        <section className="panel decision-panel"><h2>마지막 요청의 선택 근거</h2>
          {latest ? <><p>입력 {number(latest.input_tokens)} 토큰 · 예상 출력 {latest.predicted_output_tokens} 토큰 ({state.predictor}) · {latest.selected ? `서버 ${latest.selected} 선택` : '처리 가능한 서버 없음 → 거절'}</p>
            <div className="table-scroll"><table><thead><tr>{['서버', '가능 여부', '재사용 입력', '대기', '입력 처리', '답변 생성', '예상 합계'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{latest.candidates.map(c => <tr key={c.server} className={c.server === latest.selected ? 'chosen' : ''}><td>{c.server}</td><td>{c.eligible ? '가능' : '메모리 제한'}</td><td>{number(c.cached_tokens)} 토큰</td>{['wait', 'prefill', 'decode', 'total'].map(k => <td key={k}>{c[k].toFixed(2)}초</td>)}</tr>)}</tbody></table></div>
          </> : <p>시작 또는 1초 진행을 눌러 요청을 분배하세요.</p>}
        </section>
        <section className="panel decision-panel"><h2>최근 분배 기록</h2><div className="table-scroll"><table><thead><tr><th>요청</th><th>도착 시각</th><th>입력 토큰</th><th>예상 출력</th><th>분배 서버</th></tr></thead><tbody>{[...state.decisions].reverse().map(d => <tr key={d.id}><td>#{d.id + 1}</td><td>{d.time.toFixed(2)}초</td><td>{number(d.input_tokens)}</td><td>{d.predicted_output_tokens}</td><td>{d.selected ?? '거절'}</td></tr>)}</tbody></table></div></section>
      </>}
    </div>
  </main>
}
