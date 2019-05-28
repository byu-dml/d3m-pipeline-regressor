import json
import os
import random
import tarfile
import typing

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset, Sampler


DATA_DIR = './data'


def group_json_objects(json_objects, group_key):
    """
    Groups JSON data by group_key.

    Parameters:
    -----------
    json_objects: List[Dict], JSON compatible list of objects.
    group_key: str, json_objects is grouped by group_key. group_key must be a
        key into each object in json_objects and the corresponding value must
        be hashable. group_key can be a '.' delimited string to access deeply
        nested fields.

    Returns:
    --------
    A dict with key being a group and the value is a list of indices into
    json_objects.
    """
    grouped_objects = {}
    for i, obj in enumerate(json_objects):
        group = obj
        for key_part in group_key.split('.'):
            group = group[key_part]
        if not group in grouped_objects:
            grouped_objects[group] = []
        grouped_objects[group].append(i)
    return grouped_objects


def split_data(data: typing.List[typing.Dict], group_by_key: str, test_size: int, seed: int):
    grouped_data_indices = group_json_objects(data, group_by_key)
    groups = list(grouped_data_indices.keys())

    rnd = random.Random()
    rnd.seed(seed)
    rnd.shuffle(groups)

    train_data = []
    for group in groups[test_size:]:
        for i in grouped_data_indices[group]:
            train_data.append(data[i])

    test_data = []
    for group in groups[:test_size]:
        for i in grouped_data_indices[group]:
            test_data.append(data[i])

    return train_data, test_data


def _extract_tarfile(path):
    assert tarfile.is_tarfile(path)

    dirname = os.path.dirname(path)
    with tarfile.open(path, 'r:*') as tar:
        members = tar.getmembers()
        if len(members) != 1:
            raise ValueError('Expected tar file with 1 member, but got {}'.format(len(members)))
        tar.extractall(os.path.dirname(path))
        extracted_path = os.path.join(dirname, tar.getmembers()[0].name)

    return extracted_path


def get_data(path):
    if tarfile.is_tarfile(path):
        path = _extract_tarfile(path)
    with open(path, 'r') as f:
        data = json.load(f)
    return data


class DropMissingValues:

    def __init__(self, values_to_drop=[]):
        self.values_to_drop = values_to_drop

    def fit(
        self, data: typing.List[typing.Dict[str, typing.Union[int, float]]]
    ):
        for key, is_missing in pd.DataFrame(data).isna().any().iteritems():
            if is_missing:
                self.values_to_drop.append(key)

    def predict(
        self, data: typing.List[typing.Dict[str, typing.Union[int, float]]]
    ):
        for instance in data:
            for key in self.values_to_drop:
                instance.pop(key, None)
        return data


class StandardScaler:
    """
    Transforms data by subtracting the mean and scaling by the standard
    deviation. Drops columns that have 0 standard deviation. Clips values to
    numpy resolution, min, and max.
    """

    def __init__(self):
        self.means = None
        self.stds = None

    def fit(
        self, data: typing.List[typing.Dict[str, typing.Union[int, float]]]
    ):
        values_map = {}
        for instance in data:
            for key, value in instance.items():
                if key not in values_map:
                    values_map[key] = []
                values_map[key].append(value)

        self.means = {}
        self.stds = {}
        for key, values in values_map.items():
            self.means[key] = np.mean(values)
            self.stds[key] = np.std(values, ddof=1)

    def predict(
        self, data: typing.List[typing.Dict[str, typing.Union[int, float]]]
    ):
        if self.means is None or self.stds is None:
            raise Exception('StandardScaler not fit')

        transformed_data = []
        for instance in data:
            transformed_instance = {}
            for key, value in instance.items():
                if self.stds[key] != 0:  # drop columns with 0 std dev
                    transformed_instance[key] = (value - self.means[key]) / self.stds[key]

            transformed_data.append(transformed_instance)

        return transformed_data


def encode_dag(dag: typing.Sequence[typing.Sequence[typing.Any]]):
    """
    Converts a directed acyclic graph DAG) to a string. If two DAGs have the same encoding string, then they are equal.
    However, two isomorphic DAGs may have different encoding strings.

    Parameters
    ----------
    dag: typing.List[typing.List[typing.Any]]
        A representation of a dag. Each element in the outer list represents a vertex. Each inner list or vertex
        contains a reference to the outer list, representing edges.
    """
    return ''.join(''.join(str(edge) for edge in vertex) for vertex in dag)


