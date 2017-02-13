#!/usr/bin/env python3
# coding: utf-8
"""
File: word2vec.py
Author: Lukas Galke
Email: vim@lpag.de
Github: https://github.com/lgalke
Description: Embedding-based retrieval techniques.
"""
from sklearn.base import BaseEstimator
from sklearn.feature_extraction.text import CountVectorizer, TfidfTransformer
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize, Normalizer
from sklearn.pipeline import Pipeline, make_pipeline
from gensim.similarities import WmdSimilarity
# from scipy.spatial.distance import cosine
import numpy as np

try:
    from .base import RetrievalBase, RetriEvalMixin, Matching
    # from .utils import argtopk
    from .utils import filter_vocab, argtopk
    from .combination import CombinatorMixin
    from .core import EmbeddedVectorizer

except (ValueError, SystemError):
    from base import RetrievalBase, RetriEvalMixin, Matching
    from combination import CombinatorMixin

default_analyzer = CountVectorizer().build_analyzer()


class StringSentence(object):
    """
    Uses analyze_fn to decompose strings into words
    analyze_fn : callable to for analysis :: string -> [string]
    documents : iterable of documents
    c : the window size
    >>> documents = ["The quick brown fox jumps over the lazy dog"]
    >>> from sklearn.feature_extraction.text import CountVectorizer
    >>> analyze_fn = CountVectorizer().build_analyzer()
    >>> analyze_fn(documents[0]) == documents[0].lower().split()
    True
    >>> sentences = StringSentence(documents, analyze_fn, 3)
    >>> x = list(sentences)
    >>> len(x)
    3
    >>> x[2]
    ['the', 'lazy', 'dog']
    >>> sentences = StringSentence(documents, analyze_fn, 5)
    >>> x = list(sentences)
    >>> len(x)
    2
    >>> x[0]
    ['the', 'quick', 'brown', 'fox', 'jumps']
    >>> x[1]
    ['over', 'the', 'lazy', 'dog']
    """
    def __init__(self, documents, analyze_fn=None, max_sentence_length=10000):
        if analyze_fn is None:
            self.analyze_fn = default_analyzer
        else:
            self.analyze_fn = analyze_fn
        self.documents = documents
        self.max_sentence_length = max_sentence_length

    def __iter__(self):
        for document in self.documents:
            words = self.analyze_fn(document)
            i = 0
            while i < len(words):
                yield words[i:(i + self.max_sentence_length)]
                i += self.max_sentence_length


