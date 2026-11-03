import {routePoint} from './storyTimeline.js'

export const SERVER_X = {A:170,B:500,C:830}
export const queuePath = id => [{x:500,y:425},{x:500,y:466},{x:SERVER_X[id],y:466},{x:SERVER_X[id],y:508}]
export const queuePathLength = id => 83 + Math.abs(SERVER_X[id]-500)
export const queueCapacity = id => id === 'B' ? 3 : 18
export const queuePoint = (id,distance) => routePoint(queuePath(id),1-Math.min(queuePathLength(id),Math.max(0,distance))/queuePathLength(id))
export const visibleQueue = server => (server.queued_requests || []).slice(0,queueCapacity(server.id)).map((job,index)=>({...job,distance:18+index*20}))
