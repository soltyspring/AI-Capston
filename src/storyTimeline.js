export const STORY_QUESTION = '네이버 소개 자료를 요약해 줘.'
export const STORY_QUESTIONS = [
  {text:STORY_QUESTION, label:'자료 요약'},
  {text:'이번 분기 매출에서 달라진 점을 찾아 줘.', label:'보고서 분석'},
  {text:'이 코드가 어떻게 동작하는지 설명해 줘.', label:'코드 설명'},
  {text:'두 서비스의 장단점을 비교해 줘.', label:'정보 비교'},
  {text:'이 문서를 자연스러운 영어로 번역해 줘.', label:'문서 번역'},
]
export const storyQuestion = frame => STORY_QUESTIONS[(frame.cycle || 0) % STORY_QUESTIONS.length]
export const storyDuration = frame => frame.phase === 'typing' ? storyQuestion(frame).text.length * 95 + 550 : STORY_DURATIONS[frame.phase]
export const STORY_PHASES = ['typing', 'inbound', 'input', 'output', 'wait', 'compare', 'dispatch', 'processing', 'complete', 'live']
export const STORY_NUMBER_DURATION = {fact:650,cost:1000,stagger:120}
export const STORY_DURATIONS = {typing:STORY_QUESTION.length * 95 + 550, inbound:1100, input:850, output:850, wait:850, compare:1500, dispatch:1500, processing:2000, complete:3500, live:2000}

// Choose an unused real decision, preferring a visibly different comparison.
// Never change its selected server or manufacture new token counts.
export function nextStoryRequest(decisions, used, previous) {
  const available = [...decisions].reverse().filter(d => d.selected && !used.has(d.id))
  return available.find(d => d.selected !== previous?.selected && d.input_tokens !== previous?.input_tokens && d.predicted_output_tokens !== previous?.predicted_output_tokens)
    || available.find(d => d.input_tokens !== previous?.input_tokens && d.predicted_output_tokens !== previous?.predicted_output_tokens)
    || available[0] || null
}

export function advanceStory(frame, delta, {hasDecision, hasCompletion, hasAnimationFinished=true}) {
  const elapsed = frame.elapsed + Math.max(0, Math.min(delta, 200))
  if (elapsed < storyDuration(frame)) return {...frame, elapsed}
  if (frame.phase === 'live') return {phase:'typing',elapsed:0,cycle:(frame.cycle || 0)+1}
  if (frame.phase === 'typing' && !hasDecision) return {...frame, elapsed}
  if (frame.phase === 'processing' && (!hasCompletion || !hasAnimationFinished)) return {...frame, elapsed}
  return {...frame,phase:STORY_PHASES[STORY_PHASES.indexOf(frame.phase) + 1], elapsed:0}
}

export function storyProgress(frame) {
  return Math.min(1, frame.elapsed / (storyDuration(frame) || 1))
}

// Presentation-only count-up: the target always comes from the frozen decision.
// Frame time means pause/resume and question changes also pause/reset these numbers.
export function storyNumber(frame, phase, target, {delay=0,duration=STORY_NUMBER_DURATION.fact}={}) {
  const current=STORY_PHASES.indexOf(frame.phase), start=STORY_PHASES.indexOf(phase)
  if(current<start || target == null) return null
  if(current>start) return target
  const t=Math.max(0,Math.min(1,(frame.elapsed-delay)/duration))
  return target*(1-(1-t)**3)
}

export function serverSelectionReason(request) {
  if(!request?.selected) return ''
  if(request.selection_reason==='tie_round_robin') return `최저 비용 동점 · ${request.tie_servers.join('/')} 순환 배정`
  const others=request.candidates.filter(c=>c.eligible && c.server!==request.selected)
  if(!others.length) return `처리 가능한 서버가 ${request.selected}뿐`
  return '가장 빨리 끝날 것으로 예상되는 서버로 요청을 전달합니다.'
}

export function storyExplanation(frame,request) {
  const index=STORY_PHASES.indexOf(frame.phase)
  if(index<2) return null
  if(index<5) return {key:'prepare',title:'요청 확인',text:'입력 길이와 예상 답변 길이, 서버의 남은 작업을 확인합니다.'}
  if(index===5) return {key:'compare',title:'서버 비교',text:'처리 가능한 서버의 예상 완료 시간을 비교합니다.'}
  if(index<8) return {key:'selected',title:`서버 ${request?.selected || '—'} 선택`,text:serverSelectionReason(request)}
  return {key:'done',title:'처리 결과 확인',text:'처리 결과를 확인하고 다음 요청을 준비합니다.'}
}

export function routePoint(points, progress) {
  const lengths = points.slice(1).map((p,i) => Math.hypot(p.x-points[i].x,p.y-points[i].y))
  let remaining = Math.max(0, Math.min(1, progress)) * lengths.reduce((sum,n) => sum+n,0)
  for (let i=0;i<lengths.length;i++) {
    if (remaining <= lengths[i]) {
      const ratio = lengths[i] ? remaining / lengths[i] : 0
      return {x:points[i].x+(points[i+1].x-points[i].x)*ratio,y:points[i].y+(points[i+1].y-points[i].y)*ratio}
    }
    remaining -= lengths[i]
  }
  return points.at(-1)
}
