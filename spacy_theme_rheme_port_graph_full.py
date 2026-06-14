"""
Build a global Theme/Rheme Port Graph from text.

Pipeline:
1. spaCy parses text.
2. Each sentence becomes a main node.
3. spaCy heuristic extracts Theme/Rheme.
4. Theme/Rheme become ports of each sentence node.
5. All ports are compared globally.
6. Similar ports are connected.
7. Graph is rendered to PNG.

Install:
    pip install "spacy>=3.7,<4" networkx matplotlib
    python -m spacy download en_core_web_sm

Run:
    python spacy_theme_rheme_port_graph.py
"""

import argparse
import textwrap
from itertools import combinations
from pathlib import Path
from difflib import SequenceMatcher

import matplotlib.pyplot as plt
import networkx as nx
import spacy


DEFAULT_TEXT = """
Graph-based summarization helps organize long documents.
This method extracts entities and relations from the text.
The extracted graph supports better retrieval.
As a result, the summary becomes more coherent.
A coherent summary helps readers understand the main ideas quickly.
"""


def load_nlp(model_name: str):
    try:
        return spacy.load(model_name)
    except OSError:
        raise SystemExit(
            f"Cannot load spaCy model: {model_name}\n"
            f"Run: python -m spacy download {model_name}"
        )


def split_theme_rheme(sent):
    """
    Use spaCy only for Theme/Rheme splitting.

    Heuristic:
    1. If subject exists:
       Theme = full subject subtree.
       Rheme = remaining sentence.
    2. Else:
       Theme = tokens before ROOT.
       Rheme = ROOT and tokens after ROOT.
    """
    tokens = list(sent)

    subjects = [
        t for t in tokens
        if t.dep_ in ("nsubj", "nsubjpass", "expl")
    ]

    if subjects:
        subj = subjects[0]
        theme_span = sent.doc[subj.left_edge.i: subj.right_edge.i + 1]
        theme = theme_span.text.strip()
        rheme = sent.text.replace(theme, "", 1).strip()
        return theme, rheme

    roots = [t for t in tokens if t.dep_ == "ROOT"]

    if roots:
        root = roots[0]

        theme_tokens = [t for t in tokens if t.i < root.i]
        rheme_tokens = [t for t in tokens if t.i >= root.i]

        theme = " ".join(t.text for t in theme_tokens).strip()
        rheme = " ".join(t.text for t in rheme_tokens).strip()

        return theme, rheme

    return sent.text.strip(), ""


def string_sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def build_port_graph(text, nlp, sim_threshold=0.28, include_same_sentence=False):
    doc = nlp(text)
    sents = list(doc.sents)

    G = nx.Graph()
    all_ports = []

    for i, sent in enumerate(sents, 1):
        sid = f"S{i}"
        sent_text = sent.text.strip()

        theme, rheme = split_theme_rheme(sent)

        G.add_node(
            sid,
            kind="sentence",
            text=sent_text
        )

        port_map = {
            "Theme": theme,
            "Rheme": rheme
        }

        for port_name, port_text in port_map.items():
            if not port_text:
                continue

            pid = f"{sid}.{port_name}"

            G.add_node(
                pid,
                kind="port",
                owner=sid,
                port=port_name,
                text=port_text
            )

            G.add_edge(
                sid,
                pid,
                kind="has_port"
            )

            all_ports.append(pid)

    # Global all-port vs all-port matching
    for p1, p2 in combinations(all_ports, 2):
        if not include_same_sentence:
            if G.nodes[p1]["owner"] == G.nodes[p2]["owner"]:
                continue

        t1 = G.nodes[p1]["text"]
        t2 = G.nodes[p2]["text"]
        score = string_sim(t1, t2)

        if score >= sim_threshold:
            G.add_edge(
                p1,
                p2,
                kind="semantic_similarity",
                score=round(score, 3)
            )

    return G, sents


