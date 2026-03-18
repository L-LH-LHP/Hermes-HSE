#!/usr/bin/env python3
"""
Enron 邮件合规审计 - 数据预处理脚本

从 Enron maildir 提取邮件，使用 NLP（nltk / TF-IDF）进行关键词提取，
输出 Hermes 索引构建所需的 database/ 与 database_paths/ 格式。
与 extract_database.go 输出格式兼容，可直接被 C++ server 的 init() 使用。

数据源: https://www.cs.cmu.edu/~enron/
"""

import os
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# 可选依赖
try:
    import nltk
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


# 与 extract_database.go 保持一致的停用词集合（英文）
DEFAULT_STOPWORDS = {
    "a", "about", "above", "across", "after", "afterwards", "again", "against",
    "all", "almost", "alone", "along", "already", "also", "although", "always",
    "am", "among", "amongst", "amount", "an", "and", "another", "any", "anyhow",
    "anyone", "anything", "anyway", "anywhere", "are", "around", "as", "at",
    "back", "be", "became", "because", "become", "becomes", "becoming", "been",
    "before", "behind", "being", "below", "beside", "besides", "between",
    "beyond", "both", "bottom", "but", "by", "call", "can", "cannot", "could",
    "did", "do", "does", "done", "down", "due", "during", "each", "eight",
    "either", "else", "elsewhere", "empty", "enough", "etc", "even", "ever",
    "every", "everyone", "everything", "everywhere", "except", "few", "first",
    "five", "for", "former", "formerly", "from", "front", "full", "further",
    "get", "give", "go", "had", "has", "have", "he", "her", "here", "hereafter",
    "hereby", "herein", "hereupon", "hers", "herself", "him", "himself", "his",
    "how", "however", "if", "in", "inc", "indeed", "into", "is", "it", "its",
    "itself", "keep", "last", "latter", "least", "less", "ltd", "made", "many",
    "may", "me", "meanwhile", "might", "more", "moreover", "most", "mostly",
    "much", "must", "my", "myself", "name", "namely", "neither", "never",
    "nevertheless", "next", "nine", "no", "nobody", "none", "noone", "nor",
    "not", "nothing", "now", "nowhere", "of", "off", "often", "on", "once",
    "one", "only", "onto", "or", "other", "others", "otherwise", "our", "ours",
    "ourselves", "out", "over", "own", "part", "per", "perhaps", "please",
    "put", "rather", "re", "same", "see", "seem", "seemed", "seeming", "seems",
    "several", "she", "should", "show", "side", "since", "sincere", "six", "so",
    "some", "somehow", "someone", "something", "sometime", "sometimes", "somewhere",
    "still", "such", "system", "take", "ten", "than", "that", "the", "their",
    "themselves", "then", "there", "thereafter", "thereby", "therefore",
    "therein", "thereupon", "these", "they", "third", "this", "those", "though",
    "three", "through", "throughout", "thru", "thus", "to", "together", "too",
    "toward", "towards", "two", "under", "until", "up", "upon", "us", "very",
    "via", "was", "we", "well", "were", "what", "whatever", "when", "whence",
    "whenever", "where", "whereafter", "whereas", "whereby", "wherein",
    "whereupon", "wherever", "whether", "which", "while", "who", "whoever",
    "whole", "whom", "whose", "why", "will", "with", "within", "without",
    "would", "yet", "you", "your", "yours", "yourself", "yourselves", "the",
}


def ensure_nltk_data():
    """下载 nltk 所需数据（仅首次运行）"""
    if not NLTK_AVAILABLE:
        return False
    for resource in ("punkt", "punkt_tab", "stopwords"):
        try:
            nltk.data.find(f"tokenizers/{resource}" if "punkt" in resource else f"corpora/{resource}")
        except LookupError:
            try:
                nltk.download(resource if resource == "stopwords" else "punkt", quiet=True)
            except Exception:
                pass
    return True


def tokenize_with_nltk(text: str) -> list:
    """使用 nltk 分词（若可用），否则简单按非字母分割"""
    text = text.lower()
    if NLTK_AVAILABLE:
        try:
            return nltk.word_tokenize(text)
        except Exception:
            pass
    return re.findall(r"[a-z]+", text)