def preprocess_data(train_data, test_data):
    train_metafeatures = []
    for instance in train_data:
        train_metafeatures.append(instance['metafeatures'])
        for step in instance['pipeline']['steps']:
            step['name'] = step['name'].replace('.', '_')

    test_metafeatures = []
    for instance in test_data:
        test_metafeatures.append(instance['metafeatures'])
        for step in instance['pipeline']['steps']:
            step['name'] = step['name'].replace('.', '_')

    # drop metafeature if missing for any instance
    dropper = DropMissingValues()
    dropper.fit(train_metafeatures)
    train_metafeatures = dropper.predict(train_metafeatures)
    test_metafeatures = dropper.predict(test_metafeatures)

    # scale data to unit mean and unit standard deviation
    scaler = StandardScaler()
    scaler.fit(train_metafeatures)
    train_metafeatures = scaler.predict(train_metafeatures)
    test_metafeatures = scaler.predict(test_metafeatures)

    # convert from dict to list
    for instance, mf_instance in zip(train_data, train_metafeatures):
        instance['metafeatures'] = [value for key, value in sorted(mf_instance.items())]
        pipeline_dag = (step['inputs'] for step in instance['pipeline']['steps'])
        instance['pipeline_structure'] = encode_dag(pipeline_dag)

    for instance, mf_instance in zip(test_data, test_metafeatures):
        instance['metafeatures'] = [value for key, value in sorted(mf_instance.items())]
        pipeline_dag = (step['inputs'] for step in instance['pipeline']['steps'])
        instance['pipeline_structure'] = encode_dag(pipeline_dag)

    return train_data, test_data


class Dataset(Dataset):
    """
    A subclass of torch.utils.data.Dataset for handling simple JSON structed
    data.

    Parameters:
    -----------
    data: List[Dict], JSON structed data.
    features_key: str, the key into each element of data whose value is a list
        of features used for input to a PyTorch network.
    target_key: str, the key into each element of data whose value is the
        target used for a PyTorch network.
    device": str, the device onto which the data will be loaded
    """

    def __init__(
        self, data: typing.List[typing.Dict], features_key: str,
        target_key: str, task_type: str, device: str
    ):
        self.data = data
        self.features_key = features_key
        self.target_key = target_key
        self.task_type = task_type
        if self.task_type == "CLASSIFICATION":
            self._y_dtype = torch.int64
        elif self.task_type == "REGRESSION":
            self._y_dtype = torch.float32
        self.device = device

    def __getitem__(self, item: int):
        x = torch.tensor(
            self.data[item][self.features_key], dtype=torch.float32, device=self.device
        )
        y = torch.tensor(
            self.data[item][self.target_key],
            dtype=self._y_dtype,
            device=self.device
        )
        return x, y

    def __len__(self):
        return len(self.data)


class RandomSampler(Sampler):
    """
    Samples indices uniformly without replacement.

    Parameters
    ----------
    n: int
        the number of indices to sample
    seed: int
        used to reproduce randomization
    """

    def __init__(self, n, seed):
        self.n = n
        self._indices = list(range(n))
        self._random = random.Random()
        self._random.seed(seed)

    def __iter__(self):
        self._random.shuffle(self._indices)
        return iter(self._indices)

    def __len__(self):
        return self.n


