// Run with the connected Figma use_figma API, preceded by const BATCH = 0..3.
// Only creates a new page and its own new slides; existing pages are not changed.
const pageName = 'Ai 캡스톤 진행공유 10.23';
let page = figma.root.children.find(p => p.name === pageName);
if (!page) { page = figma.createPage(); page.name = pageName; }
await figma.setCurrentPageAsync(page);
await Promise.all(['Regular','Medium','Bold'].map(style => figma.loadFontAsync({family:'Noto Sans KR',style})));
const reference = await figma.getNodeByIdAsync('423:3');
const ids = [];
const black = {r:0,g:0,b:0};
const brown = {r:0.30588236,g:0.24313725,b:0.15294118};
function keep(node) { ids.push(node.id); return node; }
function text(parent, value, width, height, size=38, style='Medium') {
  const n=keep(figma.createText()); parent.appendChild(n);
  n.name=value.split('\n')[0].slice(0,45); n.fontName={family:'Noto Sans KR',style};
  n.fontSize=size; n.lineHeight={unit:'PIXELS',value:Math.round(size*1.45)};
  n.characters=value; n.textAutoResize='NONE'; n.resize(width,height);
  n.fills=[{type:'SOLID',color:black}]; return n;
}
function at(parent,value,x,y,w,h,size=38,style='Medium') {const n=text(parent,value,w,h,size,style);n.x=x;n.y=y;return n;}
function rule(parent,x,y,w) {const n=keep(figma.createLine());parent.appendChild(n);n.x=x;n.y=y;n.resize(w,0);n.strokes=[{type:'SOLID',color:brown}];n.strokeWeight=2;return n;}
function stack(parent,x,y,width) {const n=keep(figma.createAutoLayout('VERTICAL'));parent.appendChild(n);n.name='본문 / 편집 가능한 텍스트';n.fills=[];n.itemSpacing=25;n.primaryAxisSizingMode='AUTO';n.counterAxisSizingMode='FIXED';n.resize(width,100);n.x=x;n.y=y;return n;}
function row(parent,width,height=115) {const n=keep(figma.createAutoLayout('HORIZONTAL'));parent.appendChild(n);n.fills=[];n.itemSpacing=0;n.primaryAxisSizingMode='FIXED';n.counterAxisSizingMode='FIXED';n.resize(width,height);return n;}
function slide(index,title,sub='') {
  const name=`${String(index).padStart(2,'0')}_${title}`;
  if(page.children.some(n=>n.name===name)) throw new Error('Slide already exists: '+name);
  const f=keep(figma.createFrame());page.appendChild(f);f.name=name;f.resize(1920,1080);f.x=index*1974;f.y=0;
  f.fills=reference.fills;f.clipsContent=true;
  at(f,'3-2 AI 캡스톤',51,12,1000,48,30);
  rule(f,0,63,1920);rule(f,20,985,1880);
  at(f,'인하공업전문대학\n컴퓨터정보공학과 강동주, 윤석진',51,995,1000,80,26);
  at(f,String(index).padStart(2,'0'),1805,1003,90,50,30,'Regular');
  if(index){at(f,title,96,125,1728,108,64,'Bold');if(sub)at(f,sub,96,248,1728,104,38);}
  return f;
}
function note(f,value) {at(f,value,96,913,1728,62,25,'Regular');}
function points(f,items,y=375) {const s=stack(f,96,y,1728);for(const [heading,body] of items){const n=stack(s,0,0,1728);n.itemSpacing=7;text(n,heading,1728,61,42,'Bold');text(n,body,1728,110,36);}return s;}
function table(f,headers,rows,widths,y=370,height=105,size=33) {
  const s=stack(f,96,y,1728);s.itemSpacing=0;
  let r=row(s,1728,85);headers.forEach((v,i)=>text(r,v,widths[i],85,size,'Bold'));
  for(const values of rows){r=row(s,1728,height);r.paddingTop=14;r.strokes=[{type:'SOLID',color:brown}];r.strokeTopWeight=1;r.strokeBottomWeight=0;r.strokeLeftWeight=0;r.strokeRightWeight=0;values.forEach((v,i)=>text(r,v,widths[i],height-15,size));}
  return s;
}
const build = [
()=>{const f=slide(0,'LLM 개발 및 서버 분산 최적화');at(f,'LLM 개발 및\n서버 분산 최적화',100,190,1600,265,86,'Bold');at(f,'이번 주 진행 공유\n데이터 전처리 · 실험 환경 · 확인용 시뮬레이터',100,505,1650,160,44);at(f,'발표자 강동주  |  2026.10.23',100,758,1600,68,32);},
()=>{const f=slide(1,'1. 요청 개수만 나누면 왜 부족할까?','같은 한 건이어도, 읽을 입력과 만들 답변의 양이 다릅니다.');points(f,[['문제: 순번은 같아도 끝나는 시간은 다릅니다.','긴 입력·긴 답변·기존 대기 때문에 특정 서버의 응답이 늦어질 수 있습니다.'],['핵심 차이: 서버마다 지금 할 일이 다릅니다.','이미 쌓인 작업과 남아 있는 공통 입력 캐시를 함께 봐야 합니다.'],['우리의 질문: 더 먼저 끝낼 서버를 고를 수 있을까?','동일한 요청 기록에서 Round Robin과 비용 기반 선택을 비교합니다.']],350);note(f,'목표는 완료 지연 감소입니다. 모든 환경에서 성능이 개선된다고 미리 결론 내리지 않습니다.');},
()=>{const f=slide(2,'2. 우리가 만드는 것','LLM을 새로 만드는 것이 아니라, 요청을 어디로 보낼지 결정하는 분배기입니다.');
 const r=keep(figma.createAutoLayout('HORIZONTAL'));f.appendChild(r);r.fills=[];r.itemSpacing=35;r.x=96;r.y=390;
 for(const [i,title,body] of [[0,'요청 재생','Mooncake 도착 시각\n입력 길이·블록 식별자'],[1,'예측 + 분배','답변 길이 예측\n서버별 완료 비용 비교'],[2,'가상 서버 3대','A · B · C의 FIFO 큐\n컨텍스트·캐시·완료']]){if(i)text(r,'→',70,220,60,'Bold');const box=stack(r,0,0,500);box.itemSpacing=30;text(box,title,500,90,46,'Bold');text(box,body,500,180,36);}
 at(f,'서버 상태가 다시 분배 판단에 반영됩니다.',96,750,1728,85,44,'Bold');note(f,'현재는 가상 처리입니다. 실제 GPU 사용률 수집·추론 엔진 연결·연속 배칭은 다음 단계입니다.');},
()=>{const f=slide(3,'3. 데이터는 Mooncake 하나로 시작','conversation_trace.jsonl · 한 줄 = 요청 1개 · 총 12,031개 · 약 58분 57초');table(f,['원본 필드 / 예시','무엇을 뜻하나요?','어디에 쓰나요?'],[
 ['timestamp / 0','상대 도착 시각 (ms)','요청의 도착 간격 재현'],['input_length / 6,758','읽어야 할 입력 토큰','입력 비용·용량 검사·출력 예측'],['output_length / 500','실제 답변 토큰','학습 정답·완료 계산·오차 평가'],['hash_ids / [0,1,…,13]','512토큰 입력 블록 식별자','공통 앞부분 캐시 일치 확인']], [490,540,698],365,123,32);note(f,'출처: Mooncake FAST25 공개 conversation_trace.jsonl. 실제 출력은 서버 선택 시 숨깁니다.');},
()=>{const f=slide(4,'4. 전처리: 기록을 믿고 비교할 수 있게','원본 순서와 동시 도착을 보존하고, 미래의 정답이 판단에 섞이지 않게 합니다.');table(f,['단계','이번에 적용한 기준'],[
 ['① 파일 검증','고정 버전·SHA256, 필드·정수·길이·시각 순서 검사'],['② 시간순 분리','학습 8,421개 → 검증 1,805개 → 최종 재생 1,805개'],['③ 특징 생성','입력 길이·로그 길이·블록 수·학습 첫 블록 빈도'],['④ 재생·평가','출력 정답은 완료 계산에만 사용 · 큰 요청은 거절로 기록']], [400,1328],355,126,34);note(f,'입력 중앙값 6,909 / 최대 126,195토큰. 출력 중앙값 350토큰. 원본 전체 관찰 통계입니다.');},
()=>{const f=slide(5,'5. KV 캐시: 같은 앞부분을 다시 계산하지 않기','질문이 비슷해서가 아니라, 실제 입력 앞부분의 토큰이 같아야 합니다.');
 table(f,['실제 기록','입력','실제 출력','첫 블록'],[['요청 A / 0ms','6,758토큰','500토큰','0'],['요청 B / 0ms','7,322토큰','490토큰','0']], [560,390,390,388],355,115,36);
 at(f,'두 요청은 첫 512토큰을 공유합니다.',96,720,1728,70,44,'Bold');at(f,'하지만 동시에 도착했다고 바로 재사용되지는 않습니다.\n그 서버에서 계산을 마쳤고 캐시가 남아 있어야 합니다.',96,805,1728,108,36);note(f,'현재는 블록 단위 모의 캐시입니다. 완료 후 등록·LRU 퇴출, 바쁜 서버 재사용은 0으로 가정합니다.');},
()=>{const f=slide(6,'6. 답변 길이는 과거 기록으로 예측','현재는 오프라인 지도학습입니다. 스스로 정책을 학습하는 로드밸런서는 아닙니다.');points(f,[['입력: 요청이 도착했을 때 알 수 있는 값','입력 길이·로그 길이·입력 블록 수·학습 구간 첫 블록 등장 빈도'],['정답: 과거 요청이 실제로 만든 답변 길이','랜덤 포레스트를 학습하고, 새 요청의 예상 출력 토큰을 얻습니다.'],['반영: 답변 생성 비용과 뒤에 쌓일 대기 비용','예상 출력 ÷ 생성 속도를 사용합니다. 실제 출력은 선택에 쓰지 않습니다.']],350);note(f,'질문 원문이 없는 데이터입니다. 의미를 읽는 모델이나 자동 온라인 재학습은 아직 구현하지 않았습니다.');},
()=>{const f=slide(7,'7. 예상 완료 비용이 가장 낮은 서버 선택','실제 재생 예시: 입력 26,348토큰 · 예측 출력 322토큰 · 가상 시각 3,049초');
 at(f,'예상 대기 + 새 입력 처리 + 예상 답변 생성',96,365,1728,90,51,'Bold');table(f,['서버','대기','입력 처리','답변 생성','예상 합계'],[['A','3.91초','6.59초','3.22초','13.72초'],['B 선택','0.00초','6.59초','3.22초','9.81초'],['C','0.00초','6.59초','3.22초','9.81초']], [330,330,360,360,348],490,112,35);
 note(f,'B·C 동률 → 기본 순서로 B 선택. 입력+출력 예약 용량 검사 후 비교하며, 모든 시간은 모의 계산입니다.');},
()=>{const f=slide(8,'8. 시뮬레이터에서 하나씩 확인하기','보이는 값의 역할과, 요청을 고른 순간의 근거를 연결했습니다.');table(f,['화면 요소','무엇을 확인하나요?'],[
 ['움직이는 점 / 서버 큐','어디로 배정됐는지 / 몇 요청이 기다리는지'],['컨텍스트 / KV 표시','실행 중 입력 / 저장 캐시와 활성 요청의 예약량'],['선택 근거 표','배정 직전 대기·재사용·입력·생성 비용'],['완료 타임라인','완료 후 예상 출력과 실제 출력·오차 비교']], [550,1178],355,126,35);note(f,'점 이동 2초는 설명용 애니메이션입니다. GPU 사용률은 미수집, 현재 값과 선택 당시 값은 분리합니다.');},
()=>{const f=slide(9,'9. 이번 주 공유: 데이터·세팅·확인 도구','현재 코드에서 확인한 구현 범위입니다. 실제 GPU 실험 완료를 뜻하지 않습니다.');points(f,[['데이터 전처리·분석','공식 원본 검증, 네 필드 역할 정리, 시간순 학습·검증·재생 분리'],['실험 환경 구성','Python / FastAPI + React / Vite 연결, 출력 길이 예측 기준선 연결'],['확인용 시뮬레이터','가상 서버 3대의 큐·캐시·완료, 정책 전환, 선택 근거와 설명 탭']],350);note(f,'회귀 테스트 24개 통과 · 프런트 빌드 통과 (2026.10.23 재실행). 기능 검증이지 GPU 성능 검증은 아닙니다.');},
()=>{const f=slide(10,'10. 확인 결과: 예측 효과는 아직 작습니다.','MAE = 예상 출력과 실제 출력의 평균 절대 차이. 작을수록 좋습니다.');table(f,['평가 구간','고정 400','학습 평균 346','학습 모델'],[['검증 1,805개','186.20','179.30','179.55'],['최종 평가 1,805개','185.73','179.31','178.05']], [550,390,400,388],355,125,36);
 at(f,'고정값보다는 개선, 단순 평균과는 작은 차이',96,730,1728,80,44,'Bold');at(f,'예측 오차 감소와 분배 지연 감소는 별도로 검증해야 합니다.',96,825,1728,68,36);note(f,'단위: 토큰 · 동일 70/15/15 시간 분할 · random_state=42. 검증 구간에서는 단순 평균이 더 낫습니다.');},
()=>{const f=slide(11,'11. 다음 단계: 가정을 실제 측정으로 바꾸기','동일한 조건으로 비교한 뒤, 실제로 도움이 되는 범위를 확인하겠습니다.');points(f,[['① A10G 실측으로 시간·용량 모델 보정','모델·입력 길이·출력 길이별 측정. GPU 1대의 가상 3서버는 자원을 공유합니다.'],['② 정책 비교 자동화','같은 요청·예측기·서버 설정으로 Round Robin과 비용 선택을 비교합니다.'],['③ 평가·연동 확대','평균·P50·P95 지연, 완료·거절, 캐시 적용률 확인 후 실제 추론 엔진을 연결합니다.']],350);note(f,'완료 조건: 전체 큐를 비운 결과를 비교하고, 실측·가정·미구현을 구분해 보고합니다.');},
];
const first=BATCH*3, last=Math.min(build.length,first+3);
for(let i=first;i<last;i++)build[i]();
const slides=page.children.filter(n=>n.type==='FRAME').map(n=>({id:n.id,name:n.name}));
return {pageId:page.id,pageName:page.name,batch:BATCH,slides,createdNodeIds:ids};
