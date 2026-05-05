"""
Embedding 模型微调脚本

使用 sentence-transformers 对 BAAI/bge-large-zh-v1.5 进行金融领域微调。
支持两种训练模式：
1. 对比学习（Contrastive Learning）— 使用正例/负例对
2. 三元组损失（Triplet Loss）— 使用 anchor/positive/negative

环境要求：
  pip install sentence-transformers torch

用法：
  # 对比学习模式
  python scripts/finetune_embedding.py --mode contrastive --epochs 3

  # 三元组模式
  python scripts/finetune_embedding.py --mode triplet --epochs 3

  # LoRA 微调（需要 peft 库）
  python scripts/finetune_embedding.py --mode contrastive --lora --epochs 5
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "finetune"
OUTPUT_DIR = ROOT / "models" / "embedding_finetuned"


def load_pairs(path: str):
    """加载正例/负例对数据"""
    from sentence_transformers import InputExample
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line.strip())
            examples.append(InputExample(
                texts=[item["query"], item["positive"]],
                label=float(item.get("label", 1.0)),
            ))
    return examples


def load_triplets(path: str):
    """加载三元组数据"""
    from sentence_transformers import InputExample
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line.strip())
            examples.append(InputExample(
                texts=[item["anchor"], item["positive"], item["negative"]],
            ))
    return examples


def train_contrastive(model_name: str, epochs: int, batch_size: int, use_lora: bool):
    """对比学习模式训练"""
    from sentence_transformers import SentenceTransformer, losses
    from torch.utils.data import DataLoader

    pairs_path = DATA_DIR / "pairs.jsonl"
    if not pairs_path.exists():
        print(f"[ERROR] 训练数据不存在: {pairs_path}")
        print("请先运行: python scripts/build_finetune_dataset.py")
        sys.exit(1)

    print(f"[INFO] 加载基座模型: {model_name}")
    model = SentenceTransformer(model_name)

    if use_lora:
        print("[INFO] 应用 LoRA 适配器...")
        try:
            from peft import LoraConfig, get_peft_model, TaskType
            lora_config = LoraConfig(
                task_type=TaskType.FEATURE_EXTRACTION,
                r=8,
                lora_alpha=16,
                lora_dropout=0.1,
                target_modules=["query", "value"],
            )
            model._first_module().auto_model = get_peft_model(
                model._first_module().auto_model, lora_config
            )
            print("[INFO] LoRA 已应用（r=8, alpha=16）")
        except ImportError:
            print("[WARN] peft 未安装，回退为全参数微调")
            print("  安装: pip install peft")

    examples = load_pairs(str(pairs_path))
    print(f"[INFO] 加载 {len(examples)} 个训练样本")

    train_dataloader = DataLoader(examples, shuffle=True, batch_size=batch_size)
    train_loss = losses.CosineSimilarityLoss(model)

    print(f"[INFO] 开始训练: epochs={epochs}, batch_size={batch_size}")
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=epochs,
        warmup_steps=int(len(train_dataloader) * 0.1),
        output_path=str(OUTPUT_DIR),
        show_progress_bar=True,
    )
    print(f"[DONE] 模型已保存到: {OUTPUT_DIR}")


def train_triplet(model_name: str, epochs: int, batch_size: int, use_lora: bool):
    """三元组损失模式训练"""
    from sentence_transformers import SentenceTransformer, losses
    from torch.utils.data import DataLoader

    triplets_path = DATA_DIR / "triplets.jsonl"
    if not triplets_path.exists():
        print(f"[ERROR] 训练数据不存在: {triplets_path}")
        print("请先运行: python scripts/build_finetune_dataset.py")
        sys.exit(1)

    print(f"[INFO] 加载基座模型: {model_name}")
    model = SentenceTransformer(model_name)

    examples = load_triplets(str(triplets_path))
    print(f"[INFO] 加载 {len(examples)} 个三元组样本")

    train_dataloader = DataLoader(examples, shuffle=True, batch_size=batch_size)
    train_loss = losses.TripletLoss(model, distance_metric=losses.TripletDistanceMetric.COSINE, triplet_margin=0.3)

    print(f"[INFO] 开始训练: epochs={epochs}, batch_size={batch_size}")
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=epochs,
        warmup_steps=int(len(train_dataloader) * 0.1),
        output_path=str(OUTPUT_DIR),
        show_progress_bar=True,
    )
    print(f"[DONE] 模型已保存到: {OUTPUT_DIR}")


def evaluate_model(model_path: str):
    """评估微调前后的检索效果"""
    from sentence_transformers import SentenceTransformer
    import numpy as np

    pairs_path = DATA_DIR / "pairs.jsonl"
    if not pairs_path.exists():
        print("[ERROR] 没有评估数据")
        return

    # 加载正例数据
    queries, passages, labels = [], [], []
    with open(pairs_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line.strip())
            queries.append(item["query"])
            passages.append(item["positive"])
            labels.append(float(item.get("label", 1.0)))

    print("\n========== 检索效果评估 ==========\n")

    for name, path in [("原始模型 (bge-large-zh)", "BAAI/bge-large-zh-v1.5"), ("微调模型", model_path)]:
        try:
            model = SentenceTransformer(path)
        except Exception as e:
            print(f"  [{name}] 加载失败: {e}")
            continue

        q_embs = model.encode(queries, normalize_embeddings=True)
        p_embs = model.encode(passages, normalize_embeddings=True)

        # 计算余弦相似度
        sims = np.sum(q_embs * p_embs, axis=1)
        pos_sims = [s for s, l in zip(sims, labels) if l > 0.5]
        neg_sims = [s for s, l in zip(sims, labels) if l <= 0.5]

        print(f"  [{name}]")
        print(f"    正例平均相似度: {np.mean(pos_sims):.4f}")
        if neg_sims:
            print(f"    负例平均相似度: {np.mean(neg_sims):.4f}")
            print(f"    正-负差距:     {np.mean(pos_sims) - np.mean(neg_sims):.4f}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Embedding 模型微调")
    parser.add_argument("--mode", choices=["contrastive", "triplet", "evaluate"], default="contrastive",
                        help="训练模式: contrastive(对比学习) / triplet(三元组) / evaluate(仅评估)")
    parser.add_argument("--model", default="BAAI/bge-large-zh-v1.5", help="基座模型名称")
    parser.add_argument("--epochs", type=int, default=3, help="训练轮数")
    parser.add_argument("--batch-size", type=int, default=8, help="批大小")
    parser.add_argument("--lora", action="store_true", help="使用 LoRA 微调")
    args = parser.parse_args()

    if args.mode == "evaluate":
        evaluate_model(str(OUTPUT_DIR))
    elif args.mode == "contrastive":
        train_contrastive(args.model, args.epochs, args.batch_size, args.lora)
        evaluate_model(str(OUTPUT_DIR))
    elif args.mode == "triplet":
        train_triplet(args.model, args.epochs, args.batch_size, args.lora)
        evaluate_model(str(OUTPUT_DIR))


if __name__ == "__main__":
    main()