class GroupDataLoader(object):
    """
    Batches a dataset for PyTorch Neural Network training. Partitions the
    dataset so that batches belong to the same group.

    Parameters:
    -----------
    data: List[Dict], JSON compatible list of objects representing a dataset.
        dataset_class must know how to parse the data given dataset_params.
    group_key: str, pipeline run data is grouped by group_key and each
        batch of data comes from only one group. group_key must be a key into
        each element of the pipeline run data. the value of group_key must be
        hashable.
    dataset_class: Type[torch.utils.data.Dataset], the class used to make
        dataset instances after the dataset is partitioned.
    dataset_params: dict, extra parameters needed to instantiate dataset_class
    batch_size: int, the number of data points in each batch
    drop_last: bool, default False. whether to drop the last incomplete batch.
    shuffle: bool, default True. whether to randomize the batches.
    """

    def __init__(
        self, data: typing.List[typing.Dict], group_key: str,
        dataset_class: typing.Type[Dataset], dataset_params: dict,
        batch_size: int, drop_last: bool, shuffle: bool, seed: int
    ):
        self.data = data
        self.group_key = group_key
        self.dataset_class = dataset_class
        self.dataset_params = dataset_params
        self.batch_size = batch_size
        self.drop_last = drop_last
        self.shuffle = shuffle
        self.seed = seed

        self._random = random.Random()
        self._random.seed(seed)

        self._init_dataloaders()
        self._init_group_metadataloader()

    def _init_dataloaders(self):
        """
        Groups self.data based on group_key. Creates a
        torch.utils.data.DataLoader for each group, using self.dataset_class.
        """
        # group the data
        grouped_data = group_json_objects(self.data, self.group_key)

        # create dataloaders
        self._group_dataloaders = {}
        for group, group_indices in grouped_data.items():
            group_data = [self.data[i] for i in group_indices]
            group_dataset = self.dataset_class(group_data, **self.dataset_params)
            new_dataloader = self._get_data_loader(
                group_dataset
            )
            # assert(len(new_dataloader) != 0)
            self._group_dataloaders[group] = new_dataloader

    def _get_data_loader(self, data):
        if self.shuffle:
            sampler = RandomSampler(len(data), self._randint())
        else:
            sampler = None
        dataloader = DataLoader(
            dataset = data,
            sampler =  sampler,
            batch_size = self.batch_size,
            drop_last = self.drop_last
        )
        # assert(len(dataloader) != 0)
        return dataloader

    def _randint(self):
        return self._random.randint(0,2**32-1)

    def _init_group_metadataloader(self):
        """
        Creates a dataloader which randomizes the batches over the groups. This
        allows the order of the batches to be independent of the groups.
        """
        self._group_batches = []
        for group, group_dataloader in self._group_dataloaders.items():
            # assert(len(group_dataloader) != 0)
            self._group_batches += [group] * len(group_dataloader)
        self._random.shuffle(self._group_batches)

    def __iter__(self):
        return iter(self._iter())

    def _iter(self):
        group_dataloader_iters = {}
        for group in self._group_batches:
            if not group in group_dataloader_iters:
                group_dataloader_iters[group] = iter(
                    self._group_dataloaders[group]
                )
            x_batch, y_batch = next(group_dataloader_iters[group])
            # since all pipeline are the same in this group, just grab one of them
            pipeline = self._group_dataloaders[group].dataset.data[0]["pipeline"]
            yield (group, pipeline, x_batch), y_batch
        raise StopIteration()

    def __len__(self):
        return len(self._group_batches)

class RNNDataset(Dataset):
    def __init__(self, data: dict, features_key: str, target_key: str, task_type: str, device: str,
                 prim_to_enc: dict):
        super(RNNDataset, self).__init__(data, features_key, target_key, task_type, device)
        self.pipeline_key = 'pipeline'
        self.primitive_to_enc = prim_to_enc

    def __getitem__(self, index):
        (x, y) = super().__getitem__(index)
        item = self.data[index]
        pipeline = item[self.pipeline_key]
        encoded_pipeline = self.encode_pipeline(pipeline)
        return (encoded_pipeline, x, y)

    def encode_pipeline(self, pipeline):
        # Create a tensor of encoded primitives
        encoding = []
        for primitive in pipeline['steps']:
            primitive_name = primitive['name']
            encoded_primitive = self.primitive_to_enc[primitive_name]
            encoding.append(encoded_primitive)

        encoding = torch.tensor(encoding, dtype=torch.float32)

        if "cuda" in self.device:
            encoding = encoding.cuda()

        return encoding

class RNNDataLoader(GroupDataLoader):
    # TODO: Maybe the data loaders shouldn't have a group key passed in but should be instantiated as a local var
    def __init__(self, data: dict, group_key: str, dataset_params: dict,
                 batch_size: int, drop_last: bool, shuffle: bool, seed: int):
        dataset_class = RNNDataset
        super().__init__(data, group_key, dataset_class, dataset_params, batch_size, drop_last, shuffle, seed)

        self.pipeline_structures = {}
        grouped_by_structure = group_json_objects(data, group_key)
        for (group, group_indices) in grouped_by_structure.items():
            index = group_indices[0]
            item = data[index]
            pipeline = item['pipeline']['steps']
            group_structure = [primitive['inputs'] for primitive in pipeline]
            self.pipeline_structures[group] = group_structure

    def _iter(self):
        group_dataloader_iters = {}
        for group in self._group_batches:
            if not group in group_dataloader_iters:
                group_dataloader_iters[group] = iter(self._group_dataloaders[group])

            # Get a batch of encoded pipelines, metafeatures, and targets
            (pipeline_batch, x_batch, y_batch) = next(group_dataloader_iters[group])

            # Get the structure of the pipelines in this group so the RNN can parse the pipeline
            group_structure = self.pipeline_structures[group]

            yield ((group_structure, pipeline_batch, x_batch), y_batch)
        raise StopIteration()
