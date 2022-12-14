import traceback
from typing import Tuple, List
from flask import current_app
from flaskr import file_utils
from flaskr.evaluation_result import EvaluationResult

import torch
from torch.utils.data import Dataset
from torch.utils.data import DataLoader

from Data.dataset import SeqDataset, TreeDataset
from Data.utils import get_seq_batch_data, get_tree_batch_data
from Model.model import AbsoluteTransformer, RelativeTransformer, BiLSTM, TreeLSTM
from Utils.const import TYPE_NUM
from Utils.metric import get_metric

MAX_DECODE = 500
TRAIN_SEQ_DATASET_KEY = "train_seq_dataset"
TRAIN_TREE_DATASET_KEY = "train_tree_dataset"
TRAIN_TABLE_FILE_PATH = "Dataset/spider/tables.json"
SUPPORTED_MODELS = ["BiLSTM", "Relative-Transformer",
                    "Transformer", "TreeLSTM"]
TRAIN_DATA_FILES = [
    "./Dataset/spider_composed_tree2seq/train.json",
]

device = torch.device("cuda:0")
train_seq_dataset: Dataset = None
train_tree_dataset: Dataset = None
checkpoint_dict: dict = {}


def predict(model:str, db_id:str, gold_nl_array:str, input_sql:str, input_identifier:str) -> EvaluationResult:
    result = EvaluationResult()
    result.modelName = model
    result.original = input_sql
    # score is considered only gold_nl is passed in
    result.hasScore = gold_nl_array != None and gold_nl_array != ""

    if model not in SUPPORTED_MODELS:
        error_msg = f"model '{model}' is not in supported models {SUPPORTED_MODELS}!"
        current_app.logger.error(error_msg)
        result.failedReason = error_msg
        return result

    # current_app.logger.info(f"you are on bridge, torch version:{torch.__version__}")
    input_identifier = f"{input_identifier}@{model}"
    jsonTargetPath = file_utils.build_input_sql_json(
        db_id, gold_nl_array, input_sql, input_identifier)
    if jsonTargetPath == "":
        error_msg = f"build json file for model '{model}' input_sql '{input_sql}' failed!"
        current_app.logger.error(error_msg)
        result.failedReason = error_msg
        return result

    run_sql2text(model, jsonTargetPath, result)
    return result


def is_ready():
    return len(checkpoint_dict) != 0 and train_seq_dataset != None and train_tree_dataset != None


def setup_checkpoints(_):
    global checkpoint_dict
    
    for model in SUPPORTED_MODELS:
        path = get_checkpoint_path(model)
        current_app.logger.info(f"loading checkpoint {path} for model '{model}'...")
        # print(f"[setup_checkpoints] loading checkpoint {path} for model '{model}'...")
        checkpoint_dict[model] = torch.load(path)
        # print(f"[setup_checkpoints] checkpoint {path} loaded!")
        current_app.logger.info(f"checkpoint {path} loaded!")

    # print("!!setup_checkpoints finished!!")
    current_app.logger.info("!!setup_checkpoints finished!!")


def setup_models(_):
    # no app_context in this method, current_app not avialable
    global train_seq_dataset
    global train_tree_dataset
    if train_seq_dataset is None:
        # print(f"[setup_models] generating {TRAIN_SEQ_DATASET_KEY}...")
        current_app.logger.info(f"generating {TRAIN_SEQ_DATASET_KEY}...")
        train_seq_dataset = SeqDataset(
            TRAIN_DATA_FILES, TRAIN_TABLE_FILE_PATH)
        # print(f"[setup_models] {TRAIN_SEQ_DATASET_KEY} generated!")
        current_app.logger.info(f"{TRAIN_SEQ_DATASET_KEY} generated!")
    if train_tree_dataset is None:
        # print(f"[setup_models] generating {TRAIN_TREE_DATASET_KEY}...")
        current_app.logger.info(f"generating {TRAIN_TREE_DATASET_KEY}...")
        train_tree_dataset = TreeDataset(
            TRAIN_DATA_FILES, TRAIN_TABLE_FILE_PATH)
        # print(f"[setup_models] {TRAIN_TREE_DATASET_KEY} generated!")
        current_app.logger.info(f"{TRAIN_TREE_DATASET_KEY} generated!")

    # print("!!setup_models finished!!")
    current_app.logger.info("!!setup_models finished!!")


def get_checkpoint_path(model: str) -> str:
    """
    return empty string if model not supported
    """
    if model == 'BiLSTM':
        return 'Checkpoints/BiLSTM/spider/bilstm_big.pt'

    if model == 'Relative-Transformer':
        return 'Checkpoints/RelativeTransformer/spider/rel_transformer_big.pt'

    if model == 'Transformer':
        return 'Checkpoints/Transformer/spider/transformer_big.pt'

    if model == 'TreeLSTM':
        return 'Checkpoints/TreeLSTM/spider/tree2seq_big.pt'

    current_app.logger.error("not supported model %s", model)
    return ''


def get_train_dataset(model: str) -> Dataset:
    if model in ['Relative-Transformer', 'Transformer', 'BiLSTM']:
        if train_seq_dataset is None:
            current_app.logger.error("%s is not setup!", TRAIN_SEQ_DATASET_KEY)

        return train_seq_dataset
    elif model == 'TreeLSTM':
        if train_tree_dataset is None:
            current_app.logger.error("%s is not setup!", TRAIN_TREE_DATASET_KEY)

        return train_tree_dataset
    else:
        current_app.logger.error("not supported model %s", model)
        return None