class Word2VecRetrieval(RetrievalBase, RetriEvalMixin, CombinatorMixin):
    """ Kwargs are passed down to RetrievalBase's countvectorizer,
    whose analyzer is then used to decompose the documents into tokens
    model - the Word Embedding model to use
    name  - identifier for the retrieval model
    verbose - verbosity level
    oov    - token to use for out of vocabulary words
    vocab_analyzer - analyzer to use to prepare for vocabulary filtering
    try_lowercase - try to match with an uncased word if cased failed
    >>> docs = ["the quick",\
    "brown fox",\
    "jumps over",\
    "the lazy dog",\
    "This is a document about coookies and cream and fox and dog",\
    "The master thesis on the information retrieval task"]
    >>> sentences = StringSentence(docs)
    >>> from gensim.models import Word2Vec
    >>> model = Word2Vec(sentences, min_count=1)
    >>> word2vec = Word2VecRetrieval(model)
    >>> _ = word2vec.fit(docs)
    >>> values = word2vec.evaluate([(0,"fox"),\
    (1,"dog")], [[0,1,0,0,1,0],[0,0,0,1,1,0]])
    >>> values['mean_average_precision']
    1.0
    >>> values['mean_reciprocal_rank']
    1.0
    >>> values['ndcg@k']
    (1.0, 0.0)
    """
    def __init__(self,
                 model,
                 name=None,
                 wmd=1.0,
                 verbose=0,
                 vocab_analyzer=None,
                 oov=None,
                 try_lowercase=False,
                 **matching_params):
        self.model = model
        self.wmd = wmd
        self.verbose = verbose
        self.try_lowercase = try_lowercase
        # inits self._cv
        if name is None:
            if not self.wmd:
                name = "wcd"
            else:
                name = "wcd+wmd"
        self._init_params(name=name, **matching_params)
        # uses cv's analyzer which can be specified by kwargs
        if vocab_analyzer is None:
            # if none, infer from analyzer used in matching
            self.analyzer = self._cv.build_analyzer()
        else:
            self.analyzer = vocab_analyzer
        self.oov = oov

    def _filter_oov_token(self, words):
        return [word for word in words if word != self.oov]

    def _medoid_expansion(self, words, n_expansions=1):
        """
        >>> from gensim.models import Word2Vec
        >>> model = Word2Vec(["brown fox".split()],min_count=1)
        >>> rtrvl = Word2VecRetrieval(model)
        >>> rtrvl._medoid_expansion(["brown"], n_expansions=1)
        ['brown', 'fox']
        """
        if n_expansions < 1:
            return words
        exps = self.model.most_similar(positive=words)[:n_expansions]
        exps, _scores = zip(*exps)
        exps = list(exps)
        if self.verbose > 0:
            print("Expanded", words, "by:", exps)
        return words + exps

    def fit(self, docs, y=None):
        self._fit(docs, y)
        # self._X = np.apply_along_axis(lambda d: self.analyzer(str(d)), 0, X)
        # sentences = [self.analyzer(doc) for doc in docs]
        # self.bigrams = Phrases(sentences)
        # sentences = [self.bigrams[sentence] for sentence in sentences]
        analyzed_docs = (self.analyzer(doc) for doc in docs)
        X = [filter_vocab(self.model, d, oov=self.oov) for d in analyzed_docs]

        self._X = np.asarray(X)
        return self

    def partial_fit(self, docs, y=None):
        self._partial_fit(docs, y)

        analyzed_docs = (self.analyzer(doc) for doc in docs)
        Xprep = np.asarray(
            [filter_vocab(self.model, doc, oov=self.oov) for doc in
                analyzed_docs]
        )
        self._X = np.hstack([self._X, Xprep])

    def query(self, query, k=None):
        if k is None:
            k = len(self._X)

        model = self.model
        verbose = self.verbose
        indices = self._matching(query)
        wmd = self.wmd
        docs, labels = self._X[indices], self._y[indices]
        if verbose > 0:
            print(len(docs), "documents matched.")

        # if self.wmd:
        #     if self.wmd is True: wmd = k
        #     elif isinstance(self.wmd, int):
        #         wmd = k + self.wmd
        #     elif isinstance(self.wmd, float):
        #         wmd = int(k * self.wmd)
        #     else:
        #         raise ValueError("wmd= what?")
        # else:
        #     wmd = False

        q = self.analyzer(query)
        # q = self.bigrams[q]
        q = filter_vocab(self.model, q, oov=self.oov)

        # docs, labels set
        if verbose > 0:
            print("Preprocessed query:", q)
        if len(docs) == 0 or len(q) == 0:
            return []
        cosine_similarities = np.asarray(
            [model.n_similarity(q, doc) for doc in docs]
        )

        # nav
        topk = argtopk(cosine_similarities, k, sort=not wmd)  # sort when wcd
        # # # It is important to also clip the labels #
        docs, labels = docs[topk], labels[topk]
        # may be fewer than k

        # ind = np.argsort(cosine_similarities)[::-1]
        # if verbose > 0:
        #     print(cosine_similarities[topk])

        if not wmd:
            # no wmd, were done
            return labels
        else:  # wmd TODO prefetch and prune
            # scores = np.asarray([model.wmdistance(self._filter_oov_token(q),
            #                                       self._filter_oov_token(doc))
            scores = np.asarray([model.wmdistance(q, doc) for doc in docs])
            ind = np.argsort(scores)
            if verbose > 0:
                print(scores[ind])
            result = labels[ind]

        # if not wmd:  # if wmd is False
        #     result = labels[:k]
        # else:
        #     if verbose > 0:
        #         print("Computing wmdistance")
        #     scores = np.asarray([model.wmdistance(self._filter_oov_token(q),
        #                                           self._filter_oov_token(doc))
        #                          for doc in docs])
        #     ind = np.argsort(scores)  # ascending by distance
        #     if verbose > 0:
        #         print(scores[ind])
        #     ind = ind[:k]             # may be more than k
        #     result = labels[ind]

        return result


