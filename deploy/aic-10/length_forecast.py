"""Censor-aware output length candidate; fallback to the original predictor."""
import json
import math
from pathlib import Path
import re


SHORT = re.compile(r'\b(?:brief(?:ly)?|short|concise|one sentence|single sentence|one word)\b|한\s*문장|짧게|간단히|한\s*단어', re.I)
LONG = re.compile(r'\b(?:detailed|comprehensive|step.by.step|long.form|handbook|essay)\b|자세히|단계별|구체적으로', re.I)
EXACT = re.compile(r'\b(?:exactly|no more than|at most|under|within)\s+(\d{1,4})\s+(words?|tokens?)\b|(\d{1,4})\s*(토큰|단어)\s*(?:이내|이하)', re.I)

TOPICS = tuple(re.compile(p, re.I) for p in (
    r'금융|주가|투자|회계|\b(?:finance|stocks?|investment|accounting|banking)\b',
    r'프로그래밍|데이터베이스|소프트웨어|\b(?:python|javascript|sql|programming|software|database|kubernetes)\b',
    r'의료|건강|질병|\b(?:medical|medicine|health|clinical|disease)\b',
    r'법률|정책|계약|\b(?:legal|law|policy|regulation|contract)\b',
    r'교육|수업|학습|\b(?:education|teaching|school|lesson|curriculum)\b',
    r'과학|물리|수학|\b(?:science|physics|math|chemistry|biology)\b',
    r'마케팅|영업|고객|\b(?:marketing|sales|customer|business|management)\b',
    r'여행|음식|생활|\b(?:travel|cooking|recipe|fitness|lifestyle)\b',
))
FORMATS = tuple(re.compile(p, re.I) for p in (
    r'표로|표\s*형식|\b(?:table|csv|spreadsheet)\b',
    r'목록|항목별|글머리|\b(?:bullets?|bullet points?|list format|numbered list)\b',
    r'코드\s*(?:예시|예제|블록|작성)|\b(?:code (?:example|snippet|block)|write (?:a )?(?:script|function))\b',
    r'\b(?:json|xml|yaml|schema)\b|구조화된\s*형식',
    r'보고서|문단|이메일|\b(?:report|paragraphs?|e-?mail|memo|checklist)\b',
))
COUNT = re.compile(r'\b(\d{1,3})\s+(?:items?|examples?|points?|steps?|sentences?|paragraphs?|bullets?)\b|(\d{1,3})\s*(?:가지|항목|문장|단계|문단|개의\s*예)', re.I)
WORD = re.compile(r'\b[^\W\d_]{2,24}\b')


def prompt_sections(prompt):
    # Requests often put long documents between the first and last instructions.
    return prompt if len(prompt) <= 4096 else prompt[:2048] + '\n' + prompt[-2048:]


def keyword_terms(prompt):
    return set(WORD.findall(prompt_sections(prompt).casefold()))


def extra_features(prompt, vocabulary):
    text = prompt_sections(prompt)
    terms = keyword_terms(prompt)
    count = COUNT.search(text)
    return (*[float(bool(p.search(text))) for p in TOPICS],
            *[float(k in terms) for k in vocabulary],
            *[float(bool(p.search(text))) for p in FORMATS],
            math.log1p(int(count[1] or count[2])) if count else 0.)


def features(prompt, input_tokens, baseline):
    match = EXACT.search(prompt)
    limit = 0
    if match:
        limit = int(match[1] or match[3])
        if (match[2] or match[4]).lower() in ('word', 'words', '단어'):
            limit *= 2
    return (math.log1p(input_tokens), math.log1p(baseline), float(bool(SHORT.search(prompt))),
            float(bool(LONG.search(prompt))), math.log1p(limit), math.log1p(len(prompt)))


def estimate(model, prompt, input_tokens, baseline):
    if not model:
        return baseline
    xs = features(prompt, input_tokens, baseline)
    if model.get('version') == 2:
        xs += extra_features(prompt, model['vocabulary'])
    elif model.get('version') == 3:
        xs += extra_features(prompt, model['vocabulary'])[len(TOPICS):]
    value = model['intercept'] + sum(
        w * ((x - center) / scale)
        for x, center, scale, w in zip(xs, model['center'], model['scale'], model['weights']))
    return max(1, min(4096, round(math.expm1(max(0, min(math.log1p(4096), value))))))


def load_candidate(path):
    try:
        model = json.loads(Path(path).read_text(encoding='utf-8'))
        if model.get('version') in (1, 2, 3) and model.get('accepted'):
            return model
    except (OSError, ValueError):
        pass
    return None
