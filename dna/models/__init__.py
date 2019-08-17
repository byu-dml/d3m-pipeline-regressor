import typing

from .baselines import (
    AutoSklearnMetalearner, LinearRegressionBaseline, MeanBaseline, MedianBaseline, MetaAutoSklearn,
    PerPrimitiveBaseline, RandomBaseline, RandomForestBaseline
)
from .dna_regression_model import DNARegressionModel
from .lstm_model import LSTMModel
from .dag_lstm_regression_model import DAGLSTMRegressionModel
from .hidden_dag_lstm_regression_model import HiddenDAGLSTMRegressionModel
from .attention_regression_model import AttentionRegressionModel
from .dag_attention_regression_model import DAGAttentionRegressionModel
from .probabilistic_matrix_factorization import ProbabilisticMatrixFactorization


class ModelNotFitError(Exception):
    pass


def get_model(model_name: str, model_config: typing.Dict, seed: int):
    model_class = {
        'dna_regression': DNARegressionModel,
        'mean_regression': MeanBaseline,
        'median_regression': MedianBaseline,
        'per_primitive_regression': PerPrimitiveBaseline,
        'autosklearn': AutoSklearnMetalearner,
        'lstm': LSTMModel,
        'daglstm_regression': DAGLSTMRegressionModel,
        'hidden_daglstm_regression': HiddenDAGLSTMRegressionModel,
        'attention_regression': AttentionRegressionModel,
        'dag_attention_regression': DAGAttentionRegressionModel,
        'linear_regression': LinearRegressionBaseline,
        'random_forest': RandomForestBaseline,
        'random': RandomBaseline,
        'meta_autosklearn': MetaAutoSklearn,
        'probabilistic_matrix_factorization': ProbabilisticMatrixFactorization,
    }[model_name.lower()]
    init_model_config = model_config.get('__init__', {})
    return model_class(**init_model_config, seed=seed)
