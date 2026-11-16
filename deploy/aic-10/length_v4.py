"""Prompt-text output length model: TF-IDF ridge stacked into SVD gradient boosting."""
import re

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold

LIMIT = 2000
# Explicit length requests that a bag of words captures poorly ("30-section", "한 문장", "자세히").
CUES = tuple(re.compile(p, re.I) for p in (
    r'\b(?:brief(?:ly)?|short|concise|one.sentence|single sentence|one word|two sentences)\b|한\s*문장|한두\s*문장|짧게|간단히|한\s*단어|한\s*줄',
    r'\b(?:detailed|comprehensive|extensive|in.depth|step.by.step|long.form|handbook|manual|essay|guide)\b|자세히|상세히|단계별|구체적으로|심층',
    r'\b(?:in every|for each|each section|every section)\b|각\s*(?:항목|단계|섹션|장)마다',
    r'json|yaml|xml|schema|스키마',
    r'\bcode\b|python|sql|javascript|함수|코드',
    r'\btable\b|표로|표\s*형식|비교표',
    r'\b(?:bullets?|list|checklist)\b|목록|항목|체크리스트',
    r'\b(?:summar(?:y|ize)|tl;dr)\b|요약',
))
COUNT = re.compile(r'(\d{1,3})\s*[- ]?\s*(?:sections?|items?|examples?|points?|steps?|sentences?|paragraphs?|bullets?|chapters?|tips?|recommendations?|ideas?)\b'
                   r'|(\d{1,3})\s*(?:개\s*(?:항목|섹션|장|예시|문장|단계)?|가지|항목|섹션|장|문장|단계|문단)', re.I)
LIMIT_WORDS = re.compile(r'(\d{1,4})\s*(?:words?|tokens?|단어|토큰|자|글자)\s*(?:이내|이하|미만|or less|or fewer|max)?', re.I)


def clip_prompt(text):
    # Long requests carry the instruction at both ends of an attached document.
    return text if len(text) <= 3000 else text[:1500] + '\n' + text[-1500:]


def numeric(prompts, input_tokens, baseline):
    return np.column_stack([np.log1p(np.asarray(input_tokens, float)),
                            np.log1p(np.minimum(np.asarray(baseline, float), LIMIT)),
                            np.log1p([len(p) for p in prompts]), [p.count('\n') for p in prompts]])


def cues(prompts):
    rows = []
    for prompt in prompts:
        # Instructions sit at the start or end; attached documents in between add noise.
        text = prompt if len(prompt) <= 1200 else prompt[:600] + '\n' + prompt[-600:]
        counts = [int(m[1] or m[2]) for m in COUNT.finditer(text) if int(m[1] or m[2]) <= 200]
        limit = LIMIT_WORDS.search(text)
        rows.append([float(bool(p.search(text))) for p in CUES]
                    + [np.log1p(max(counts)) if counts else 0., np.log1p(int(limit[1])) if limit else 0.])
    return np.array(rows, float)


class LengthV4:
    version = 4

    def _text(self, texts):
        return sparse.hstack([self.word.transform(texts), self.char.transform(texts)]).tocsr()

    def _numeric(self, prompts, input_tokens, baseline):
        base = numeric(prompts, input_tokens, baseline)
        return np.column_stack([base, cues(prompts)]) if getattr(self, 'cues', False) else base

    def fit(self, prompts, input_tokens, baseline, y, seed=0):
        self.cues = True
        texts = [clip_prompt(p) for p in prompts]
        y = np.asarray(y, float)
        self.word = TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=120000, sublinear_tf=True).fit(texts)
        self.char = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), min_df=5,
                                    max_features=150000, sublinear_tf=True).fit(texts)
        x = self._text(texts)
        stacked = np.zeros(len(y))
        for train, hold in KFold(5, shuffle=True, random_state=seed).split(x):
            stacked[hold] = Ridge(alpha=1).fit(x[train], y[train]).predict(x[hold])
        self.ridge = Ridge(alpha=1).fit(x, y)
        self.svd = TruncatedSVD(256, random_state=seed).fit(x)
        z = np.column_stack([self.svd.transform(x), self._numeric(prompts, input_tokens, baseline), stacked])
        self.booster = HistGradientBoostingRegressor(max_iter=900, learning_rate=.05, max_leaf_nodes=63,
                                                     l2_regularization=1.0, random_state=seed).fit(z, y)
        return self

    def predict(self, prompts, input_tokens, baseline):
        texts = [clip_prompt(p) for p in prompts]
        x = self._text(texts)
        z = np.column_stack([self.svd.transform(x), self._numeric(prompts, input_tokens, baseline), self.ridge.predict(x)])
        return np.clip(self.booster.predict(z), 1, LIMIT)


ROUTE_FEATURES = ('input_tokens', 'predicted_tokens', 'inflight', 'running', 'waiting', 'kv_usage')


def route_row(input_tokens, predicted, inflight, running, waiting, kv_usage):
    return [input_tokens, predicted, inflight, running or 0, waiting or 0, kv_usage or 0]
