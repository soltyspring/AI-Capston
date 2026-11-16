"""Prompt-text output length model: TF-IDF ridge stacked into SVD gradient boosting."""
import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold

LIMIT = 2000


def clip_prompt(text):
    # Long requests carry the instruction at both ends of an attached document.
    return text if len(text) <= 3000 else text[:1500] + '\n' + text[-1500:]


def numeric(prompts, input_tokens, baseline):
    return np.column_stack([np.log1p(np.asarray(input_tokens, float)),
                            np.log1p(np.minimum(np.asarray(baseline, float), LIMIT)),
                            np.log1p([len(p) for p in prompts]), [p.count('\n') for p in prompts]])


class LengthV4:
    version = 4

    def _text(self, texts):
        return sparse.hstack([self.word.transform(texts), self.char.transform(texts)]).tocsr()

    def fit(self, prompts, input_tokens, baseline, y, seed=0):
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
        z = np.column_stack([self.svd.transform(x), numeric(prompts, input_tokens, baseline), stacked])
        self.booster = HistGradientBoostingRegressor(max_iter=900, learning_rate=.05, max_leaf_nodes=63,
                                                     l2_regularization=1.0, random_state=seed).fit(z, y)
        return self

    def predict(self, prompts, input_tokens, baseline):
        texts = [clip_prompt(p) for p in prompts]
        x = self._text(texts)
        z = np.column_stack([self.svd.transform(x), numeric(prompts, input_tokens, baseline), self.ridge.predict(x)])
        return np.clip(self.booster.predict(z), 1, LIMIT)


ROUTE_FEATURES = ('input_tokens', 'predicted_tokens', 'inflight', 'running', 'waiting', 'kv_usage')


def route_row(input_tokens, predicted, inflight, running, waiting, kv_usage):
    return [input_tokens, predicted, inflight, running or 0, waiting or 0, kv_usage or 0]
