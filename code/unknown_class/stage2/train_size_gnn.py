import argparse
import datetime
import os
import sys
import yaml
import torch
import gc
os.environ['CUDA_VISIBLE_DEVICES']='5'
from koila import lazy
from src import const
from src.linker_size_lightning import SizeClassifier, SizeRegressor, SizeOrdinalClassifier
from src.utils import disable_rdkit_logging, Logger
from pytorch_lightning import Trainer, callbacks, loggers
from pdb import set_trace

torch.cuda.empty_cache()
gc.collect()
input = lazy(input, batch=0)

def main(args):
    disable_rdkit_logging()
    start_time = datetime.datetime.now().strftime('date%d-%m_time%H-%M-%S.%f')
    run_name = f'{args.experiment}'
    checkpoints_dir = os.path.join(args.checkpoints, run_name)
    print(checkpoints_dir)
    general_logs_dir = os.path.join(args.logs, "general_logs", run_name)

    os.makedirs(checkpoints_dir, exist_ok=True)
    os.makedirs(general_logs_dir, exist_ok=True)

    sys.stdout = Logger(logpath=os.path.join(args.logs, "general_logs", run_name, f'log.log'), syspart=sys.stdout)
    sys.stderr = Logger(logpath=os.path.join(args.logs, "general_logs", run_name, f'log.log'), syspart=sys.stderr)

    is_geom = 'geom' in args.train_data_prefix
    loss_weights = None

    if is_geom:
        in_node_nf = const.GEOM_NUMBER_OF_ATOM_TYPES
        out_node_nf = len(const.GEOM_TRAIN_LINKER_ID2SIZE)
        linker_size2id = const.GEOM_TRAIN_LINKER_SIZE2ID
        linker_id2size = const.GEOM_TRAIN_LINKER_ID2SIZE
        if args.loss_weights:
            loss_weights = const.GEOM_TRAIN_LINKER_SIZE_WEIGHTS
    else:
        in_node_nf = const.NUMBER_OF_ATOM_TYPES
        out_node_nf = len(const.ZINC_TRAIN_LINKER_ID2SIZE)
        linker_size2id = const.ZINC_TRAIN_LINKER_SIZE2ID
        linker_id2size = const.ZINC_TRAIN_LINKER_ID2SIZE
        if args.loss_weights:
            loss_weights = const.ZINC_TRAIN_LINKER_SIZE_WEIGHTS

    torch_device = 'cuda:0'
    wandb_logger = loggers.WandbLogger(
        save_dir=args.logs,
        project='linker_size_classifier',
        name=run_name,
        id=run_name,
        resume='allow',
        entity=args.wandb_entity,
        mode=args.wandb_mode
    )
    
    checkpoint_callback = callbacks.ModelCheckpoint(
        dirpath=checkpoints_dir,
        filename=run_name + 'best_{epoch:02d}',
        save_last=True,
        monitor='loss/val',
        save_top_k=-1,
    )

    if args.task == 'classification':
        model = SizeClassifier(
            data_path=args.data,
            train_data_prefix=args.train_data_prefix,
            val_data_prefix=args.val_data_prefix,
            in_node_nf=in_node_nf,
            hidden_nf=args.hidden_nf,  # 128
            out_node_nf=out_node_nf,
            n_layers=args.n_layers,
            batch_size=args.batch_size,  # 64
            lr=1e-4,  # 1e-3 args.lr
            normalization=args.normalization,
            torch_device=torch_device,
            loss_weights=loss_weights,
            linker_size2id=linker_size2id,
            linker_id2size=linker_id2size,
        )
    elif args.task == 'ordinal':
        model = SizeOrdinalClassifier(
            data_path=args.data,
            train_data_prefix=args.train_data_prefix,
            val_data_prefix=args.val_data_prefix,
            in_node_nf=in_node_nf,
            hidden_nf=args.hidden_nf,  # 128
            out_node_nf=out_node_nf,
            n_layers=args.n_layers,
            batch_size=args.batch_size,  # 64
            lr=args.lr,  # 1e-3
            normalization=args.normalization,
            torch_device=torch_device,
            linker_size2id=linker_size2id,
            linker_id2size=linker_id2size,
        )
    elif args.task == 'regression':
        model = SizeRegressor(
            data_path=args.data,
            train_data_prefix=args.train_data_prefix,
            val_data_prefix=args.val_data_prefix,
            in_node_nf=in_node_nf,
            hidden_nf=args.hidden_nf,  # 128
            n_layers=args.n_layers,
            batch_size=args.batch_size,  # 64
            lr=args.lr,  # 1e-3
            normalization=args.normalization,
            torch_device=torch_device,
        )
    else:
        raise NotImplementedError
    
    trainer = Trainer(
        max_epochs=1000,  # 1000
        logger=wandb_logger,
        callbacks=[checkpoint_callback],
        accelerator='gpu',
        devices=1,
        num_sanity_val_steps=0,
        enable_progress_bar=True,
    )
    
    trainer.fit(model=model)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SOSI')
    parser.add_argument('--config', type=argparse.FileType(mode='r'), default='configs/uspto_size.yml')
    parser.add_argument('--experiment', type=str, default='uspto_size_gnn')  # , required=True
    parser.add_argument('--data', action='store', type=str)   #, required=True
    parser.add_argument('--train_data_prefix', action='store', type=str) # , required=True
    parser.add_argument('--val_data_prefix', action='store', type=str)  # , required=True
    parser.add_argument('--hidden_nf', action='store', type=int, required=False, default=128)
    parser.add_argument('--n_layers', action='store', type=int, required=False, default=3)
    parser.add_argument('--normalization', action='store', type=str, required=False, default=None)
    parser.add_argument('--batch_size', action='store', type=int, required=False, default=64)
    parser.add_argument('--lr', action='store', type=float, required=False, default=1e-3)
    parser.add_argument('--task', action='store', type=str, required=False, default='classification')
    parser.add_argument('--loss_weights', action='store_true', default=False)
    parser.add_argument('--device', action='store', type=str, default='cuda:0')  # , required=True
    parser.add_argument('--checkpoints', type=str, default='code/unknown_class/stage2/2nd_stage_train/models/uspto_size_gnn')
    parser.add_argument('--logs', action='store', type=str)  #, required=True
    parser.add_argument('--wandb_entity', type=str, default=None, help='Entity (project) name')
    parser.add_argument('--wandb_mode', type=str, default='offline')

    args = parser.parse_args()
    
    if args.config:
        config_dict = yaml.load(args.config, Loader=yaml.FullLoader)
        arg_dict = args.__dict__
        for key, value in config_dict.items():
            if isinstance(value, list) and key != 'normalize_factors':
                for v in value:
                    arg_dict[key].append(v)
            else:
                arg_dict[key] = value
        args.config = args.config.name
    else:
        config_dict = {}
    main(args)
