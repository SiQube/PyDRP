import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
import os
import torch
import warnings

class PreprocessingPipeline():
    def __init__(self,
                 data,
                 root = ".data/",):
        self.root = root
        self.data = data
    def preprocess(self):
        """
        Takes the data, returns a DataFrame with columns CELL_ID, DRUG_ID, and Y and saves to `root/processed/`. Needs to be overriden.
        """
        raise NotImplementedError
    def get_cell_lines(self):
        """
        Returns the features for the cell-lines indexed by the identifier from CELL_ID. Needs to be overriden.
        """
        raise NotImplementedError
    def get_drugs(self):
        """
        Returns the SMILES for the drugs indexed by the identifier from DRUG_ID. Needs to be overriden.
        """
        raise NotImplementedError

class TargetPipeline():
    def __init__(self):
        self.fitted = False
    def fit(self, x):
        """
        Takes the train input, and fits the preprocessor to it. Needs to be overriden.
        """
        raise NotImplementedError
    def __call__(self, x):
        """
        Takes train or test partitions, and transforms the data. Needs to be overriden.
        """
        raise NotImplementedError
    def transform(self, x):
        """
        Takes train or test partitions, and transforms the data.
        """
        assert self.fitted, "You are trying to transform data using a non-fitted processor"
        return self(x)

class DrugFeaturizer():
    def __init__(self):
        pass
    def __call__(self, smiles_list, drugs):
        """
        Takes A list of smiles and returns a dictionary with the chosen representation. Needs to be overriden.
        """
        raise NotImplementedError
    def __str__(self):
        """
        returns a description of the featurization
        """
        raise NotImplementedError

class Splitter():
    def __init__(self,
                 data,
                 k = 25,
                 partition_column = None,
                 seed = 3558,
                 exclude_from_test = []):
        self.partition_column = partition_column
        self.k = k
        self.seed = seed
        self.exclude_from_test = exclude_from_test
        self.data = data.reset_index(drop=True)
        if self.partition_column is None:
            self.elements = self.data.index.to_numpy()
        else:
            self.elements = self.data.loc[:, self.partition_column].unique()
        self.partitioned = False
    def get_partitions(self, shuffle = True):
        rng = np.random.default_rng(self.seed)
        if shuffle:
            perm = rng.permutation(len(self.elements))
            elements = self.elements[perm]
        else:
            elements = self.elements.copy()
        self.splits = np.array_split(elements, self.k)
        self.partitioned = True
        self.fitted = True
    def get_folds(self):
        assert self.partitioned, "The data was not partitioned yet"
        rng = np.random.default_rng(self.seed)
        test = np.arange(self.k)
        valid_partition = False
        while not valid_partition:
            val = rng.permutation(self.k)
            valid_partition = (val != test).all()
        self.test_folds = test
        self.val_folds = val
    def fit(self, shuffle = True):
        self.get_partitions(shuffle)
        self.get_folds()
    def __getitem__(self, idx):
        """
        Returns train, validation and test partitions, following the specified partition column, and excluding from
        test the specified elements in exclude_from_test.
        """
        assert self.fitted, "You are trying to get splits before fitting the splitter"
        train_folds = [i for i in range(self.k) if (i != self.val_folds[idx]) and  i != self.test_folds[idx]]
        training_idx = np.concatenate([self.splits[f] for f in train_folds])
        val_idx = self.splits[self.val_folds[idx]]
        test_idx = [el for el in self.splits[self.test_folds[idx]] if el not in self.exclude_from_test]
        if self.partition_column is None:
            return self.data.loc[training_idx], self.data.loc[val_idx], self.data.loc[test_idx]
        else:
            return (self.data.query(f"{self.partition_column} in @training_idx"),
        self.data.query(f"{self.partition_column} in @val_idx"),
        self.data.query(f"{self.partition_column} in @test_idx"))

class DatasetManager():
    def __init__(self,
                 processing_pipeline,
                 target_processor,
                 drug_featurizer,
                 k=25,
                 partition_column = None,
                 exclude_from_test = [],
                 ):
        self.ppl = processing_pipeline
        self.drug_featurizer = drug_featurizer
        self.processed_data = self.ppl.preprocess()
        self.splitter = Splitter(self.processed_data, k, partition_column, exclude_from_test)
        self.splitter.fit()
        self.target_processor = target_processor
    def get_data(self):
        return self.processed_data
    def get_partition(self, idx):
        train, val, test = self.splitter[idx]
        self.target_processor.fit(train)
        return self.target_processor.transform(train), self.target_processor.transform(test), self.target_processor.transform(val)
    def get_cell_lines(self):
        return self.ppl.get_cell_lines()
    def get_drugs(self):
        smiles =  self.ppl.get_drugs()
        path_cache = f"data/processed/{str(self.ppl)}_{str(self.drug_featurizer)}.pt"
        if os.path.exists(path_cache):
            drug_dict = torch.load(path_cache)
        else:
            drug_dict = self.drug_featurizer(list(smiles.iloc[:, 0].to_numpy()), smiles.index.to_numpy())
            torch.save(drug_dict, path_cache)
        featurized_drugs = set(list(drug_dict.keys()))
        input_drugs = set(list(smiles.index.to_numpy()))
        diff_drugs = featurized_drugs.difference(input_drugs)
        if len(diff_drugs) > 0:
            warnings.warn(f"it was not possible to featurize {diff_drugs}", RuntimeWarning)
        return drug_dict

class LogMinMaxScaling(TargetPipeline):
    """ Applies log transformation and MinMaxScaling to the target"""
    def __init__(self,
                 offset = 1,
                 target_range = (-1, 1)):
        super(LogMinMaxScaling).__init__()
        self.offset = offset
        self.minmax = MinMaxScaler(target_range)
    def fit(self, x):
        self.minmax.fit(X = np.log(x["Y"].to_numpy()[:, None] + self.offset))
        self.fitted=True
    def __call__(self, x):
        Y_t = self.minmax.transform(np.log(x["Y"].to_numpy()[:, None] + self.offset)).squeeze()
        return x.assign(Y = Y_t)

class MinMaxScaling(TargetPipeline):
    """ Applies MinMaxScaling to the target"""
    def __init__(self,
                 target_range = (-1, 1)):
        super(LogMinMaxScaling).__init__()
        self.minmax = MinMaxScaler(target_range)
    def fit(self, x):
        self.minmax.fit(X = x["Y"].to_numpy()[:, None])
        self.fitted=True
    def __call__(self, x):
        Y_t = self.minmax.transform(x["Y"].to_numpy()[:, None]).squeeze()
        return x.assign(Y = Y_t)