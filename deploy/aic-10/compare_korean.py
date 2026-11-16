"""Korean holdout: score both predictors on one generated answer per prompt."""
import argparse
import asyncio
import json
import re
import sqlite3
from statistics import mean, median

import httpx
from compare_fixed import REPORT, ROOT, trial
from length_forecast import estimate, load_candidate

SUBJECTS = ('동네 악기 연습실', '공동 우산 보관함', '야간 자율 독서실', '주말 자전거 수리소',
            '어린이 천체 관측회', '공원 씨앗 교환대')
TYPES = ('한 문장', '목록', 'JSON', '코드', '긴 문서 요약')
PATTERNS = (
    r'한\s*문장|1\s*문장',
    r'목록|\d+\s*(?:개\s*항목|가지)',
    r'\bJSON\b',
    r'(?:Python|파이썬|코드|함수).{0,40}(?:작성|구현|만들)',
    r'요약',
)


def format_group(prompt):
    instruction = prompt.split('\n', 1)[0]
    for i in (2, 3, 4, 0, 1):
        if re.search(PATTERNS[i], instruction, re.I) and (i != 4 or len(prompt) >= 800):
            return TYPES[i]
    return None


def fit_calibration(model, checkpoint):
    path = ROOT/'central-training.sqlite3'
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        samples = {i:(x,y,b) for i,x,y,b in db.execute("""
            SELECT s.request_index,s.input_tokens,s.output_tokens,e.baseline
            FROM samples s JOIN length_evaluation e USING(request_index)
            WHERE s.request_index < ? AND s.outcome='complete'
            AND e.finish_reason='stop' AND e.baseline IS NOT NULL AND s.output_tokens>0
        """, (checkpoint,))}
    residual = {key:[] for key in TYPES}
    with (ROOT/'workload'/'requests.jsonl').open(encoding='utf-8') as corpus:
        for i,line in enumerate(corpus):
            if i >= checkpoint:
                break
            if i not in samples:
                continue
            prompt = json.loads(line)['prompt']
            kind = format_group(prompt)
            if kind:
                x,y,b = samples[i]
                residual[kind].append(y-estimate(model,prompt,x,b))
    return {key:{'samples':len(values), 'offset':round(median(values)*len(values)/(len(values)+30))
                 if len(values)>=20 else 0} for key,values in residual.items()}


def prompts():
    for j, subject in enumerate(SUBJECTS):
        yield TYPES[0], f'{subject}을 처음 접하는 사람에게 운영 목적을 딱 한 문장으로 설명해 줘.'
        yield TYPES[1], f'{subject}에 처음 방문하기 전 확인할 사항을 {j+3}개 항목의 목록으로 써 줘.'
        yield TYPES[2], f'{subject}은 토요일 {j+9}시에 열고 정원은 {j+13}명이다. name, day, hour, capacity 키만 가진 JSON 객체로 변환해 줘.'
        yield TYPES[3], f'{subject}의 예약 내역에서 빈 날짜를 찾아 오름차순으로 반환하는 Python 함수를 작성해 줘. 입력과 반환 예시도 포함해 줘.'
        notes = []
        for i in range(28):
            notes.append(f'{i+1}주차: {subject} 방문자는 {18+i*3+j}명, 신규 이용자는 {4+(i+j)%9}명이었다. '
                         + ('우천으로 예약 취소가 늘었으며 다음 주에는 안내판을 보완한다.' if i%3==0 else
                            '봉사자가 접수를 지원했고, 예약 시간과 실제 이용 시간 차이를 기록했다.'))
        yield TYPES[4], f'{subject} 운영일지를 읽고 변화, 문제점, 다음 조치를 구분해 요약해 줘. 원문 수치를 근거로 사용해 줘.\n'+'\n'.join(notes)


async def run(checkpoint):
    model = load_candidate(ROOT/'cycles'/str(checkpoint)/'predictor.json')
    if not model or model.get('before') != checkpoint:
        raise RuntimeError('checkpoint predictor unavailable')
    cases = list(prompts())
    wanted = {prompt for _, prompt in cases}
    with (ROOT/'workload'/'requests.jsonl').open(encoding='utf-8') as corpus:
        if any(json.loads(line)['prompt'] in wanted for line in corpus):
            raise RuntimeError('holdout overlaps training corpus')
    rows = []
    calibration = fit_calibration(model, checkpoint)
    report = dict(status='running', checkpoint=checkpoint, total=len(cases), done=0, results=[],
                  calibration=calibration)

    def save():
        data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        data['korean_holdout'] = report
        tmp = REPORT.with_name('korean-holdout.next')
        tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        tmp.replace(REPORT)

    def summarize():
        result = []
        for kind in TYPES:
            group = [r for r in rows if r['type']==kind]
            natural = [r for r in group if r['finish_reason']=='stop' and r['output_tokens']>0]
            item = dict(type=kind, total=len(group), natural=len(natural),
                        capped=sum(r['finish_reason']=='length' for r in group))
            for key in ('original', 'candidate', 'calibrated'):
                item[key] = round(mean(abs(r[key]-r['output_tokens']) for r in natural), 2) if natural else None
                item[key+'_under'] = sum(r[key]<r['output_tokens'] for r in natural)
            result.append(item)
        return result

    save()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for kind, prompt in cases:
                row = await trial(client, dict(prompt=prompt, max_tokens=2000), 'round_robin', None)
                original = row['original_prediction']
                candidate = estimate(model, prompt, row['input_tokens'], original)
                group = format_group(prompt)
                calibrated = max(1,min(4096,candidate+calibration.get(group, {}).get('offset',0)))
                rows.append(dict(type=kind, original=original, candidate=candidate,
                                 calibrated=calibrated,
                                 output_tokens=row['output_tokens'], finish_reason=row['finish_reason']))
                report.update(done=len(rows), results=summarize())
                save()
        report['status'] = 'completed'
    except Exception as exc:
        report.update(status='interrupted', reason=str(exc)[:160])
        raise
    finally:
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=int, required=True)
    asyncio.run(run(parser.parse_args().checkpoint))
