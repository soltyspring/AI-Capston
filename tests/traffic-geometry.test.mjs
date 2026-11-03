import test from 'node:test'
import assert from 'node:assert/strict'
import {queuePath,queuePathLength,queuePoint,visibleQueue} from '../src/trafficGeometry.js'

test('queue front is nearest the correct server with 20-unit nonoverlapping slots',()=>{
  for(const id of ['A','B','C']) {
    const server={id,queued_requests:Array.from({length:32},(_,i)=>({id:i,input_tokens:100}))}
    const queue=visibleQueue(server)
    assert.equal(queue[0].distance,18)
    for(let i=1;i<queue.length;i++) assert.equal(queue[i].distance-queue[i-1].distance,20)
    assert.ok(queue.at(-1).distance<queuePathLength(id))
    assert.deepEqual(queuePoint(id,0),queuePath(id).at(-1))
    assert.deepEqual(queuePoint(id,queuePathLength(id)),queuePath(id)[0])
    assert.deepEqual(queue.map(job=>job.id),server.queued_requests.slice(0,queue.length).map(job=>job.id))
  }
})
test('empty queue draws no invented waiting requests',()=>{
  assert.deepEqual(visibleQueue({id:'A',queue:0,queued_requests:[]}),[])
})
