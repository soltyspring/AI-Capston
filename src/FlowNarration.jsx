import React, { useEffect, useState } from 'react'
import { BookOpen, CheckCircle2, MessageSquare, Scale } from 'lucide-react'

export const FLOW_STEPS = ['사용자 입력', '처리 비용 비교', '서버에 분배', '결과 기록·학습']

export function NarrationControls({ step, setStep }) {
  const [auto, setAuto] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (!auto) return
    const timer = setInterval(() => setStep(value => (value + 1) % 4), 6500)
    return () => clearInterval(timer)
  }, [auto, setStep])
  return <div className="narration-controls">
    <div><strong>동작 원리</strong><span>설명과 실제 재생은 별도입니다.</span></div>
    <div className="narration-steps" role="group" aria-label="동작 원리 설명 단계">
      {FLOW_STEPS.map((label, index) => <button key={label} aria-pressed={step === index} onClick={() => {setAuto(false); setStep(index)}}><b>{index + 1}</b>{label}</button>)}
    </div>
    <label><input type="checkbox" checked={auto} onChange={event => setAuto(event.target.checked)}/>설명 자동 재생</label>
  </div>
}

export function FlowMessages({ state, step }) {
  const latest = state.decisions.at(-1)
  const chosen = latest?.candidates.find(candidate => candidate.server === latest.selected)
  const completion = state.completions?.at(-1)
  const [typed, setTyped] = useState('')
  const example = '네이버 소개 자료를 요약해 줘.'
  useEffect(() => {
    if (step !== 0 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {setTyped(example); return}
    let length = 0
    setTyped('')
    const timer = setInterval(() => {length += 1; setTyped(example.slice(0, length)); if (length >= example.length) clearInterval(timer)}, 55)
    return () => clearInterval(timer)
  }, [step])
  return <>
    <div className={`explain-message request-message ${step === 0 ? 'is-current' : ''}`} aria-label="사용자 입력 설명">
      <span className="message-label"><MessageSquare size={16}/>01 · 사용자 입력</span>
      <blockquote>“{typed}<span className={step === 0 ? 'typing-caret' : ''}/>”</blockquote>
      <small>설명용 예시 · 데이터에는 질문 원문이 없습니다.</small>
    </div>
    <div className={`explain-message balancer-message ${step === 1 ? 'is-current' : ''}`} aria-label="서버 비교 설명">
      <span className="message-label"><Scale size={16}/>02 · 어디서 먼저 끝날까요?</span>
      <div className="cost-recipe">대기 + 입력 처리 + 답변 생성</div>
      <small>서버 상태와 요청 크기로 예상 시간을 비교합니다.</small>
    </div>
    <div className={`explain-message dispatch-message ${step === 2 ? 'is-current' : ''}`} aria-label="요청 분배 설명">
      <span className="message-label"><CheckCircle2 size={16}/>03 · 서버에 분배</span>
      <p>{latest ? latest.selected ? `요청 #${latest.id + 1} → 서버 ${latest.selected}` : `요청 #${latest.id + 1}: 용량 초과` : '요청을 기다리는 중입니다.'}</p>
      <small>{chosen ? `대기 ${chosen.wait.toFixed(2)} + 입력 ${chosen.prefill.toFixed(2)} + 생성 ${chosen.decode.toFixed(2)} = ${chosen.total.toFixed(2)}초` : '비용 기반: 가장 빠른 서버 · Round Robin: 순번'}</small>
    </div>
    <div className={`learning-message ${step === 3 ? 'is-current' : ''}`}>
      <span className="message-label"><BookOpen size={17}/>04 · 결과 확인</span>
      <p>{completion ? `#${completion.id + 1}: 예상 ${completion.predicted_output_tokens.toLocaleString()} → 실제 ${completion.output_tokens.toLocaleString()}토큰` : '완료 후 답변 길이와 지연을 확인합니다.'}</p>
      <small>현재: 출력 길이 예측·오차 확인 · 향후: 결과를 활용한 분배 정책 학습</small>
    </div>
  </>
}