class WordCentroidRetrieval(BaseEstimator, RetriEvalMixin):
    """
    Retrieval Model based on Word Centroid Distance
    """
    def __init__(self,
                 embedding,
                 analyzer,
                 name="WCD",
                 n_jobs=1,
                 normalize=True,
                 verbose=0,
                 oov=None,
                 matching=True,
                 **kwargs):
        self.name = name
        self._embedding = embedding
        self._normalize = normalize
        self._oov = oov
        self.verbose = verbose
        self.n_jobs = n_jobs

        self._neighbors = NearestNeighbors(**kwargs)

        self._analyzer = analyzer

        if matching is True:
            self._matching = Matching()
        elif matching is False or matching is None:
            self._matching = None
        else:
            self._matching = Matching(**dict(matching))

    def _compute_centroid(self, words):
        if len(words) == 0:  # no words left at all? could also return zeros
            return self._embedding[self._oov]
        E = self._embedding
        embedded_words = np.vstack([E[word] for word in words])
        centroid = np.mean(embedded_words, axis=0).reshape(1, -1)
        return centroid

    def fit(self, docs, labels):
        E, analyze = self._embedding, self._analyzer

        analyzed_docs = (analyze(doc) for doc in docs)
        # out of vocabulary words do not have to contribute to the centroid

        filtered_docs = (filter_vocab(E, d, self._oov) for d in analyzed_docs)
        centroids = np.vstack([self._compute_centroid(doc) for doc in
                               filtered_docs])  # can we generate?
        if self.verbose > 0:
            print("Centroids shape:", centroids.shape)
        if self._normalize:
            normalize(centroids, norm='l2', copy=False)

        self._y = np.asarray(labels)

        if self._matching:
            self._matching.fit(docs)
            self._centroids = centroids
        else:
            # if we dont do matching, its enough to fit a nearest neighbors on
            # all centroids before query time
            self._neighbors.fit(centroids)
        return self

    def query(self, query, k=None, return_distance=False):
        if k is None:
            k = len(self._centroids)
        E, analyze, nn = self._embedding, self._analyzer, self._neighbors
        tokens = analyze(query)
        words = filter_vocab(E, tokens, self._oov)
        query_centroid = self._compute_centroid(words)
        if self._normalize:
            query_centroid = normalize(query_centroid, norm='l2', copy=False)
        if self.verbose > 0:
            print("Analyzed query", words)
            # print("Centered (normalized) query shape", query_centroid.shape)

        if self._matching:
            matched = self._matching.predict(query)
            centroids, labels = self._centroids[matched], self._y[matched]
            if len(centroids) == 0:
                return []  # nothing to fit here
            nn.fit(centroids)
            # k `leq` n_matched
            n_ret = min(k, len(matched))
        else:
            labels = self._y
            n_ret = k

        # either fit nn on the fly or precomputed in own fit method
        dist, ind = nn.kneighbors(query_centroid, n_neighbors=n_ret,
                                  return_distance=True)

        dist, ind = dist[0], ind[0]  # we only had one query in the first place

        if return_distance:
            return labels[ind], dist
        else:
            return labels[ind]


