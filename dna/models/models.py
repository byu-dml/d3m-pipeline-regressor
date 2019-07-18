import json
import os
import typing

import autosklearn.regression as autosklearn
import numpy as np
import pandas as pd
from sklearn import linear_model
import torch
import torch.nn as nn
from tqdm import tqdm

from dna import utils
from dna.data import Dataset, GroupDataLoader, PMFDataset, RNNDataLoader, group_json_objects
from dna.kND import KNearestDatasets
from .torch_modules.dna_module import DNAModule
from .torch_modules.dag_lstm_mlp import DAGLSTMMLP
from .torch_modules.hidden_mlp_dag_lstm_mlp import HiddenMLPDAGLSTMMLP
from .torch_modules.pmf import PMF


class ModelNotFitError(Exception):
    pass


class ModelBase:

    def __init__(self, *, seed):
        self.seed = seed
        self.fitted = False

    def fit(self, data, *, verbose=False):
        raise NotImplementedError()


class RegressionModelBase(ModelBase):

    def predict_regression(self, data, *, verbose=False):
        raise NotImplementedError()


class RankModelBase(ModelBase):

    def predict_rank(self, data, *, verbose=False):
        raise NotImplementedError()


class SubsetModelBase(ModelBase):

    def predict_subset(self, data, k, **kwargs):
        raise NotImplementedError()


class PyTorchModelBase:

    def __init__(self, *, y_dtype, device, seed):
        """
        Parameters
        ----------
        y_dtype:
            one of: torch.int64, torch.float32
        """
        self.y_dtype = y_dtype
        self.device = device
        self.seed = seed

        self._model = None

    def fit(
        self, train_data, n_epochs, learning_rate, batch_size, drop_last, *, validation_data=None, output_dir=None,
        verbose=False
    ):
        self._model = self._get_model(train_data)
        self._loss_function = self._get_loss_function()
        self._optimizer = self._get_optimizer(learning_rate)

        model_save_path = None
        if output_dir is not None:
            model_save_path = os.path.join(output_dir, 'model.pt')

        train_data_loader = self._get_data_loader(train_data, batch_size, drop_last, shuffle=True)
        validation_data_loader = None
        min_loss_score = np.inf
        if validation_data is not None:
            validation_data_loader = self._get_data_loader(validation_data, batch_size, drop_last=False, shuffle=False)

        for e in range(n_epochs):
            save_model = False
            if verbose:
                print('epoch {}'.format(e))

            self._train_epoch(
                train_data_loader, self._model, self._loss_function, self._optimizer, verbose=verbose
            )

            train_predictions, train_targets = self._predict_epoch(train_data_loader, self._model, verbose=verbose)
            train_loss_score = self._loss_function(train_predictions, train_targets)
            if output_dir is not None:
                self._save_outputs(output_dir, 'train', e, train_predictions, train_targets, train_loss_score)
            if verbose:
                print('train loss: {}'.format(train_loss_score))

            if validation_data_loader is not None:
                validation_predictions, validation_targets = self._predict_epoch(validation_data_loader, self._model, verbose=verbose)
                validation_loss_score = self._loss_function(validation_predictions, validation_targets)
                if output_dir is not None:
                    self._save_outputs(output_dir, 'validation', e, validation_predictions, validation_targets, validation_loss_score)
                if verbose:
                    print('validation loss: {}'.format(validation_loss_score))
                if validation_loss_score < min_loss_score:
                    min_loss_score = validation_loss_score
                    save_model = True
            else:
                if train_loss_score < min_loss_score:
                    min_loss_score = train_loss_score
                    save_model = True

            if save_model and model_save_path is not None:
                torch.save(self._model.state_dict(), model_save_path)

        if not save_model and model_save_path is not None:  # model not saved during final epoch
            self._model.load_state_dict(torch.load(model_save_path))

        self.fitted = True

    def _get_model(self, train_data):
        raise NotImplementedError()

    def _get_loss_function(self):
        raise NotImplementedError()

    def _get_optimizer(self, learning_rate):
        raise NotImplementedError()

    def _get_data_loader(self, data, batch_size, drop_last, shuffle):
        raise NotImplementedError()

    def _train_epoch(
        self, data_loader, model: nn.Module, loss_function, optimizer, *, verbose=True
    ):
        model.train()

        if verbose:
            progress = tqdm(total=len(data_loader), position=0)

        for x_batch, y_batch in data_loader:
            y_hat_batch = model(x_batch)
            loss = loss_function(y_hat_batch, y_batch)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

            if verbose:
                progress.update(1)

        if verbose:
            progress.close()

    def _predict_epoch(
        self, data_loader, model: nn.Module, *, verbose=True
    ):
        model.eval()
        predictions = []
        targets = []
        if verbose:
            progress = tqdm(total=len(data_loader), position=0)

        with torch.no_grad():
            for x_batch, y_batch in data_loader:
                y_hat_batch = model(x_batch)

                if y_batch.shape[0] == 1:
                    predictions.append(y_hat_batch.item())
                    targets.append(y_batch.item())
                else:
                    predictions.extend(y_hat_batch.tolist())
                    targets.extend(y_batch.tolist())

                if verbose:
                    progress.update(1)

        if verbose:
            progress.close()

        return torch.tensor(predictions, dtype=self.y_dtype), torch.tensor(targets, dtype=self.y_dtype)

    @staticmethod
    def _save_outputs(output_dir, phase, epoch, predictions, targets, loss_score):
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        save_filename = phase + '_scores.csv'
        save_path = os.path.join(output_dir, save_filename)
        with open(save_path, 'a') as f:
            f.write(str(float(loss_score)) + '\n')

        output_dir = os.path.join(output_dir, 'outputs')
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        save_filename = str(epoch) + '_' + phase + '.json'
        save_path = os.path.join(output_dir, save_filename)
        outputs = {
            'predictions': predictions.tolist(),
            'targets': targets.tolist(),
        }
        with open(save_path, 'w') as f:
            json.dump(outputs, f, separators=(',',':'))


