import test from 'node:test'
import assert from 'node:assert/strict'
import {advanceStory, nextStoryRequest, routePoint, STORY_DURATIONS, STORY_PHASES, STORY_QUESTIONS, STORY_NUMBER_DURATION, storyQuestion, storyDuration, storyNumber, serverSelectionReason} from '../src/storyTimeline.js'

function finish(frame, gates={hasDecision:true,hasCompletion:true}) {
  for(let i=0;i<100 && frame.phase!=='live';i++) {
    const next=advanceStory(frame,100,gates)
    if(next.phase!==frame.phase) return next
    frame=next
  }
  return frame
}
test('animated metrics start at zero, are monotonic and settle at the real target',()=>{
  assert.equal(storyNumber({phase:'typing',elapsed:0},'input',1492),null)
  assert.equal(storyNumber({phase:'input',elapsed:0},'input',1492),0)
  let previous=0
  for(let elapsed=0;elapsed<=1000;elapsed+=20) {
    const value=storyNumber({phase:'compare',elapsed},'compare',13.72,{duration:1000})
    assert.ok(value>=previous && value<=13.72)
    previous=value
  }
  assert.equal(previous,13.72)
  assert.equal(storyNumber({phase:'dispatch',elapsed:0},'compare',9.81),9.81)
  assert.equal(storyNumber({phase:'wait',elapsed:300},'wait',0),0)
  assert.equal(storyNumber({phase:'typing',elapsed:0,cycle:2},'compare',9.81),null)
  assert.equal(storyNumber({phase:'compare',elapsed:100},'compare',9.81,{delay:240}),0)
})
test('original fast count-ups finish before their phase ends',()=>{
  assert.equal(STORY_NUMBER_DURATION.fact,650)
  assert.equal(STORY_NUMBER_DURATION.cost,1000)
  for(const phase of ['input','output','wait']) assert.ok(STORY_DURATIONS[phase]>STORY_NUMBER_DURATION.fact)
  assert.ok(STORY_DURATIONS.compare>STORY_NUMBER_DURATION.cost+2*STORY_NUMBER_DURATION.stagger)
  assert.equal(storyNumber({phase:'input',elapsed:650},'input',1492),1492)
})
test('selection explanations use the real total, exact ties and eligibility',()=>{
  const request={selected:'B',candidates:[{server:'A',eligible:true,total:13.72},{server:'B',eligible:true,total:9.81},{server:'C',eligible:true,total:11.81}]}
  assert.match(serverSelectionReason(request),/예상되는 서버/)
  assert.doesNotMatch(serverSelectionReason(request),/초 빠름|다음 서버보다/)
  assert.match(serverSelectionReason({...request,selection_reason:'tie_round_robin',tie_servers:['B','C']}),/B\/C 순환 배정/)
  assert.match(serverSelectionReason({...request,candidates:request.candidates.map(c=>({...c,eligible:c.server==='B'}))}),/서버가 B뿐/)
})
test('steps follow typing, movement, comparison, dispatch and completion in order',()=>{
  let frame={phase:'typing',elapsed:0}
  const phases=[frame.phase]
  while(frame.phase!=='live') {frame=finish(frame);phases.push(frame.phase)}
  assert.deepEqual(phases,STORY_PHASES)
})
test('no request is sent before typing finishes and a real decision exists',()=>{
  let frame={phase:'typing',elapsed:STORY_DURATIONS.typing}
  frame=advanceStory(frame,100,{hasDecision:false,hasCompletion:false})
  assert.equal(frame.phase,'typing')
  assert.equal(advanceStory(frame,100,{hasDecision:true,hasCompletion:false}).phase,'inbound')
})
test('completion waits for the actual matching completion record',()=>{
  let frame={phase:'processing',elapsed:STORY_DURATIONS.processing}
  frame=advanceStory(frame,100,{hasDecision:true,hasCompletion:false})
  assert.equal(frame.phase,'processing')
  assert.equal(advanceStory(frame,100,{hasDecision:true,hasCompletion:true}).phase,'complete')
})
test('large background-frame gaps cannot skip the sequence',()=>{
  assert.equal(advanceStory({phase:'typing',elapsed:0},100000,{hasDecision:true,hasCompletion:true}).elapsed,200)
})
test('live flow repeats with the next question and preserves its cycle through phases',()=>{
  const next=advanceStory({phase:'live',elapsed:STORY_DURATIONS.live,cycle:0},100,{hasDecision:true,hasCompletion:true})
  assert.deepEqual(next,{phase:'typing',elapsed:0,cycle:1})
  assert.notEqual(storyQuestion(next).text,storyQuestion({cycle:0}).text)
  assert.equal(advanceStory({...next,elapsed:storyDuration(next)},100,{hasDecision:true,hasCompletion:true}).cycle,1)
  assert.equal(storyQuestion({cycle:STORY_QUESTIONS.length}).text,storyQuestion({cycle:0}).text)
})
test('each question captures an unused real decision and prefers different metrics and server',()=>{
  const old={id:1,selected:'A',input_tokens:100,predicted_output_tokens:200}
  const b={id:2,selected:'B',input_tokens:300,predicted_output_tokens:250}
  const a={id:3,selected:'A',input_tokens:400,predicted_output_tokens:350}
  assert.equal(nextStoryRequest([old,b,a],new Set([1]),old),b)
  assert.equal(nextStoryRequest([old,b,a],new Set([1,2]),b),a)
  assert.equal(nextStoryRequest([old],new Set([1]),old),null)
})
test('completion is not shown until the one-percent replay finishes',()=>{
  const frame={phase:'processing',elapsed:3000}
  assert.equal(advanceStory(frame,50,{hasDecision:true,hasCompletion:true,hasAnimationFinished:false}).phase,'processing')
  assert.equal(advanceStory(frame,50,{hasDecision:true,hasCompletion:true,hasAnimationFinished:true}).phase,'complete')
})
test('packet follows line segments and ends exactly at the selected server',()=>{
  const path=[{x:500,y:425},{x:500,y:466},{x:170,y:466},{x:170,y:508}]
  assert.deepEqual(routePoint(path,0),path[0])
  assert.deepEqual(routePoint(path,1),path.at(-1))
  for(let p=0;p<=1;p+=.01) {
    const dot=routePoint(path,p)
    assert.ok((dot.x===500 && dot.y>=425 && dot.y<=466) || (dot.y===466 && dot.x>=170 && dot.x<=500) || (dot.x===170 && dot.y>=466 && dot.y<=508))
  }
})