class FastWordCentroidRetrieval(BaseEstimator, RetriEvalMixin):

    """Docstring for FastWordCentrodRetrieval. """

    def __init__(self, embedding, analyzer='word', matching=None, name="FWCD",
                 n_jobs=1, use_idf=True):
        """TODO: to be defined1. """
        self.name = name
        self.matching = Matching(**dict(matching)) if matching else None
        self.vect = EmbeddedVectorizer(embedding, analyzer=analyzer, norm='l2',
                                       use_idf=use_idf)
        self.nn = NearestNeighbors(n_jobs=n_jobs, metric='cosine',
                                   algorithm='brute')

    def fit(self, X_raw, y):
        cents = self.vect.fit_transform(X_raw)
        self.centroids = cents
        print(' FIT centroids shape', self.centroids.shape)

        self._y = y
        if self.matching:
            self.matching.fit(X_raw)
        else:
            self.nn.fit(cents)

    def query(self, query, k=None, matched_indices=None):
        centroids = self.centroids
        if k is None:
            k = centroids.shape[0]

        q_centroid = self.vect.transform([query])

        if self.matching:
            ind = self.matching.predict(query)
            centroids, labels = centroids[ind], self._y[ind]
            n_ret = min(k, centroids.shape[0])
            if n_ret == 0:
                return []
            self.nn.fit(centroids)
        elif matched_indices:
            centroids, labels = centroids[ind], self._y[ind]
            n_ret = min(k, centroids.shape[0])
            if n_ret == 0:
                return []
            self.nn.fit(centroids)
        else:
            labels = self._y
            n_ret = k

        ind = self.nn.kneighbors(q_centroid, n_neighbors=n_ret,
                                 return_distance=False)[0]

        return labels[ind]


class WordMoversRetrieval(BaseEstimator, RetriEvalMixin):
    """Retrieval based on the Word Mover's Distance"""
    def __init__(self, embedding, analyzer=None, oov=None,
                 matching_params=None,
                 name="ppwmd", verbose=0, n_jobs=1):
        """initalize parameters"""
        self.embedding = embedding
        self.analyzer = analyzer
        self.oov = oov
        self.matching = (Matching(**dict(matching_params)) if matching_params
                         else None)
        self.verbose = verbose
        self.name = name

    def fit(self, raw_docs, y=None):
        if self.matching:
            self.matching.fit(raw_docs, y)
        analyzed_docs = (self.analyzer(doc) for doc in raw_docs)
        X_ = [filter_vocab(self.embedding, d, oov=self.oov) for d in
              analyzed_docs]
        self._X = np.asarray(X_)
        self._y = np.asarray(y)
        return self

    def query(self, query, k=None, matched_indices=None):
        k = k if k is not None else len(self._X)
        E, analyzed = self.embedding, self.analyzer
        if self.matching:
            ind = self.matching.predict(query)
            docs, labels = self._X[ind], self._y[ind]
        else:
            docs, labels = self._X, self._y

        q = filter_vocab(E, analyzed(query), oov=self.oov)

        if self.verbose:
            print('Analyed query: %s' % q)
            print('Computing wm distance for %d documents' % len(docs))

        wm_dists = [E.wmdistance(q, doc) for doc in docs]

        topk = np.argsort(wm_dists)[:k]

        return labels[topk]


class WmdSimilarityRetrieval(BaseEstimator, RetriEvalMixin):
    def __init__(self, embedding, analyzer, k, name='gwmd', verbose=0):
        self.embedding = embedding
        self.analyzer = analyzer
        self.name = name
        self.verbose = verbose
        self.instance = None

    def fit(self, X, y=None):
        E, analyze = self.embedding, self.analyzer
        corpus = [analyze(doc) for doc in X]
        self.instance = WmdSimilarity(corpus, E, num_best=self.k)

        self._labels = y

    def query(self, sent, k=None, matching_indices=None):
        query = self.analyzer(sent)
        sims = self.instance[query]
        indices, scores = zip(*sims)
        indices = list(indices)
        return self._labels[indices]


if __name__ == '__main__':
    import doctest
    doctest.testmod()