class PyTorchRegressionRankSubsetModelBase(PyTorchModelBase, RegressionModelBase, RankModelBase, SubsetModelBase):

    def __init__(self, y_dtype, device, seed):
        # different arguments means different function calls
        PyTorchModelBase.__init__(self, y_dtype=torch.float32, device=device, seed=seed)
        RegressionModelBase.__init__(self, seed=seed)

    def predict_regression(self, data, *, batch_size, verbose):
        if self._model is None:
            raise Exception('model not fit')

        data_loader = self._get_data_loader(data, batch_size, drop_last=False, shuffle=False)
        predictions, targets = self._predict_epoch(data_loader, self._model, verbose=verbose)
        reordered_predictions = predictions.numpy()[data_loader.get_group_ordering()]
        return reordered_predictions.tolist()

    def predict_rank(self, data, *, batch_size, verbose):
        if self._model is None:
            raise Exception('model not fit')

        predictions = self.predict_regression(data, batch_size=batch_size, verbose=verbose)
        ranks = utils.rank(predictions)
        return {
            'pipeline_id': [instance['pipeline_id'] for instance in data],
            'rank': ranks,
        }

    def predict_subset(self, data, k, *, batch_size, verbose=False):
        if self._model is None:
            raise Exception('model not fit')

        ranked_data = self.predict_rank(data, batch_size=batch_size, verbose=verbose)
        top_k = pd.DataFrame(ranked_data).nsmallest(k, columns='rank')['pipeline_id']
        return top_k.tolist()


class DNARegressionModel(PyTorchRegressionRankSubsetModelBase):

    def __init__(
        self, n_hidden_layers: int, hidden_layer_size: int, activation_name: str, use_batch_norm: bool,
        use_skip: bool = False, dropout = 0.0, *, device: str = 'cuda:0', seed: int = 0
    ):
        super().__init__(y_dtype=torch.float32, device=device, seed=seed)

        self.n_hidden_layers = n_hidden_layers
        self.hidden_layer_size = hidden_layer_size
        self.activation_name = activation_name
        self.use_batch_norm = use_batch_norm
        self.use_skip = use_skip
        self.dropout = dropout
        self.output_layer_size = 1
        self._model_seed = self.seed + 1

    def _get_model(self, train_data):
        submodule_input_sizes = {}
        for instance in train_data:
            for step in instance['pipeline']['steps']:
                submodule_input_sizes[step['name']] = len(step['inputs'])
        self.input_layer_size = len(train_data[0]['metafeatures'])

        return DNAModule(
            submodule_input_sizes, self.n_hidden_layers + 1, self.input_layer_size, self.hidden_layer_size,
            self.output_layer_size, self.activation_name, self.use_batch_norm, self.use_skip, self.dropout,
            device=self.device, seed=self._model_seed
        )

    def _get_loss_function(self):
        objective = torch.nn.MSELoss(reduction="mean")
        return lambda y_hat, y: torch.sqrt(objective(y_hat, y))

    def _get_optimizer(self, learning_rate):
        return torch.optim.Adam(self._model.parameters(), lr=learning_rate)

    def _get_data_loader(self, data, batch_size, drop_last, shuffle=True):
        return GroupDataLoader(
            data = data,
            group_key = 'pipeline.id',
            dataset_class = Dataset,
            dataset_params = {
                'features_key': 'metafeatures',
                'target_key': 'test_f1_macro',
                'y_dtype': self.y_dtype,
                'device': self.device
            },
            batch_size = batch_size,
            drop_last = drop_last,
            shuffle = shuffle,
            seed = self.seed + 2
        )