def extract_keywords_simple(
    text: str,
    stopwords: set,
    min_len: int = 4,
    max_len: int = 20,
    use_nltk: bool = True,
) -> list:
    """
    简单关键词提取：与 Go 版本逻辑一致，仅保留长度在 [min_len, max_len] 的纯字母词并去停用词。
    """
    tokens = tokenize_with_nltk(text) if use_nltk else re.findall(r"[a-zA-Z]+", text.lower())
    seen = set()
    out = []
    for w in tokens:
        w = w.lower()
        if len(w) < min_len or len(w) > max_len or w in stopwords:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def extract_keywords_tfidf(
    documents: list,
    stopwords: set,
    top_k: int = 50,
    min_len: int = 4,
    max_len: int = 20,
) -> list:
    """
    基于 TF-IDF 提取每封邮件的 top-k 关键词（需 sklearn）。
    返回 list of list：每个文档对应一个关键词列表。
    """
    if not SKLEARN_AVAILABLE:
        return [extract_keywords_simple(doc, stopwords) for doc in documents]

    def tokenize(doc):
        tokens = tokenize_with_nltk(doc)
        return [t for t in tokens if min_len <= len(t) <= max_len and t not in stopwords]

    try:
        vectorizer = TfidfVectorizer(
            tokenizer=tokenize,
            token_pattern=None,
            lowercase=True,
            max_features=10000,
            stop_words=list(stopwords) if stopwords else None,
        )
        X = vectorizer.fit_transform(documents)
        terms = vectorizer.get_feature_names_out()
        result = []
        for i in range(X.shape[0]):
            row = X.getrow(i)
            scores = row.toarray().flatten()
            idx = scores.argsort()[::-1][:top_k]
            result.append([terms[j] for j in idx if scores[j] > 0])
        return result
    except Exception:
        return [extract_keywords_simple(doc, stopwords) for doc in documents]


def read_mail_content(file_path: Path, encodings: tuple = ("utf-8", "latin-1", "cp1252", "iso-8859-1")) -> str:
    """读取单封邮件内容，尝试多种编码"""
    for enc in encodings:
        try:
            return file_path.read_text(encoding=enc, errors="replace")
        except Exception:
            continue
    try:
        return file_path.read_bytes().decode("utf-8", errors="replace")
    except Exception:
        return ""


def walk_maildir(maildir_root: Path, max_writers: int | None = None):
    """
    遍历 maildir 目录结构：maildir/<user_folder>/<subfolder>/<files>
    与 extract_database.go 一致：每个一级子目录为一个用户（写作者）。
    yield: (user_index_1based, user_name, list of (file_id_1based, file_path_str, content))
    """
    if not maildir_root.is_dir():
        raise FileNotFoundError(f"Maildir not found: {maildir_root}")

    dirs = sorted([d for d in maildir_root.iterdir() if d.is_dir()])
    if max_writers is not None:
        dirs = dirs[: max_writers]

    for user_id_1based, user_dir in enumerate(dirs, start=1):
        user_name = user_dir.name
        files_with_content = []
        file_id = 0
        # 遍历子目录（如 inbox, sent_items 等）
        for sub in sorted(user_dir.iterdir()):
            if not sub.is_dir():
                continue
            for f in sorted(sub.iterdir()):
                if not f.is_file():
                    continue
                file_id += 1
                content = read_mail_content(f)
                # 相对路径：相对 maildir 的父目录（即 Hermes 项目根），与 app.py 中 PROJECT_ROOT / file_path 一致
                try:
                    rel_path = f.relative_to(maildir_root.parent)
                    path_str = str(rel_path).replace("\\", "/")
                except ValueError:
                    path_str = str(f).replace("\\", "/")
                files_with_content.append((file_id, path_str, content))
        yield user_id_1based, user_name, files_with_content