def build_dataset(model: str, user_request_data_file: str) -> Dataset:
    test_data_files = [user_request_data_file]

    test_set = None
    if model in ['Relative-Transformer', 'Transformer', 'BiLSTM']:
        train_set = get_train_dataset(model)
        test_set = SeqDataset(
            test_data_files, TRAIN_TABLE_FILE_PATH, vocab=train_set.vocab)
    elif model == 'TreeLSTM':
        train_set = get_train_dataset(model)
        test_set = TreeDataset(
            test_data_files, TRAIN_TABLE_FILE_PATH, vocab=train_set.vocab)
    else:
        current_app.logger.error("not supported model %s", model)

    return test_set


def build_model(args, vocab) -> torch.nn.Module:
    if args is None or vocab is None:
        return None

    if args.model == "BiLSTM":
        return BiLSTM(args.down_embed_dim, vocab.size, args.hid_size,
                      vocab.pad_idx, args.dropout, args.max_oov_num,
                      args.copy)

    if args.model == "Relative-Transformer":
        return RelativeTransformer(args.down_embed_dim, vocab.size,
                                   args.down_d_model, args.down_d_ff,
                                   args.down_head_num, args.down_layer_num,
                                   args.hid_size, args.dropout, vocab.pad_idx,
                                   args.down_max_dist, args.max_oov_num,
                                   args.copy, args.rel_share, args.k_v_share)

    if args.model == "Transformer":
        return AbsoluteTransformer(args.down_embed_dim,
                                   vocab.size,
                                   args.down_d_model,
                                   args.down_d_ff,
                                   args.down_head_num,
                                   args.down_layer_num,
                                   args.hid_size,
                                   args.dropout,
                                   vocab.pad_idx,
                                   max_oov_num=args.max_oov_num,
                                   copy=args.copy,
                                   pos=args.absolute_pos)

    if args.model == "TreeLSTM":
        return TreeLSTM(args.down_embed_dim, vocab.size, TYPE_NUM,
                        args.hid_size, args.dropout, vocab.pad_idx,
                        args.max_oov_num, args.copy)

    current_app.logger.error("not supported model %s", args.model)
    return None


def evaluate(model: torch.nn.Module, dataset, vocab, args, model_name) -> Tuple[str, float]:
    if model_name not in SUPPORTED_MODELS:
        return "", 0

    model.eval()
    dataloader = DataLoader(dataset, args.eval_batch_size)
    all_predictions = []

    for batch_data in dataloader:
        preds = []
        if model_name in ["Relative-Transformer", "Transformer", "BiLSTM"]:
            batch, _ = get_seq_batch_data(
                batch_data, vocab.pad_idx, device, vocab.size, vocab.unk_idx, args.down_max_dist)
            nodes, questions, rela_dist, copy_mask, src2trg_map = batch
            if model_name == "Relative-Transformer":
                nodes, hidden, mask = model.encode(nodes, rela_dist)
            else:  # "Transformer", "BiLSTM"
                nodes, hidden, mask = model.encode(nodes)
        elif model_name == "TreeLSTM":
            batch, _ = get_tree_batch_data(batch_data, device)
            nodes, types, node_order, adjacency_list, edge_order, questions, copy_mask, src2trg_map = batch
            nodes, hidden, mask = model.encode(
                nodes, types, node_order, adjacency_list, edge_order)
        else:
            current_app.logger.error("not supported model %s", model_name)
            return "", 0

        inputs = questions[:, 0].view(-1, 1)
        for _ in range(MAX_DECODE):
            cur_out, hidden = model.decode(
                inputs, nodes, hidden, mask, copy_mask, src2trg_map)
            next_input = cur_out.argmax(dim=-1)
            preds.append(next_input)
            next_input[next_input >= vocab.size] = vocab.unk_idx
            inputs = next_input

        preds = torch.cat(preds, dim=1)
        all_predictions += preds.tolist()

    _, scores, result_predictions, _ = get_metric(all_predictions, dataset.origin_questions, vocab,
                                          True, dataset.val_map_list, dataset.idx2tok_map_list)

    prediction = result_predictions[0] if len(result_predictions) > 0 else ""
    score = scores[0] if len(scores) > 0 else 0
    return prediction, score


def run_sql2text(model: str, user_input_json_path: str, result:EvaluationResult):
    try:
        if model not in checkpoint_dict.keys():
            reason = f"checkpoint for model {model} is not loaded yet"
            current_app.logger.error(reason)
            result.failedReason = reason
            return

        checkpoint = checkpoint_dict[model]
        checkpoint_args = checkpoint['args']
        # build default args
        checkpoint_args.data = "spider"
        checkpoint_args.eval_batch_size = 1

        test_dataset = build_dataset(model, user_input_json_path)
        if test_dataset is None:
            reason = f"build dataset for {model} failed"
            current_app.logger.error(reason)
            result.failedReason = reason
            return

        model = build_model(checkpoint_args, test_dataset.vocab)
        if model is None:
            reason = f"build model for {model} failed"
            current_app.logger.error(reason)
            result.failedReason = reason
            return

        model.to(device)
        model.load_state_dict(checkpoint['model'])
        current_app.logger.info(
            "%s is ready, start evaluate...", checkpoint_args.model)
        prediction, score = evaluate(
            model, test_dataset, test_dataset.vocab, checkpoint_args, checkpoint_args.model)
        result.result = prediction
        result.score = score
        result.success = True

    except Exception as err:
        current_app.logger.error(err)
        current_app.logger.error(traceback.format_exc())
        result.failedReason = str(err)
        return

    return