class DAGLSTMRegressionModel(PyTorchRegressionRankSubsetModelBase):

    def __init__(
        self, activation_name: str, hidden_state_size: int, lstm_n_layers: int, dropout: float,
        output_n_hidden_layers: int, output_hidden_layer_size: int, use_batch_norm: bool, use_skip: bool = False,
        reduction: str = 'mean', *, device: str = 'cuda:0', seed: int = 0
    ):
        super().__init__(y_dtype=torch.float32, seed=seed, device=device)

        self.activation_name = activation_name
        self.hidden_state_size = hidden_state_size
        self.lstm_n_layers = lstm_n_layers
        self.dropout = dropout
        self.output_n_hidden_layers = output_n_hidden_layers
        self.output_hidden_layer_size = output_hidden_layer_size
        self.use_batch_norm = use_batch_norm
        self.use_skip = use_skip
        self.reduction = reduction
        self.device = device
        self.seed = seed
        self._data_loader_seed = seed + 1
        self._model_seed = seed + 2

        self.pipeline_structures = None
        self.num_primitives = None
        self.primitive_name_to_enc = None
        self.target_key = 'test_f1_macro'
        self.batch_group_key = 'pipeline_structure'
        self.pipeline_key = 'pipeline'
        self.steps_key = 'steps'
        self.prim_name_key = 'name'
        self.prim_inputs_key = 'inputs'
        self.features_key = 'metafeatures'

    def fit(self, train_data, n_epochs, learning_rate, batch_size, drop_last, *, validation_data=None, output_dir=None,
        verbose=False):
        # Get all the pipeline structure for each pipeline structure group before encoding the pipelines
        self.pipeline_structures = {}
        grouped_by_structure = group_json_objects(train_data, self.batch_group_key)
        for (group, group_indices) in grouped_by_structure.items():
            index = group_indices[0]
            item = train_data[index]
            pipeline = item[self.pipeline_key][self.steps_key]
            group_structure = [primitive[self.prim_inputs_key] for primitive in pipeline]
            self.pipeline_structures[group] = group_structure

        # Get the mapping of primitives to their one hot encoding
        self.primitive_name_to_enc = self._get_primitive_name_to_enc(train_data=train_data)

        PyTorchModelBase.fit(
            self, train_data, n_epochs, learning_rate, batch_size, drop_last, validation_data=validation_data,
            output_dir=output_dir, verbose=verbose
        )


    def _get_primitive_name_to_enc(self, train_data):
        primitive_names = set()

        # Get a set of all the primitives in the train set
        for instance in train_data:
            primitives = instance[self.pipeline_key][self.steps_key]
            for primitive in primitives:
                primitive_name = primitive[self.prim_name_key]
                primitive_names.add(primitive_name)

        # Get one hot encodings of all the primitives
        self.num_primitives = len(primitive_names)
        encoding = np.identity(n=self.num_primitives)

        # Create a mapping of primitive names to one hot encodings
        primitive_name_to_enc = {}
        primitive_names = sorted(primitive_names)
        for (primitive_name, primitive_encoding) in zip(primitive_names, encoding):
            primitive_name_to_enc[primitive_name] = primitive_encoding

        return primitive_name_to_enc

    def _get_model(self, train_data):
        return DAGLSTMMLP(
            lstm_input_size=self.num_primitives,
            lstm_hidden_state_size=self.hidden_state_size,
            lstm_n_layers=self.lstm_n_layers,
            dropout=self.dropout,
            mlp_extra_input_size=len(train_data[0][self.features_key]),
            mlp_hidden_layer_size=self.output_hidden_layer_size,
            mlp_n_hidden_layers=self.output_n_hidden_layers,
            output_size=1,
            mlp_activation_name=self.activation_name,
            mlp_use_batch_norm=self.use_batch_norm,
            mlp_use_skip=self.use_skip,
            reduction=self.reduction,
            device=self.device,
            seed=self._model_seed,
        )

    def _get_optimizer(self, learning_rate):
        return torch.optim.Adam(self._model.parameters(), lr=learning_rate)

    def _get_data_loader(self, data, batch_size, drop_last, shuffle=True):
        return RNNDataLoader(
            data=data,
            group_key=self.batch_group_key,
            dataset_params={
                'features_key': self.features_key,
                'target_key': self.target_key,
                'y_dtype': self.y_dtype,
                'device': self.device
            },
            batch_size=batch_size,
            drop_last=drop_last,
            shuffle=shuffle,
            seed=self._data_loader_seed,
            pipeline_structures=self.pipeline_structures,
            primitive_to_enc=self.primitive_name_to_enc,
            pipeline_key=self.pipeline_key,
            steps_key=self.steps_key,
            prim_name_key=self.prim_name_key
        )

    def _get_loss_function(self):
        objective = torch.nn.MSELoss(reduction="mean")
        return lambda y_hat, y: torch.sqrt(objective(y_hat, y))