def print_theme_rheme(G, sents):
    print("THEME / RHEME")
    print("=" * 70)

    for i, sent in enumerate(sents, 1):
        sid = f"S{i}"
        print(f"{sid}: {sent.text.strip()}")

        theme_id = f"{sid}.Theme"
        rheme_id = f"{sid}.Rheme"

        if theme_id in G.nodes:
            print(f"  Theme: {G.nodes[theme_id]['text']}")
        if rheme_id in G.nodes:
            print(f"  Rheme : {G.nodes[rheme_id]['text']}")

        print()


def print_semantic_edges(G):
    print("GLOBAL PORT EDGES")
    print("=" * 70)

    for u, v, d in G.edges(data=True):
        if d["kind"] == "semantic_similarity":
            print(
                f"{u} <--> {v} | score={d['score']} | "
                f"{G.nodes[u]['text']} <--> {G.nodes[v]['text']}"
            )


def wrap_label(text, width=24):
    return "\n".join(textwrap.wrap(text, width=width))


def draw_graph(G, output_png):
    sentence_nodes = [
        n for n, d in G.nodes(data=True)
        if d["kind"] == "sentence"
    ]

    port_nodes = [
        n for n, d in G.nodes(data=True)
        if d["kind"] == "port"
    ]

    pos = {}
    x_gap = 5.7
    y_gap = 1.9

    for i, sid in enumerate(sentence_nodes):
        x = i * x_gap
        pos[sid] = (x, 0)

        owned_ports = [
            n for n in port_nodes
            if G.nodes[n]["owner"] == sid
        ]

        for pid in owned_ports:
            if G.nodes[pid]["port"] == "Theme":
                pos[pid] = (x, y_gap)
            else:
                pos[pid] = (x, -y_gap)

    labels = {}

    for n, d in G.nodes(data=True):
        if d["kind"] == "sentence":
            labels[n] = f"{n}\n{wrap_label(d['text'], 28)}"
        else:
            labels[n] = f"{d['port']}\n{wrap_label(d['text'], 24)}"

    has_port_edges = [
        (u, v) for u, v, d in G.edges(data=True)
        if d["kind"] == "has_port"
    ]

    semantic_edges = [
        (u, v) for u, v, d in G.edges(data=True)
        if d["kind"] == "semantic_similarity"
    ]

    edge_labels = {
        (u, v): str(d["score"])
        for u, v, d in G.edges(data=True)
        if d["kind"] == "semantic_similarity"
    }

    plt.figure(figsize=(19, 8))

    nx.draw_networkx_nodes(
        G,
        pos,
        nodelist=sentence_nodes,
        node_size=5200
    )

    nx.draw_networkx_nodes(
        G,
        pos,
        nodelist=port_nodes,
        node_size=4300
    )

    nx.draw_networkx_edges(
        G,
        pos,
        edgelist=has_port_edges,
        width=1.0
    )

    nx.draw_networkx_edges(
        G,
        pos,
        edgelist=semantic_edges,
        width=2.0,
        style="dashed"
    )

    nx.draw_networkx_labels(
        G,
        pos,
        labels=labels,
        font_size=7
    )

    nx.draw_networkx_edge_labels(
        G,
        pos,
        edge_labels=edge_labels,
        font_size=8
    )

    plt.title("spaCy Theme/Rheme Global Port Graph")
    plt.axis("off")
    plt.tight_layout()

    output_png = Path(output_png)
    plt.savefig(output_png, bbox_inches="tight", dpi=220)
    print(f"\nSaved PNG: {output_png.resolve()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str, default=DEFAULT_TEXT)
    parser.add_argument("--text-file", type=str, default=None)
    parser.add_argument("--model", type=str, default="en_core_web_md")
    parser.add_argument("--threshold", type=float, default=0.28)
    parser.add_argument("--output", type=str, default="spacy_theme_rheme_port_graph.png")
    parser.add_argument("--include-same-sentence", action="store_true")
    args = parser.parse_args()

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    else:
        text = args.text

    nlp = load_nlp(args.model)

    G, sents = build_port_graph(
        text=text,
        nlp=nlp,
        sim_threshold=args.threshold,
        include_same_sentence=args.include_same_sentence
    )

    print_theme_rheme(G, sents)
    print_semantic_edges(G)
    draw_graph(G, args.output)


if __name__ == "__main__":
    main()
