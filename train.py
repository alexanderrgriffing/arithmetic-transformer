import argparse
import torch
from torch.optim.lr_scheduler import CosineAnnealingWarmRestarts
from pytorch_lightning.loggers import WandbLogger
from pytorch_lightning.tuner import Tuner
import wandb
from collections import OrderedDict
import inspect
import torch.nn.functional as F
import pytorch_lightning as pl
import tqdm
from collections import Counter

from dataset import AdditionDataset
from model import AdditionModel



def main():
    # Needed to enable tensor cores
    torch.set_float32_matmul_precision("medium")

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=1000,
        help="Number of examples to generate and train on",
    )
    parser.add_argument("--lr", type=float, default=1e-3, help="Adam LR")
    parser.add_argument("--dropout", type=float, default=0)
    parser.add_argument("--noise-rank", type=int, default=10)
    parser.add_argument(
        "--hidden-size",
        type=int,
        default=32,
        help="The hidden size for the neural network",
    )
    parser.add_argument(
        "--num-layers",
        type=int,
        default=4,
        help="The number of layers for the neural network",
    )
    parser.add_argument("--batch-size", type=int, default=2**8, help="Batch size")
    parser.add_argument(
        "--num-examples", type=int, default=3, help="Number of examples to output"
    )
    parser.add_argument(
        "--kind",
        type=str,
        default="lstm",
        help="The type of neural network to use (lstm, transformer, hybrid)",
    )
    parser.add_argument("--tune-lr", action="store_true")
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--norm-last", action="store_true")
    parser.add_argument("--cosine", action="store_true")
    parser.add_argument("--grouped", action="store_true")
    parser.add_argument("--norm-kvs", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--num-heads", type=int, default=1, help="The number of heads/rank in transformer/mlp")
    parser.add_argument("--num-queries", type=int, default=1)
    parser.add_argument(
        "--tag",
        type=str,
        default="",
    )
    args = parser.parse_args()

    dataset = AdditionDataset(
        10**6, # data points per epoch
        base=10,
        number_length=1,
        sequence_length=2,
    )

    model = AdditionModel(
        ds=dataset,
        batch_size=2**10,
        kind=args.kind,
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        num_heads=args.num_heads,
        num_queries=args.num_queries,
        norm_first=not args.norm_last,
        is_cosine=args.cosine,
        grouped=args.grouped,
        norm_kvs=args.norm_kvs,
        lr=args.lr,
        dropout=args.dropout,
        noise_rank=args.noise_rank,
    )
    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f'The model has {num_params} parameters')

    manual_training(model, dataset, args)

def lightning_training(model, dataset, args):
    if args.wandb:
        logger = WandbLogger(
            name=f"{args.kind}_p={format_size(num_params)}_base={args.base}_seqlen={args.sequence_length}_layers={args.num_layers}_hidden={args.hidden_size}_heads={args.num_heads}_qs={args.num_queries}_lr={args.lr}_norm_first={not args.norm_last}_grouped={args.grouped}_cosine={args.cosine}_normkvs={args.norm_kvs}_dropout={args.dropout}_noise_rank={args.noise_rank}_tag={args.tag}",
            log_model=True,
        )
        wandb.init('add lstm')
        wandb.watch(model, log="all")
        trainer = pl.Trainer(max_epochs=args.epochs, logger=logger, reload_dataloaders_every_n_epochs=1)
    else:
        trainer = pl.Trainer(max_epochs=args.epochs, reload_dataloaders_every_n_epochs=1)

    trainer.fit(model)

def manual_training(model, dataset, args):
    if args.cpu:
        device = torch.device('cpu')
    else:
        if torch.cuda.is_available():
            device = torch.device('cuda')
        elif torch.backends.mps.is_available():
            device = torch.device('mps')
        else:
            device = torch.device('cpu')
    model = model.to(device)

    # Get optimizer (and potentially the scheduler)
    optimizers = model.configure_optimizers()
    if isinstance(optimizers, tuple) and len(optimizers) == 2:
        optimizer, scheduler = optimizers
    else:
        optimizer = optimizers

    # Standard PyTorch Training Loop
    time_to_success = Counter()
    for epoch in range(args.epochs):
        # Training Loop
        model.train()
        for batch_idx in tqdm.tqdm(range(10**3)):
            batch = dataset.generate_batch(args.batch_size).to(model.device)
            optimizer.zero_grad()
            loss = model.training_step(batch, batch_idx)
            loss.backward()
            optimizer.step()

        model.print_examples(3)

        # Validation Loop
        accs = []
        model.eval()
        with torch.no_grad():
            for batch_idx in tqdm.tqdm(range(10**2)):
                batch = dataset.generate_batch(args.batch_size).to(model.device)
                acc = model.validation_step(batch, batch_idx)
                accs.append(acc)

        time_to_success[dataset.number_length] += 1

        acc = torch.mean(torch.tensor(accs))
        print(f"Validation acc: {acc}")
        print(sorted(time_to_success.items()))
        if acc > .9:
            print(f"Switching to number length {dataset.number_length+1}")
            print(f"Took {time_to_success[dataset.number_length]} epochs")
            dataset.number_length += 1


if __name__ == "__main__":
    main()