class HiddenDAGLSTMRegressionModel(DAGLSTMRegressionModel):

    def __init__(
        self, activation_name: str, input_n_hidden_layers: int, input_hidden_layer_size: int, hidden_state_size: int,
        lstm_n_layers: int, dropout: float, output_n_hidden_layers: int, output_hidden_layer_size: int,
        use_batch_norm: bool, use_skip: bool = False, reduction: str = 'mean', *, device: str = 'cuda:0', seed: int = 0
    ):
        super().__init__(
            activation_name, hidden_state_size, lstm_n_layers, dropout, output_n_hidden_layers,
            output_hidden_layer_size, use_batch_norm, use_skip, reduction, device=device, seed=seed
        )

        self.input_n_hidden_layers = input_n_hidden_layers
        self.input_hidden_layer_size = input_hidden_layer_size

    def _get_model(self, train_data):
        n_features = len(train_data[0][self.features_key])
        return HiddenMLPDAGLSTMMLP(
            lstm_input_size=self.num_primitives,
            lstm_hidden_state_size=self.hidden_state_size,
            lstm_n_layers=self.lstm_n_layers,
            dropout=self.dropout,
            input_mlp_input_size=n_features,
            mlp_hidden_layer_size=self.output_hidden_layer_size,
            mlp_n_hidden_layers=self.output_n_hidden_layers,
            mlp_activation_name=self.activation_name,
            output_size=1,
            mlp_use_batch_norm=self.use_batch_norm,
            mlp_use_skip=self.use_skip,
            reduction=self.reduction,
            device=self.device,
            seed=self._model_seed,
        )


class DNASiameseModule(nn.Module):

    def __init__(self, input_model, submodules, output_model):
        super().__init__()
        self.input_model = input_model
        self.submodules = submodules
        self.output_model = output_model
        self.h1 = None
        self.f_activation = F_ACTIVATIONS[ACTIVATION]

    def forward(self, args):
        pipeline_ids, (left_pipeline, right_pipeline), x = args
        self.h1 = self.input_model(x)
        left_h2 = self.recursive_get_output(left_pipeline, len(left_pipeline) - 1)
        right_h2 = self.recursive_get_output(right_pipeline, len(right_pipeline) - 1)
        h2 = torch.cat((left_h2, right_h2), 1)
        return self.output_model(h2)

    def recursive_get_output(self, pipeline, current_index):
        """
        The recursive call to find the input
        :param pipeline: the pipeline list containing the submodules
        :param current_index: the index of the current submodule
        :return:
        """
        try:
            current_submodule = self.submodules[pipeline[current_index]['name']]
            if "inputs.0" in pipeline[current_index]['inputs']:
                return self.f_activation(current_submodule(self.h1))

            outputs = []
            for input in pipeline[current_index]["inputs"]:
                curr_output = self.recursive_get_output(pipeline, input)
                outputs.append(curr_output)

            if len(outputs) > 1:
                new_output = self.f_activation(current_submodule(torch.cat(tuple(outputs), dim=1)))
            else:
                new_output = self.f_activation(current_submodule(curr_output))

            return new_output
        except Exception as e:
            print("There was an error in the foward pass.  It was ", e)
            print(pipeline[current_index])
            quit(1)


