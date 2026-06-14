import spacy
import networkx as nx
from itertools import combinations

nlp = spacy.load("en_core_web_md")
G = nx.MultiDiGraph()

THRESHOLD = 0.72

text = """
Graph-based summarization helps organize long documents.
This method extracts entities and relations from the text.
The extracted graph supports better retrieval.
As a result, the summary becomes more coherent.
"""

doc = nlp(text)
sents = list(doc.sents)

def extract_ports(sent):
    ports = {}

    # Theme ~ subject
    subs = [t for t in sent if t.dep_ in ("nsubj", "nsubjpass")]
    if subs:
        s = subs[0]
        ports["Theme"] = sent.doc[s.left_edge.i:s.right_edge.i+1].text

    # Entities
    ents = [e.text for e in sent.ents]
    for i, e in enumerate(ents):
        ports[f"Entity_{i}"] = e

    # Root verb = Action
    roots = [t for t in sent if t.dep_ == "ROOT"]
    if roots:
        ports["Action"] = roots[0].lemma_

    # Full clause
    ports["Clause"] = sent.text.strip()

    return ports

# Build graph
all_ports = []

for i, sent in enumerate(sents):
    sid = f"S{i+1}"
    G.add_node(sid, kind="sentence", text=sent.text)

    ports = extract_ports(sent)

    for pname, ptext in ports.items():
        pid = (sid, pname)

        G.add_node(
            pid,
            kind="port",
            owner=sid,
            port=pname,
            text=ptext
        )

        G.add_edge(sid, pid, kind="has_port")
        all_ports.append(pid)

# GLOBAL ALL-PORT vs ALL-PORT
for p1, p2 in combinations(all_ports, 2):

    # bỏ cùng node nếu muốn
    if G.nodes[p1]["owner"] == G.nodes[p2]["owner"]:
        continue

    t1 = G.nodes[p1]["text"]
    t2 = G.nodes[p2]["text"]

    sim = nlp(t1).similarity(nlp(t2))

    if sim >= THRESHOLD:
        G.add_edge(
            p1, p2,
            kind="semantic_similarity",
            score=round(sim, 3)
        )

# show
for u, v, data in G.edges(data=True):
    if data["kind"] != "has_port":
        print(u, "->", v, data)