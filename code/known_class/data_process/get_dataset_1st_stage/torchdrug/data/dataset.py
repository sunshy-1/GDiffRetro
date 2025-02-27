import os
import csv
import math
import lmdb
import pickle
import logging
import warnings
from collections import defaultdict
from collections.abc import Sequence

from tqdm import tqdm

import numpy as np

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold
import torch
from torch.utils import data as torch_data

from torchdrug import core, data, utils


logger = logging.getLogger(__name__)


class MoleculeDataset(torch_data.Dataset, core.Configurable):
    """
    Molecule dataset.

    Each sample contains a molecule graph, and any number of prediction targets.
    """

    @utils.copy_args(data.Molecule.from_molecule)
    def load_smiles(self, smiles_list, targets, transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from SMILES and targets.

        Parameters:
            smiles_list (list of str): SMILES strings
            targets (dict of list): prediction targets
            transform (Callable, optional): data transformation function
            lazy (bool, optional): if lazy mode is used, the molecules are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        num_sample = len(smiles_list)
        if num_sample > 1000000:
            warnings.warn("Preprocessing molecules of a large dataset consumes a lot of CPU memory and time. "
                          "Use load_smiles(lazy=True) to construct molecules in the dataloader instead.")
        for field, target_list in targets.items():
            if len(target_list) != num_sample:
                raise ValueError("Number of target `%s` doesn't match with number of molecules. "
                                 "Expect %d but found %d" % (field, num_sample, len(target_list)))

        self.transform = transform
        self.lazy = lazy
        self.kwargs = kwargs
        self.smiles_list = []
        self.data = []
        self.targets = defaultdict(list)

        if verbose:
            smiles_list = tqdm(smiles_list, "Constructing molecules from SMILES")
        for i, smiles in enumerate(smiles_list):
            if not self.lazy or len(self.data) == 0:
                mol = Chem.MolFromSmiles(smiles)
                if not mol:
                    logger.debug("Can't construct molecule from SMILES `%s`. Ignore this sample." % smiles)
                    continue
                mol = data.Molecule.from_molecule(mol, **kwargs)
            else:
                mol = None
            self.data.append(mol)
            self.smiles_list.append(smiles)
            for field in targets:
                self.targets[field].append(targets[field][i])

    @utils.copy_args(load_smiles)
    def load_csv(self, csv_file, smiles_field="smiles", target_fields=None, verbose=0, **kwargs):
        """
        Load the dataset from a csv file.

        Parameters:
            csv_file (str): file name
            smiles_field (str, optional): name of the SMILES column in the table.
                Use ``None`` if there is no SMILES column.
            target_fields (list of str, optional): name of target columns in the table.
                Default is all columns other than the SMILES column.
            verbose (int, optional): output verbose level
            **kwargs
        """
        if target_fields is not None:
            target_fields = set(target_fields)
        
        with open(csv_file, "r") as fin:
            reader = csv.reader(fin)
            if verbose:
                reader = iter(tqdm(reader, "Loading %s" % csv_file, utils.get_line_count(csv_file)))
            fields = next(reader)
            smiles = []
            targets = defaultdict(list)
            for values in reader:
                if not any(values):
                    continue
                if smiles_field is None:
                    smiles.append("")
                for field, value in zip(fields, values):
                    if field == smiles_field:
                        smiles.append(value)
                    elif target_fields is None or field in target_fields:
                        value = utils.literal_eval(value)
                        if value == "":
                            value = math.nan
                        targets[field].append(value)

        self.load_smiles(smiles, targets, verbose=verbose, **kwargs)

    def load_pickle(self, pkl_file, verbose=0):
        """
        Load the dataset from a pickle file.

        Parameters:
            pkl_file (str): file name
            verbose (int, optional): output verbose level
        """
        with utils.smart_open(pkl_file, "rb") as fin:
            num_sample, tasks = pickle.load(fin)

            self.smiles_list = []
            self.data = []
            self.targets = {task: [] for task in tasks}
            indexes = range(num_sample)
            if verbose:
                indexes = tqdm(indexes, "Loading %s" % pkl_file)
            for i in indexes:
                smiles, mol, values = pickle.load(fin)
                self.smiles_list.append(smiles)
                self.data.append(mol)
                for task, value in zip(tasks, values):
                    self.targets[task] = value

    def save_pickle(self, pkl_file, verbose=0):
        """
        Save the dataset to a pickle file.

        Parameters:
            pkl_file (str): file name
            verbose (int, optional): output verbose level
        """
        with utils.smart_open(pkl_file, "wb") as fout:
            num_sample = len(self.data)
            tasks = self.targets.keys()
            pickle.dump((num_sample, tasks), fout)

            indexes = range(num_sample)
            if verbose:
                indexes = tqdm(indexes, "Dumping to %s" % pkl_file)
            for i in indexes:
                values = [v[i] for v in self.targets.values()]
                pickle.dump((self.smiles_list[i], self.data[i], values), fout)

    def _standarize_index(self, index, count):
        if isinstance(index, slice):
            start = index.start or 0
            if start < 0:
                start += count
            stop = index.stop or count
            if stop < 0:
                stop += count
            step = index.step or 1
            index = range(start, stop, step)
        elif not isinstance(index, list):
            raise ValueError("Unknown index `%s`" % index)
        return index

    def get_item(self, index):
        if getattr(self, "lazy", False):
            # TODO: what if the smiles is invalid here?
            item = {"graph": data.Molecule.from_smiles(self.smiles_list[index], **self.kwargs)}
        else:
            item = {"graph": self.data[index]}
        item.update({k: v[index] for k, v in self.targets.items()})
        if self.transform:
            item = self.transform(item)
        return item

    def __getitem__(self, index):
        if isinstance(index, int):
            return self.get_item(index)

        index = self._standarize_index(index, len(self))
        return [self.get_item(i) for i in index]

    @property
    def tasks(self):
        """List of tasks."""
        return list(self.targets.keys())

    @property
    def node_feature_dim(self):
        """Dimension of node features."""
        return self.data[0].node_feature.shape[-1]

    @property
    def edge_feature_dim(self):
        """Dimension of edge features."""
        return self.data[0].edge_feature.shape[-1]

    @property
    def num_atom_type(self):
        """Number of different atom types."""
        return len(self.atom_types)

    @property
    def num_bond_type(self):
        """Number of different bond types."""
        return len(self.bond_types)

    @utils.cached_property
    def atom_types(self):
        """All atom types."""
        atom_types = set()

        if getattr(self, "lazy", False):
            warnings.warn("Calling this function for dataset with lazy=True may take a large amount of time.")             
            for smiles in self.smiles_list:
                graph = data.Molecule.from_smiles(smiles, **self.kwargs)
                atom_types.update(graph.atom_type.tolist())
        else:
            for graph in self.data:
                atom_types.update(graph.atom_type.tolist())

        return sorted(atom_types)

    @utils.cached_property
    def bond_types(self):
        """All bond types."""
        bond_types = set()

        if getattr(self, "lazy", False):
            warnings.warn("Calling this function for dataset with lazy=True may take a large amount of time.")             
            for smiles in self.smiles_list:
                graph = data.Molecule.from_smiles(smiles, **self.kwargs)
                bond_types.update(graph.edge_list[:, 2].tolist())
        else:
            for graph in self.data:
                bond_types.update(graph.edge_list[:, 2].tolist())

        return sorted(bond_types)

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        lines = [
            "#sample: %d" % len(self),
            "#task: %d" % len(self.tasks),
        ]
        return "%s(\n  %s\n)" % (self.__class__.__name__, "\n  ".join(lines))


class ReactionDataset(MoleculeDataset, core.Configurable):
    """
    Chemical reaction dataset.

    Each sample contains two molecule graphs, and any number of prediction targets.
    """

    @utils.copy_args(data.Molecule.from_molecule)
    def load_smiles(self, smiles_list, targets, transform=None, verbose=0, **kwargs):
        """
        Load the dataset from SMILES and targets.

        Parameters:
            smiles_list (list of str): SMILES strings
            targets (dict of list): prediction targets
            transform (Callable, optional): data transformation function
            verbose (int, optional): output verbose level
            **kwargs
        """

        num_sample = len(smiles_list)
        for field, target_list in targets.items():
            if len(target_list) != num_sample:
                raise ValueError("Number of target `%s` doesn't match with number of molecules. "
                                 "Expect %d but found %d" % (field, num_sample, len(target_list)))

        self.smiles_list = []
        self.data = []
        self.targets = defaultdict(list)

        if verbose:
            smiles_list = tqdm(smiles_list, "Constructing molecules from SMILES")
        for i, smiles in enumerate(smiles_list):
            smiles_reactant, agent, smiles_product = smiles.split(">")
            mols = []
            for _smiles in [smiles_reactant, smiles_product]:
                mol = Chem.MolFromSmiles(_smiles)
                if not mol:
                    logger.debug("Can't construct molecule from SMILES `%s`. Ignore this sample." % _smiles)
                    break
                mol = data.Molecule.from_molecule(mol, **kwargs)
                mols.append(mol)
            else:
                self.data.append(mols)
                self.smiles_list.append(smiles)
                for field in targets:
                    self.targets[field].append(targets[field][i])
        self.transform = transform

    @property
    def node_feature_dim(self):
        """Dimension of node features."""
        return self.data[0][0].node_feature.shape[-1]

    @property
    def edge_feature_dim(self):
        """Dimension of edge features."""
        return self.data[0][0].edge_feature.shape[-1]

    @property
    def num_atom_type(self):
        """Number of different atom types."""
        return len(self.atom_types)

    @property
    def num_bond_type(self):
        """Number of different bond types."""
        return len(self.bond_types)

    @utils.cached_property
    def atom_types(self):
        """All atom types."""
        atom_types = set()
        for graphs in self.data:
            for graph in graphs:
                atom_types.update(graph.atom_type.tolist())
        return sorted(atom_types)

    @utils.cached_property
    def bond_types(self):
        """All bond types."""
        bond_types = set()
        for graphs in self.data:
            for graph in graphs:
                bond_types.update(graph.edge_list[:, 2].tolist())
        return sorted(bond_types)

    def __len__(self):
        return len(self.data)


class NodeClassificationDataset(torch_data.Dataset, core.Configurable):
    """
    Node classification dataset.

    The whole dataset contains one graph, where each node has its own node feature and label.
    """

    def load_tsv(self, node_file, edge_file, verbose=0):
        """
        Load the edge list from a tsv file.

        Parameters:
            node_file (str): node feature and label file
            edge_file (str): edge list file
            verbose (int, optional): output verbose level
        """
        inv_node_vocab = {}
        inv_label_vocab = {}
        node_feature = []
        node_label = []

        with open(node_file, "r") as fin:
            reader = csv.reader(fin, delimiter="\t")
            if verbose:
                reader = tqdm(reader, "Loading %s" % node_file, utils.get_line_count(node_file))
            for tokens in reader:
                node_token = tokens[0]
                feature_tokens = tokens[1: -1]
                label_token = tokens[-1]
                inv_node_vocab[node_token] = len(inv_node_vocab)
                if label_token not in inv_label_vocab:
                    inv_label_vocab[label_token] = len(inv_label_vocab)
                feature = [utils.literal_eval(f) for f in feature_tokens]
                label = inv_label_vocab[label_token]
                node_feature.append(feature)
                node_label.append(label)

        edge_list = []

        with open(edge_file, "r") as fin:
            reader = csv.reader(fin, delimiter="\t")
            if verbose:
                reader = tqdm(reader, "Loading %s" % edge_file, utils.get_line_count(edge_file))
            for tokens in reader:
                h_token, t_token = tokens
                if h_token not in inv_node_vocab:
                    inv_node_vocab[h_token] = len(inv_node_vocab)
                h = inv_node_vocab[h_token]
                if t_token not in inv_node_vocab:
                    inv_node_vocab[t_token] = len(inv_node_vocab)
                t = inv_node_vocab[t_token]
                edge_list.append((h, t))

        self.load_edge(edge_list, node_feature, node_label, inv_node_vocab=inv_node_vocab,
                       inv_label_vocab=inv_label_vocab)

    def load_edge(self, edge_list, node_feature, node_label, node_vocab=None, inv_node_vocab=None, label_vocab=None,
                  inv_label_vocab=None):
        node_vocab, inv_node_vocab = self._standarize_vocab(node_vocab, inv_node_vocab)
        label_vocab, inv_label_vocab = self._standarize_vocab(label_vocab, inv_label_vocab)

        self.num_labeled_node = len(node_feature)
        if len(node_vocab) > len(node_feature):
            logger.warning("Missing features & labels for %d / %d nodes" %
                           (len(node_vocab) - len(node_feature), len(node_vocab)))
            dummy_label = 0
            dummy_feature = [0] * len(node_feature[0])
            node_label += [dummy_label] * (len(node_vocab) - len(node_feature))
            node_feature += [dummy_feature] * (len(node_vocab) - len(node_feature))

        self.graph = data.Graph(edge_list, num_node=len(node_vocab), node_feature=node_feature)
        with self.graph.node():
            self.graph.node_label = torch.as_tensor(node_label)
        self.node_vocab = node_vocab
        self.inv_node_vocab = inv_node_vocab
        self.label_vocab = label_vocab
        self.inv_node_vocab = inv_label_vocab

    def _standarize_vocab(self, vocab, inverse_vocab):
        if vocab is not None:
            if isinstance(vocab, dict):
                assert set(vocab.keys()) == set(range(len(vocab))), "Vocab keys should be consecutive numbers"
                vocab = [vocab[k] for k in range(len(vocab))]
            if inverse_vocab is None:
                inverse_vocab = {v: i for i, v in enumerate(vocab)}
        if inverse_vocab is not None:
            assert set(inverse_vocab.values()) == set(range(len(inverse_vocab))), \
                "Inverse vocab values should be consecutive numbers"
            if vocab is None:
                vocab = sorted(inverse_vocab, key=lambda k: inverse_vocab[k])
        return vocab, inverse_vocab

    @property
    def num_node(self):
        """Number of nodes."""
        return self.graph.num_node

    @property
    def num_edge(self):
        """Number of edges."""
        return self.graph.num_edge

    @property
    def node_feature_dim(self):
        """Dimension of node features."""
        return self.graph.node_feature.shape[-1]

    def __getitem__(self, index):
        return {
            "node_index": index,
            "label": self.graph.node_label[index]
        }

    def __len__(self):
        return self.num_labeled_node

    def __repr__(self):
        lines = [
            "#node: %d" % self.num_node,
            "#edge: %d" % self.num_edge,
            "#class: %d" % len(self.label_vocab),
        ]
        return "%s(\n  %s\n)" % (self.__class__.__name__, "\n  ".join(lines))


class KnowledgeGraphDataset(torch_data.Dataset, core.Configurable):
    """
    Knowledge graph dataset.

    The whole dataset contains one knowledge graph.
    """

    def load_triplet(self, triplets, entity_vocab=None, relation_vocab=None, inv_entity_vocab=None,
                     inv_relation_vocab=None):
        """
        Load the dataset from triplets.
        The mapping between indexes and tokens is specified through either vocabularies or inverse vocabularies.

        Parameters:
            triplets (array_like): triplets of shape :math:`(n, 3)`
            entity_vocab (dict of str, optional): maps entity indexes to tokens
            relation_vocab (dict of str, optional): maps relation indexes to tokens
            inv_entity_vocab (dict of str, optional): maps tokens to entity indexes
            inv_relation_vocab (dict of str, optional): maps tokens to relation indexes
        """
        entity_vocab, inv_entity_vocab = self._standarize_vocab(entity_vocab, inv_entity_vocab)
        relation_vocab, inv_relation_vocab = self._standarize_vocab(relation_vocab, inv_relation_vocab)

        num_node = len(entity_vocab) if entity_vocab else None
        num_relation = len(relation_vocab) if relation_vocab else None
        self.graph = data.Graph(triplets, num_node=num_node, num_relation=num_relation)
        self.entity_vocab = entity_vocab
        self.relation_vocab = relation_vocab
        self.inv_entity_vocab = inv_entity_vocab
        self.inv_relation_vocab = inv_relation_vocab

    def load_tsv(self, tsv_file, verbose=0):
        """
        Load the dataset from a tsv file.

        Parameters:
            tsv_file (str): file name
            verbose (int, optional): output verbose level
        """
        inv_entity_vocab = {}
        inv_relation_vocab = {}
        triplets = []

        with open(tsv_file, "r") as fin:
            reader = csv.reader(fin, delimiter="\t")
            if verbose:
                reader = tqdm(reader, "Loading %s" % tsv_file)
            for tokens in reader:
                h_token, r_token, t_token = tokens
                if h_token not in inv_entity_vocab:
                    inv_entity_vocab[h_token] = len(inv_entity_vocab)
                h = inv_entity_vocab[h_token]
                if r_token not in inv_relation_vocab:
                    inv_relation_vocab[r_token] = len(inv_relation_vocab)
                r = inv_relation_vocab[r_token]
                if t_token not in inv_entity_vocab:
                    inv_entity_vocab[t_token] = len(inv_entity_vocab)
                t = inv_entity_vocab[t_token]
                triplets.append((h, t, r))

        self.load_triplet(triplets, inv_entity_vocab=inv_entity_vocab, inv_relation_vocab=inv_relation_vocab)

    def load_tsvs(self, tsv_files, verbose=0):
        """
        Load the dataset from multiple tsv files.

        Parameters:
            tsv_files (list of str): list of file names
            verbose (int, optional): output verbose level
        """
        inv_entity_vocab = {}
        inv_relation_vocab = {}
        triplets = []
        num_samples = []

        for tsv_file in tsv_files:
            with open(tsv_file, "r") as fin:
                reader = csv.reader(fin, delimiter="\t")
                if verbose:
                    reader = tqdm(reader, "Loading %s" % tsv_file, utils.get_line_count(tsv_file))

                num_sample = 0
                for tokens in reader:
                    h_token, r_token, t_token = tokens
                    if h_token not in inv_entity_vocab:
                        inv_entity_vocab[h_token] = len(inv_entity_vocab)
                    h = inv_entity_vocab[h_token]
                    if r_token not in inv_relation_vocab:
                        inv_relation_vocab[r_token] = len(inv_relation_vocab)
                    r = inv_relation_vocab[r_token]
                    if t_token not in inv_entity_vocab:
                        inv_entity_vocab[t_token] = len(inv_entity_vocab)
                    t = inv_entity_vocab[t_token]
                    triplets.append((h, t, r))
                    num_sample += 1
            num_samples.append(num_sample)

        self.load_triplet(triplets, inv_entity_vocab=inv_entity_vocab, inv_relation_vocab=inv_relation_vocab)
        self.num_samples = num_samples

    def _standarize_vocab(self, vocab, inverse_vocab):
        if vocab is not None:
            if isinstance(vocab, dict):
                assert set(vocab.keys()) == set(range(len(vocab))), "Vocab keys should be consecutive numbers"
                vocab = [vocab[k] for k in range(len(vocab))]
            if inverse_vocab is None:
                inverse_vocab = {v: i for i, v in enumerate(vocab)}
        if inverse_vocab is not None:
            assert set(inverse_vocab.values()) == set(range(len(inverse_vocab))), \
                "Inverse vocab values should be consecutive numbers"
            if vocab is None:
                vocab = sorted(inverse_vocab, key=lambda k: inverse_vocab[k])
        return vocab, inverse_vocab

    @property
    def num_entity(self):
        """Number of entities."""
        return self.graph.num_node

    @property
    def num_triplet(self):
        """Number of triplets."""
        return self.graph.num_edge

    @property
    def num_relation(self):
        """Number of relations."""
        return self.graph.num_relation

    def __getitem__(self, index):
        return self.graph.edge_list[index]

    def __len__(self):
        return self.graph.num_edge

    def __repr__(self):
        lines = [
            "#entity: %d" % self.num_entity,
            "#relation: %d" % self.num_relation,
            "#triplet: %d" % self.num_triplet,
        ]
        return "%s(\n  %s\n)" % (self.__class__.__name__, "\n  ".join(lines))


class ProteinDataset(MoleculeDataset, core.Configurable):
    """
    Protein dataset.

    Each sample contains a protein graph, and any number of prediction targets.
    """

    @utils.copy_args(data.Protein.from_sequence)
    def load_sequence(self, sequences, targets, attributes=None, transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from protein sequences and targets.

        Parameters:
            sequences (list of str): protein sequence strings
            targets (dict of list): prediction targets
            attributes (dict of list): protein-level attributes
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the proteins are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        num_sample = len(sequences)
        if num_sample > 1000000:
            warnings.warn("Preprocessing proteins of a large dataset consumes a lot of CPU memory and time. "
                          "Use load_sequence(lazy=True) to construct molecules in the dataloader instead.")
        for field, target_list in targets.items():
            if len(target_list) != num_sample:
                raise ValueError("Number of target `%s` doesn't match with number of molecules. "
                                 "Expect %d but found %d" % (field, num_sample, len(target_list)))

        self.transform = transform
        self.lazy = lazy
        self.kwargs = kwargs
        self.sequences = []
        self.data = []
        self.targets = defaultdict(list)

        if verbose:
            sequences = tqdm(sequences, "Constructing proteins from sequences")
        for i, sequence in enumerate(sequences):
            if not self.lazy or len(self.data) == 0:
                protein = data.Protein.from_sequence(sequence, **kwargs)
            else:
                protein = None
            if attributes is not None:
                with protein.graph():
                    for field in attributes:
                        setattr(protein, field, attributes[field][i])
            self.data.append(protein)
            self.sequences.append(sequence)
            for field in targets:
                self.targets[field].append(targets[field][i])

    @utils.copy_args(load_sequence)
    def load_lmdbs(self, lmdb_files, sequence_field="primary", target_fields=None, number_field="num_examples",
                   transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from lmdb files.

        Parameters:
            lmdb_files (list of str): list of lmdb files
            sequence_field (str, optional): name of the field of protein sequence in lmdb files
            target_fields (list of str, optional): name of target fields in lmdb files
            number_field (str, optional): name of the field of sample count in lmdb files
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the proteins are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        if target_fields is not None:
            target_fields = set(target_fields)
    
        sequences = []
        num_samples = []
        targets = defaultdict(list)
        for lmdb_file in lmdb_files:
            env = lmdb.open(lmdb_file, readonly=True, lock=False, readahead=False, meminit=False)
            with env.begin(write=False) as txn:
                num_sample = pickle.loads(txn.get(number_field.encode()))
                for i in range(num_sample):
                    item = pickle.loads(txn.get(str(i).encode()))
                    sequences.append(item[sequence_field])
                    if target_fields:
                        for field in target_fields:
                            value = item[field]
                            if isinstance(value, np.ndarray) and value.size == 1:
                                value = value.item()
                            targets[field].append(value)
                num_samples.append(num_sample)
    
        self.load_sequence(sequences, targets, attributes=None, transform=transform,
                           lazy=lazy, verbose=verbose, **kwargs)
        self.num_samples = num_samples

    @utils.copy_args(data.Protein.from_molecule)
    def load_pdbs(self, pdb_files, transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from pdb files.

        Parameters:
            pdb_files (list of str): pdb file names
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the proteins are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        num_sample = len(pdb_files)
        if num_sample > 1000000:
            warnings.warn("Preprocessing proteins of a large dataset consumes a lot of CPU memory and time. "
                          "Use load_pdbs(lazy=True) to construct molecules in the dataloader instead.")

        self.transform = transform
        self.lazy = lazy
        self.kwargs = kwargs
        self.data = []
        self.pdb_files = []
        self.sequences = []

        if verbose:
            pdb_files = tqdm(pdb_files, "Constructing proteins from pdbs")
        for i, pdb_file in enumerate(pdb_files):
            if not lazy or i == 0:
                mol = Chem.MolFromPDBFile(pdb_file)
                if not mol:
                    logger.debug("Can't construct molecule from pdb file `%s`. Ignore this sample." % pdb_file)
                    continue
                protein = data.Protein.from_molecule(mol, **kwargs)
                if not protein:
                    logger.debug("Can't construct protein from pdb file `%s`. Ignore this sample." % pdb_file)
                    continue
            else:
                protein = None
            if hasattr(protein, "residue_feature"):
                with protein.residue():
                    protein.residue_feature = protein.residue_feature.to_sparse()
            self.data.append(protein)
            self.pdb_files.append(pdb_file)
            self.sequences.append(protein.to_sequence() if protein else None)

    @utils.copy_args(load_sequence)
    def load_fasta(self, fasta_file, verbose=0, **kwargs):
        """
        Load the dataset from a fasta file.

        Parameters:
            fasta_file (str): file name
            verbose (int, optional): output verbose level
            **kwargs
        """
        with open(fasta_file, "r") as fin:
            if verbose:
                fin = tqdm(fin, "Loading %s" % fasta_file, utils.get_line_count(fasta_file))
            sequences = []
            lines = []
            for line in fin:
                line = line.strip()
                if line.startswith(">") and lines:
                    sequence = "".join(lines)
                    sequences.append(sequence)
                    lines = []
                else:
                    lines.append(line)
            if lines:
                sequence = "".join(lines)
                sequences.append(sequence)

        return self.load_sequence(sequences, verbose=verbose, **kwargs)

    @utils.copy_args(data.Protein.from_molecule)
    def load_pickle(self, pkl_file, transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from a pickle file.

        Parameters:
            pkl_file (str): file name
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the proteins are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        with utils.smart_open(pkl_file, "rb") as fin:
            num_sample = pickle.load(fin)

            self.transform = transform
            self.lazy = lazy
            self.kwargs = kwargs
            self.sequences = []
            self.pdb_files = []
            self.data = []
            indexes = range(num_sample)
            if verbose:
                indexes = tqdm(indexes, "Loading %s" % pkl_file)
            for i in indexes:
                pdb_file, sequence, protein = pickle.load(fin)
                self.sequences.append(sequence)
                self.pdb_files.append(pdb_file)
                self.data.append(protein)

    def save_pickle(self, pkl_file, verbose=0):
        with utils.smart_open(pkl_file, "wb") as fout:
            num_sample = len(self.data)
            pickle.dump(num_sample, fout)

            indexes = range(num_sample)
            if verbose:
                indexes = tqdm(indexes, "Dumping to %s" % pkl_file)
            for i in indexes:
                pdb_dir, pdb_name = os.path.split(self.pdb_files[i])
                split = os.path.basename(pdb_dir)
                pdb_file = os.path.join(split, pdb_name)
                pickle.dump((pdb_file, self.sequences[i], self.data[i]), fout)

    @property
    def residue_feature_dim(self):
        """Dimension of residue features."""
        return self.data[0].residue_feature.shape[-1]


class ProteinPairDataset(ProteinDataset, core.Configurable):
    """
    Protein pair dataset.

    Each sample contains two protein graphs, and any number of prediction targets.
    """

    @utils.copy_args(data.Protein.from_sequence)
    def load_sequence(self, sequences, targets, attributes=None, transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from protein sequences and targets.

        Parameters:
            sequences (list of list of str): protein sequence string pairs
            targets (dict of list): prediction targets
            attributes (dict of list): protein-level attributes
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the protein pairs are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        num_sample = len(sequences)
        if num_sample > 1000000:
            warnings.warn("Preprocessing proteins of a large dataset consumes a lot of CPU memory and time. "
                          "Use load_sequence(lazy=True) to construct molecules in the dataloader instead.")
        for field, target_list in targets.items():
            if len(target_list) != num_sample:
                raise ValueError("Number of target `%s` doesn't match with number of molecules. "
                                 "Expect %d but found %d" % (field, num_sample, len(target_list)))

        self.transform = transform
        self.lazy = lazy
        self.kwargs = kwargs
        self.sequences = []
        self.data = []
        self.targets = defaultdict(list)

        if verbose:
            sequences = tqdm(sequences, "Constructing proteins from sequences")
        for i, sequence in enumerate(sequences):
            if not self.lazy or len(self.data) == 0:
                proteins = [data.Protein.from_sequence(seq, **kwargs) for seq in sequence]
            else:
                proteins = None
            if attributes is not None:
                for protein in proteins:
                    with protein.graph():
                        for field in attributes:
                            setattr(protein, field, attributes[field][i])
            self.data.append(proteins)
            self.sequences.append(sequence)
            for field in targets:
                self.targets[field].append(targets[field][i])

    @utils.copy_args(load_sequence)
    def load_lmdbs(self, lmdb_files, sequence_field="primary", target_fields=None, number_field="num_examples",
                   transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from lmdb files.

        Parameters:
            lmdb_files (list of str): file names
            sequence_field (str or list of str, optional): names of the fields of protein sequence in lmdb files
            target_fields (list of str, optional): name of target fields in lmdb files
            number_field (str, optional): name of the field of sample count in lmdb files
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the protein pairs are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        if target_fields is not None:
            target_fields = set(target_fields)
        else:
            target_fields = set()
        if not isinstance(sequence_field, Sequence):
            sequence_field = [sequence_field]
    
        sequences = []
        num_samples = []
        targets = defaultdict(list)
        for lmdb_file in lmdb_files:
            env = lmdb.open(lmdb_file, readonly=True, lock=False, readahead=False, meminit=False)
            with env.begin(write=False) as txn:
                num_sample = pickle.loads(txn.get(number_field.encode()))
                for i in range(num_sample):
                    item = pickle.loads(txn.get(str(i).encode()))
                    sequences.append([item[field] for field in sequence_field])
                    for field in target_fields:
                        value = item[field]
                        if isinstance(value, np.ndarray) and value.size == 1:
                            value = value.item()
                        targets[field].append(value)
                num_samples.append(num_sample)
    
        self.load_sequence(sequences, targets, transform=transform, lazy=lazy, verbose=verbose, **kwargs)
        self.num_samples = num_samples
    
    @property
    def node_feature_dim(self):
        """Dimension of node features."""
        return self.data[0][0].node_feature.shape[-1]

    @property
    def residue_feature_dim(self):
        """Dimension of residue features."""
        return self.data[0][0].residue_feature.shape[-1]


class ProteinLigandDataset(ProteinDataset, core.Configurable):
    """
    Protein-ligand dataset.

    Each sample contains a protein graph and a molecule graph, and any number of prediction targets.
    """

    @utils.copy_args(data.Protein.from_sequence)
    def load_sequence(self, sequences, smiles, targets, num_samples, attributes=None, transform=None,
                      lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from protein sequences, ligand SMILES strings and targets.

        Parameters:
            sequences (list of str): protein sequence strings
            smiles (list of str): ligand SMILES strings
            targets (dict of list): prediction targets
            num_samples (list of int): numbers of protein-ligand pairs in all splits
            attributes (dict of list): protein-level attributes
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the protein-ligand pairs are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        num_sample = len(sequences)
        if num_sample > 1000000:
            warnings.warn("Preprocessing proteins of a large dataset consumes a lot of CPU memory and time. "
                          "Use load_sequence(lazy=True) to construct molecules in the dataloader instead.")
        if len(smiles) != len(sequences):
            raise ValueError("Number of smiles doesn't match with number of proteins. "
                                 "Expect %d but found %d" % (num_sample, len(smiles)))
        for field, target_list in targets.items():
            if len(target_list) != num_sample:
                raise ValueError("Number of target `%s` doesn't match with number of molecules. "
                                 "Expect %d but found %d" % (field, num_sample, len(target_list)))

        self.transform = transform
        self.lazy = lazy
        self.kwargs = kwargs
        self.sequences = []
        self.smiles = []
        self.data = []
        self.targets = defaultdict(list)
        cum_num_samples = [num_samples[0]]
        for num in num_samples[1:]:
            cum_num_samples.append(cum_num_samples[-1] + num)
        _cur_split = 0

        if verbose:
            sequences = tqdm(sequences, "Constructing proteins from sequences")
        for i, (sequence, smile) in enumerate(zip(sequences, smiles)):
            if i >= cum_num_samples[_cur_split]:
                _cur_split += 1
            if not self.lazy or len(self.data) == 0:
                protein = data.Protein.from_sequence(sequence, **kwargs)
                mol = Chem.MolFromSmiles(smile)
                if not mol:
                    logger.debug("Can't construct molecule from SMILES `%s`. Ignore this sample." % smile)
                    num_samples[_cur_split] -= 1
                    continue
                mol = data.Molecule.from_molecule(mol)
            else:
                protein = None
                mol = None
            if attributes is not None:
                with protein.graph():
                    for field in attributes:
                        setattr(protein, field, attributes[field][i])
            self.data.append([protein, mol])
            self.sequences.append(sequence)
            self.smiles.append(smile)
            for field in targets:
                self.targets[field].append(targets[field][i])

        return num_samples

    @utils.copy_args(load_sequence)
    def load_lmdbs(self, lmdb_files, sequence_field="target", smiles_field="drug", target_fields=None,
                   number_field="num_examples", transform=None, lazy=False, verbose=0, **kwargs):
        """
        Load the dataset from lmdb files.

        Parameters:
            lmdb_files (list of str): file names
            sequence_field (str, optional): name of the field of protein sequence in lmdb files
            smiles_field (str, optional): name of the field of ligand SMILES string in lmdb files
            target_fields (list of str, optional): name of target fields in lmdb files
            number_field (str, optional): name of the field of sample count in lmdb files
            transform (Callable, optional): protein sequence transformation function
            lazy (bool, optional): if lazy mode is used, the protein-ligand pairs are processed in the dataloader.
                This may slow down the data loading process, but save a lot of CPU memory and dataset loading time.
            verbose (int, optional): output verbose level
            **kwargs
        """
        if target_fields is not None:
            target_fields = set(target_fields)
    
        sequences = []
        smiles = []
        num_samples = []
        targets = defaultdict(list)
        for lmdb_file in lmdb_files:
            env = lmdb.open(lmdb_file, readonly=True, lock=False, readahead=False, meminit=False)
            with env.begin(write=False) as txn:
                num_sample = pickle.loads(txn.get(number_field.encode()))
                for i in range(num_sample):
                    item = pickle.loads(txn.get(str(i).encode()))
                    sequences.append(item[sequence_field])
                    smiles.append(item[smiles_field])
                    if target_fields:
                        for field in target_fields:
                            value = item[field]
                            if isinstance(value, np.ndarray) and value.size == 1:
                                value = value.item()
                            targets[field].append(value)
                num_samples.append(num_sample)
    
        num_samples = self.load_sequence(sequences, smiles, targets, num_samples, transform=transform,
                                         lazy=lazy, verbose=verbose, **kwargs)
        self.num_samples = num_samples
    
    @property
    def ligand_node_feature_dim(self):
        """Dimension of node features for ligands."""
        return self.data[0][1].node_feature.shape[-1]

    @property
    def protein_node_feature_dim(self):
        """Dimension of node features for proteins."""
        return self.data[0][0].node_feature.shape[-1]

    @property
    def residue_feature_dim(self):
        """Dimension of residue features for proteins."""
        return self.data[0][0].residue_feature.shape[-1]


class SemiSupervised(torch_data.Dataset, core.Configurable):
    """
    Semi-supervised dataset.

    Parameters:
        dataset (Dataset): supervised dataset
        indices (list of int): sample indices to keep supervision
    """

    def __init__(self, dataset, indices):
        self.dataset = dataset
        self.indices = set(indices)

    def __getitem__(self, idx):
        item = self.dataset[idx]
        item["labeled"] = (idx in self.indices)
        return item

    def __len__(self):
        return len(self.dataset)


def semisupervised(dataset, length):
    """
    Randomly construct a semi-supervised dataset based on the given length.

    Parameters:
        dataset (Dataset): supervised dataset
        length (int): length of supervised data to keep
    """
    if length > len(dataset):
        raise ValueError("Length of labeled data exceeds the length of the dataset")

    indexes = torch.randperm(length)[:length].tolist()
    return SemiSupervised(dataset, indexes)


def key_split(dataset, keys, lengths=None, key_lengths=None):

    def round_to_boundary(i):
        for j in range(min(i, len(dataset) - i)):
            if keys[indexes[i - j]] != keys[indexes[i - j - 1]]:
                return i - j
            if keys[indexes[i + j]] != keys[indexes[i + j - 1]]:
                return i + j
        if i < len(dataset) - i:
            return 0
        else:
            return len(dataset)

    keys = torch.as_tensor(keys)
    key_set, keys = torch.unique(keys, return_inverse=True)
    perm = torch.randperm(len(key_set))
    keys = perm[keys]
    indexes = keys.argsort().tolist()

    if key_lengths is not None:
        assert lengths is None
        key2count = keys.bincount()
        key_offset = 0
        lengths = []
        for key_length in key_lengths:
            lengths.append(key2count[key_offset: key_offset + key_length].sum().item())
            key_offset += key_length

    offset = 0
    offsets = [offset]
    for length in lengths:
        offset = round_to_boundary(offset + length)
        offsets.append(offset)
    offsets[-1] = len(dataset)
    return [torch_data.Subset(dataset, indexes[offsets[i]: offsets[i + 1]]) for i in range(len(lengths))]


def scaffold_split(dataset, lengths):
    """
    Randomly split a dataset into new datasets with non-overlapping scaffolds.

    Parameters:
        dataset (Dataset): dataset to split
        lengths (list of int): expected length for each split.
            Note the results may be different in length due to rounding.
    """

    scaffold2id = {}
    keys = []
    for sample in dataset:
        scaffold = sample["graph"].to_scaffold()
        if scaffold not in scaffold2id:
            id = len(scaffold2id)
            scaffold2id[scaffold] = id
        else:
            id = scaffold2id[scaffold]
        keys.append(id)

    return key_split(dataset, keys, lengths)


def ordered_scaffold_split(dataset, lengths, chirality=True):
    """
    Split a dataset into new datasets with non-overlapping scaffolds and sorted w.r.t. number of each scaffold.

    Parameters:
        dataset (Dataset): dataset to split
        lengths (list of int): expected length for each split.
            Note the results may be different in length due to rounding.
    """
    frac_train, frac_valid, frac_test = 0.8, 0.1, 0.1

    scaffold2id = defaultdict(list)
    for idx, smiles in enumerate(dataset.smiles_list):
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(smiles=smiles, includeChirality=chirality)
        scaffold2id[scaffold].append(idx)

    scaffold2id = {key: sorted(value) for key, value in scaffold2id.items()}
    scaffold_sets = [
        scaffold_set for (scaffold, scaffold_set) in sorted(
            scaffold2id.items(), key=lambda x: (len(x[1]), x[1][0]), reverse=True)
    ]
    train_cutoff = frac_train * len(dataset)
    valid_cutoff = (frac_train + frac_valid) * len(dataset)
    train_idx, valid_idx, test_idx = [], [], []
    for scaffold_set in scaffold_sets:
        if len(train_idx) + len(scaffold_set) > train_cutoff:
            if len(train_idx) + len(valid_idx) + len(scaffold_set) > valid_cutoff:
                test_idx.extend(scaffold_set)
            else:
                valid_idx.extend(scaffold_set)
        else:
            train_idx.extend(scaffold_set)

    return torch_data.Subset(dataset, train_idx), torch_data.Subset(dataset, valid_idx), torch_data.Subset(dataset, test_idx)