class MeanBaseline(RegressionModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self.mean = None

    def fit(self, data, *, validation_data=None, output_dir=None, verbose=False):
        total = 0
        for instance in data:
            total += instance['test_f1_macro']
        self.mean = total / len(data)
        self.fitted = True

    def predict_regression(self, data, *, verbose=False):
        if self.mean is None:
            raise ModelNotFitError('MeanBaseline not fit')
        return [self.mean] * len(data)


class MedianBaseline(RegressionModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self.median = None

    def fit(self, data, *, validation_data=None, output_dir=None, verbose=False):
        self.median = np.median([instance['test_f1_macro'] for instance in data])
        self.fitted = True

    def predict_regression(self, data, *, verbose=False):
        if self.median is None:
            raise ModelNotFitError('MeanBaseline not fit')
        return [self.median] * len(data)


class PerPrimitiveBaseline(RegressionModelBase, RankModelBase, SubsetModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self.primitive_scores = None

    def fit(self, data, *, validation_data=None, output_dir=None, verbose=False):
        # for each primitive, get the scores of all the pipelines that use the primitive
        primitive_score_totals = {}
        for instance in data:
            for primitive in instance['pipeline']['steps']:
                if primitive['name'] not in primitive_score_totals:
                    primitive_score_totals[primitive['name']] = {
                        'total': 0,
                        'count': 0,
                    }
                primitive_score_totals[primitive['name']]['total'] += instance['test_f1_macro']
                primitive_score_totals[primitive['name']]['count'] += 1

        # compute the average pipeline score per primitive
        self.primitive_scores = {}
        for primitive_name in primitive_score_totals:
            total = primitive_score_totals[primitive_name]['total']
            count = primitive_score_totals[primitive_name]['count']
            self.primitive_scores[primitive_name] = total / count

        self.fitted = True

    def predict_regression(self, data, **kwargs):
        if self.primitive_scores is None:
            raise ModelNotFitError('PerPrimitiveBaseline not fit')

        predictions = []
        for instance in data:
            prediction = 0
            for primitive in instance['pipeline']['steps']:
                prediction += self.primitive_scores[primitive['name']]
            prediction /= len(instance['pipeline']['steps'])
            predictions.append(prediction)

        return predictions

    def predict_rank(self, data, **kwargs):
        predictions = self.predict_regression(data, **kwargs)
        ranks = list(utils.rank(predictions))
        return {
            'pipeline_id': [instance['pipeline_id'] for instance in data],
            'rank': ranks,
        }

    def predict_subset(self, data, k, **kwargs):
        if not self.fitted:
            raise Exception('model not fit')

        ranked_data = self.predict_rank(data, **kwargs)
        top_k = pd.DataFrame(ranked_data).nsmallest(k, columns='rank')['pipeline_id']
        return top_k.tolist()


class RandomBaseline(RankModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self._random_state = np.random.RandomState(seed)
        self.fitted = True

    def fit(self, *args, **kwargs):
        pass

    def predict_rank(self, data, *, verbose=False):
        predictions = list(range(len(data)))
        self._random_state.shuffle(predictions)
        return {
            'pipeline_id': [instance['pipeline_id'] for instance in data],
            'rank': predictions,
        }

    def predict_subset(self, data, k, **kwargs):
        predictions = self._random_state.choice(data, k)
        return [instance['pipeline_id'] for instance in predictions]


class SklearnBase(RegressionModelBase, RankModelBase, SubsetModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self.pipeline_key = 'pipeline'
        self.steps_key = 'steps'
        self.prim_name_key = 'name'

    def fit(self, data, *, validation_data=None, output_dir=None, verbose=False):
        self.one_hot_primitives_map = self._one_hot_encode_mapping(data)
        data = pd.DataFrame(data)
        y = data['test_f1_macro']
        X_data = self.prepare_data(data)
        self.regressor.fit(X_data, y)
        self.fitted = True

    def predict_regression(self, data, *, verbose=False):
        if not self.fitted:
            raise ModelNotFitError('{} not fit'.format(type(self).__name__))

        data = pd.DataFrame(data)
        X_data = self.prepare_data(data)
        return self.regressor.predict(X_data).tolist()

    def predict_rank(self, data, *, verbose=False):
        if not self.fitted:
            raise ModelNotFitError('{} not fit'.format(type(self).__name__))

        predictions = self.predict_regression(data)
        ranks = utils.rank(predictions)
        return {
            'pipeline_id': [instance['pipeline_id'] for instance in data],
            'rank': ranks,
        }

    def predict_subset(self, data, k, **kwargs):
        if not self.fitted:
            raise Exception('model not fit')

        ranked_data = self.predict_rank(data, **kwargs)
        top_k = pd.DataFrame(ranked_data).nsmallest(k, columns='rank')['pipeline_id']
        return top_k.tolist()

    def prepare_data(self, data):
        # expand the column of lists of metafeatures into a full dataframe
        metafeature_df = pd.DataFrame(data.metafeatures.values.tolist()).reset_index(drop=True)
        assert np.isnan(metafeature_df.values).sum() == 0, 'metafeatures should not contain nans'
        assert np.isinf(metafeature_df.values).sum() == 0, 'metafeatures should not contain infs'

        encoded_pipelines = self.one_hot_encode_pipelines(data)
        assert np.isnan(encoded_pipelines.values).sum() == 0, 'pipeline encodings should not contain nans'
        assert np.isinf(encoded_pipelines.values).sum() == 0, 'pipeline encodings should not contain infs'

        # concatenate the parts together and validate
        assert metafeature_df.shape[0] == encoded_pipelines.shape[0], 'number of metafeature instances does not match number of pipeline instances'
        X_data = pd.concat([encoded_pipelines, metafeature_df], axis=1, ignore_index=True)
        assert X_data.shape[1] == (encoded_pipelines.shape[1] + metafeature_df.shape[1]), 'dataframe was combined incorrectly'
        return X_data

    def _one_hot_encode_mapping(self, data):
        primitive_names = set()

        # Get a set of all the primitives in the train set
        for instance in data:
            primitives = instance[self.pipeline_key][self.steps_key]
            for primitive in primitives:
                primitive_name = primitive[self.prim_name_key]
                primitive_names.add(primitive_name)

        primitive_names = sorted(primitive_names)

        # Get one hot encodings of all the primitives
        self.n_primitives = len(primitive_names)
        encoding = np.identity(n=self.n_primitives)

        # Create a mapping of primitive names to one hot encodings
        primitive_name_to_enc = {}
        for (primitive_name, primitive_encoding) in zip(primitive_names, encoding):
            primitive_name_to_enc[primitive_name] = primitive_encoding

        return primitive_name_to_enc

    def one_hot_encode_pipelines(self, data):
        return pd.DataFrame([self.encode_pipeline(pipeline) for pipeline in data[self.pipeline_key]])

    def encode_pipeline(self, pipeline):
        """
        Encodes a pipeline by OR-ing the one-hot encoding of the primitives.
        """
        encoding = np.zeros(self.n_primitives)
        for primitive in pipeline[self.steps_key]:
            primitive_name = primitive[self.prim_name_key]
            # get the position of the one hot encoding
            primitive_index = np.argmax(self.one_hot_primitives_map[primitive_name])
            encoding[primitive_index] = 1
        return encoding


class LinearRegressionBaseline(SklearnBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self.regressor = linear_model.LinearRegression()
        self.fitted = False


class MetaAutoSklearn(SklearnBase):

    def __init__(self, time_left_for_this_task=60, per_run_time_limit=10, seed=0):
        super().__init__(seed=seed)
        self.regressor = autosklearn.AutoSklearnRegressor(
            time_left_for_this_task=time_left_for_this_task, per_run_time_limit=per_run_time_limit, seed=seed
        )
        self.fitted = False


class AutoSklearnMetalearner(RegressionModelBase, RankModelBase, SubsetModelBase):

    def __init__(self, seed=0):
        super().__init__(seed=seed)
        self._knd = KNearestDatasets(metric='l1')

    def _predict(self, data, method, k=None):
        data = pd.DataFrame(data)
        # they all should have the same dataset and metafeatures so take it from the first row
        dataset_metafeatures = pd.Series(data['metafeatures'].iloc[0])
        queried_pipelines = data['pipeline_id']

        if method == 'all':
            predicted_pipelines = self._knd.knn_regression(dataset_metafeatures)
            predicted_pipelines = predicted_pipelines.sort_values(ascending=False).index.tolist()
        elif method == 'k':
            predicted_pipelines = self._knd.kBestSuggestions(dataset_metafeatures, k=k)
        else:
            raise ValueError('Unknown method: {}'.format(method))

        for pipeline_id in set(predicted_pipelines).difference(set(queried_pipelines)):
            predicted_pipelines.remove(pipeline_id)

        return predicted_pipelines

    def predict_regression(self, data, **kwargs):
        predictions = []
        cached_predictions = {}
        for instance in data:
            dataset_id = instance['dataset_id']
            if dataset_id not in cached_predictions:
                metafeatures = pd.Series(instance['metafeatures'])
                cached_predictions[dataset_id] = self._knd.knn_regression(metafeatures)
            pipeline_id = instance['pipeline_id']
            predictions.append(cached_predictions[dataset_id].get(pipeline_id, None))
        return predictions

    def predict_subset(self, data, k, **kwargs):
        """
        Recommends at most k pipelines from data expected to perform well for the provided dataset.
        """
        return self._predict(data, method='k', k=k)

    def predict_rank(self, data, **kwargs):
        """
        Ranks all pipelines in data.
        """
        ranked_pipelines = self._predict(data, method='all')

        assert len(ranked_pipelines) == len(data), '{} {}'.format(len(ranked_pipelines), len(data))

        return {
            'pipeline_id': ranked_pipelines,
            'rank': list(range(len(ranked_pipelines))),
        }

    def fit(self, train_data, *args, **kwargs):
        self._runs = self._process_runs(train_data)
        self._metafeatures = self._process_metafeatures(train_data)
        self._knd.fit(self._metafeatures, self._runs)
        self.fitted = True

    @staticmethod
    def _process_runs(data):
        """
        This function is used to transform the dataframe into a workable object fot the KNN, with rows of pipeline_ids
        and columns of datasets, with the inside being filled with the scores
        :return:
        """
        new_runs = {}
        for index, row in enumerate(data):
            dataset_name = row['dataset_id']
            if dataset_name not in new_runs:
                new_runs[dataset_name] = {}
            new_runs[dataset_name][row['pipeline_id']] = row['test_f1_macro']
        final_new = pd.DataFrame(new_runs)
        return final_new

    @staticmethod
    def _process_metafeatures(data):
        metafeatures = pd.DataFrame(data)[['dataset_id', 'metafeatures']].set_index('dataset_id')
        metafeatures = pd.DataFrame(metafeatures['metafeatures'].tolist(), metafeatures.index)
        metafeatures.drop_duplicates(inplace=True)
        return metafeatures


class ProbabilisticMatrixFactorization(PyTorchRegressionRankSubsetModelBase):
    """
    Probabilitistic Matrix Factorization (see https://arxiv.org/abs/1705.05355 for the paper)
    Adapted from traditional Probabilitistic Matrix Factorization but instead of `Users` and `Items`, we have `Pipelines` and `Datasets`
    """
    def __init__(self, latent_features, lam_u, lam_v, probabilitistic, *, device: str = 'cuda:0', seed=0):
        super().__init__(y_dtype=torch.float32, device=device, seed=seed)
        self.latent_features = latent_features
        self.device = device
        self.fitted = False

        # regularization terms to make it Probabilitistic
        self.lam_u = lam_u
        self.lam_v = lam_v
        self.mse_loss = torch.nn.MSELoss(reduction="mean")
        self.probabilitistic = probabilitistic

        self.target_key = 'test_f1_macro'
        self.batch_group_key = 'pipeline_structure'
        self.pipeline_key = 'pipeline'
        self.steps_key = 'steps'
        self.prim_name_key = 'name'
        self.prim_inputs_key = 'inputs'
        self.features_key = 'metafeatures'

    def PMFLoss(self, y_hat, y):
        rmse_loss = torch.sqrt(self.mse_loss(y_hat, y))
        # PMF loss includes two extra regularlization
        # NOTE: using probabilitistic loss will make the loss look worse, even though it performs well on RMSE (because of the inflated)
        if self.probabilitistic:
            u_regularization = self.lam_u * torch.sum(self.model.dataset_factors.weight.norm(dim=1))
            v_regularization = self.lam_v * torch.sum(self.model.pipeline_factors.weight.norm(dim=1))
            return rmse_loss + u_regularization + v_regularization

        return rmse_loss

    def _get_loss_function(self):
        return lambda y_hat, y: self.PMFLoss(y_hat, y)

    def _get_optimizer(self, learning_rate):
        return torch.optim.Adam(self._model.parameters(), lr=learning_rate)

    def _get_data_loader(self, data, batch_size, drop_last, shuffle=True):
        return GroupDataLoader(
            data = data,
            group_key = 'pipeline.id',
            dataset_class = PMFDataset,
            dataset_params = {
                'features_key': 'dataset_id',
                'target_key': self.target_key,
                'y_dtype': self.y_dtype,
                'device': self.device,
                "encoding_function": self.encode_dataset,
            },
            batch_size = batch_size,
            drop_last = drop_last,
            shuffle = shuffle,
            seed = self.seed + 2,
        )

    def _get_model(self, train_data):
        self.model = PMF(self.n_pipelines, self.n_datasets, self.latent_features, device=self.device, seed=self.seed)
        return self.model

    def fit(self, train_data, n_epochs, learning_rate, batch_size, drop_last, *, validation_data=None, output_dir=None,
        verbose=False):
        self.fitted = True

        # get mappings for matrix -> using both datasets to prepare mapping, otherwise we're unprepared for new datasets
        self.pipeline_id_mapper = self.map_pipeline_ids(train_data + validation_data)
        self.dataset_id_mapper = self.map_dataset_ids(train_data + validation_data)

        # encode the pipeline dataset mapping
        train_data = self.encode_pipeline_dataset(train_data)
        if validation_data is not None:
            validation_data = self.encode_pipeline_dataset(validation_data)

        # do the rest of the fitting
        PyTorchModelBase.fit(
            self, train_data, n_epochs, learning_rate, batch_size, drop_last, validation_data=validation_data,
            output_dir=output_dir, verbose=verbose
        )

    def encode_pipeline_dataset(self, data):
        for instance in data:
            instance["pipeline"]["pipeline_embedding"] = self.encode_pipeline(instance["pipeline"]["id"])
            instance["dataset_id_embedding"] = self.dataset_id_mapper[instance["dataset_id"]]
        return data

    def map_pipeline_ids(self, data):
        unique_pipelines = list(set([instance["pipeline"]["id"] for instance in data]))
        # for reproduciblity
        unique_pipelines.sort()
        self.n_pipelines = len(unique_pipelines)
        return {unique_pipelines[index]:index for index in range(self.n_pipelines)}

    def map_dataset_ids(self, data):
        unique_datasets = list(set([instance["dataset_id"] for instance in data]))
        unique_datasets.sort()
        self.n_datasets = len(unique_datasets)
        return {unique_datasets[index]:index for index in range(self.n_datasets)}

    def encode_dataset(self, dataset):
        dataset_vec = np.zeros(self.n_datasets)
        dataset_vec[self.dataset_id_mapper[dataset]] = 1
        dataset_vec = torch.tensor(dataset_vec.astype("int64"), device=self.device).long()
        return dataset_vec

    def encode_pipeline(self, pipeline_id):
        pipeline_vec = np.zeros(self.n_pipelines)
        pipeline_vec[self.pipeline_id_mapper[pipeline_id]] = 1
        pipeline_vec = torch.tensor(pipeline_vec.astype("int64"), device=self.device).long()
        return pipeline_vec


def get_model(model_name: str, model_config: typing.Dict, seed: int):
    model_class = {
        'dna_regression': DNARegressionModel,
        'mean_regression': MeanBaseline,
        'median_regression': MedianBaseline,
        'per_primitive_regression': PerPrimitiveBaseline,
        'autosklearn': AutoSklearnMetalearner,
        'daglstm_regression': DAGLSTMRegressionModel,
        'hidden_daglstm_regression': HiddenDAGLSTMRegressionModel,
        'linear_regression': LinearRegressionBaseline,
        'random': RandomBaseline,
        'meta_autosklearn': MetaAutoSklearn,
        'probabilistic_matrix_factorization': ProbabilisticMatrixFactorization,
    }[model_name.lower()]
    init_model_config = model_config.get('__init__', {})
    return model_class(**init_model_config, seed=seed)
