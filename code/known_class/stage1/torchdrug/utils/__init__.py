import sys
sys.path.append('/home/anaconda3/lib/python3.7/site-packages')
from .io import input_choice, literal_eval, no_rdkit_log, capture_rdkit_log
from .file import download, smart_open, extract, compute_md5, get_line_count
from .torch import load_extension, cpu, cuda, detach, clone, mean, cat, stack, sparse_coo_tensor
#from .decorator import cached_property, cached, deprecated_alias
#from . import pretty, comm, doc, plot

from .decorator import copy_args, cached_property, cached, deprecated_alias
from . import pretty, comm, plot

__all__ = [
    "input_choice", "literal_eval", "no_rdkit_log", "capture_rdkit_log",
    "download", "smart_open", "extract", "compute_md5", "get_line_count",
    "load_extension", "cpu", "cuda", "detach", "clone", "mean", "cat", "stack", "sparse_coo_tensor",
    #"cached_property", "cached", "deprecated_alias",
    #"pretty", "comm", "doc", "plot",
    
    "copy_args", "cached_property", "cached", "deprecated_alias",
    "pretty", "comm", "plot"
]