def run(
    maildir: str | Path,
    database_dir: str | Path,
    database_paths_dir: str | Path,
    max_writers: int | None = None,
    extractor: str = "simple",
    top_k_tfidf: int = 50,
    use_nltk: bool = True,
):
    maildir = Path(maildir).resolve()
    database_dir = Path(database_dir).resolve()
    database_paths_dir = Path(database_paths_dir).resolve()
    database_dir.mkdir(parents=True, exist_ok=True)
    database_paths_dir.mkdir(parents=True, exist_ok=True)

    if use_nltk:
        ensure_nltk_data()
    stopwords = DEFAULT_STOPWORDS.copy()
    if NLTK_AVAILABLE and use_nltk:
        try:
            from nltk.corpus import stopwords as nltk_sw
            stopwords |= set(nltk_sw.words("english"))
        except Exception:
            pass

    total_keywords = 0
    total_docs = 0
    for user_id_1based, user_name, files_with_content in walk_maildir(maildir, max_writers):
        keyword_to_file_ids = defaultdict(list)
        path_lines = []

        if extractor == "tfidf" and SKLEARN_AVAILABLE and files_with_content:
            documents = [c for (_, _, c) in files_with_content]
            doc_keywords = extract_keywords_tfidf(documents, stopwords, top_k=top_k_tfidf)
            for (fid, path_str, _), kws in zip(files_with_content, doc_keywords):
                path_lines.append((fid, path_str))
                for kw in kws:
                    keyword_to_file_ids[kw].append(fid)
        else:
            for file_id, path_str, content in files_with_content:
                path_lines.append((file_id, path_str))
                kws = extract_keywords_simple(content, stopwords, use_nltk=use_nltk)
                for kw in kws:
                    keyword_to_file_ids[kw].append(file_id)

        # 去重 file_id 列表（与 Go 一致）
        for kw in keyword_to_file_ids:
            keyword_to_file_ids[kw] = list(dict.fromkeys(keyword_to_file_ids[kw]))

        # 写入 database_paths/{userID}.txt
        paths_file = database_paths_dir / f"{user_id_1based}.txt"
        with open(paths_file, "w", encoding="utf-8") as pf:
            for fid, path_str in path_lines:
                pf.write(f"{fid} {path_str}\n")

        # 写入 database/{userID}.txt
        db_file = database_dir / f"{user_id_1based}.txt"
        with open(db_file, "w", encoding="utf-8") as df:
            for kw, fids in sorted(keyword_to_file_ids.items()):
                line = kw + " " + " ".join(str(i) for i in fids) + "\n"
                df.write(line)

        n_kw = len(keyword_to_file_ids)
        n_docs = len(files_with_content)
        total_keywords += n_kw
        total_docs += n_docs
        print(f"  Writer {user_id_1based} ({user_name}): {n_docs} docs, {n_kw} keywords -> {db_file.name}")

    print(f"\nDone. Total docs: {total_docs}, total keyword types: {total_keywords}")
    print(f"  database/       -> {database_dir}")
    print(f"  database_paths/ -> {database_paths_dir}")
    return total_docs, total_keywords


def main():
    parser = argparse.ArgumentParser(
        description="Enron maildir 预处理：提取关键词，生成 Hermes database 与 database_paths"
    )
    parser.add_argument("--maildir", default="./maildir", help="maildir 根目录（与 extract_database.go 一致）")
    parser.add_argument("--database-dir", default="./database", help="输出 database 目录")
    parser.add_argument("--database-paths-dir", default="./database_paths", help="输出 database_paths 目录")
    parser.add_argument("--max-writers", type=int, default=None, help="最多处理前 N 个写作者（默认全部）")
    parser.add_argument("--extractor", choices=("simple", "tfidf"), default="simple",
                        help="关键词提取方式: simple=与Go一致, tfidf=每封邮件TF-IDF top-k")
    parser.add_argument("--top-k", type=int, default=50, help="TF-IDF 时每封邮件保留的关键词数")
    parser.add_argument("--no-nltk", action="store_true", help="不使用 nltk 分词，仅用正则")
    args = parser.parse_args()

    if not Path(args.maildir).is_dir():
        print(f"Error: maildir not found: {args.maildir}", file=sys.stderr)
        sys.exit(1)

    run(
        maildir=args.maildir,
        database_dir=args.database_dir,
        database_paths_dir=args.database_paths_dir,
        max_writers=args.max_writers,
        extractor=args.extractor,
        top_k_tfidf=args.top_k,
        use_nltk=not args.no_nltk,
    )


if __name__ == "__main__":
    main()
